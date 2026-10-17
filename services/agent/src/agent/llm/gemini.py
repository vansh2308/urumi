"""Gemini adapter over the REST API (``streamGenerateContent`` with server-sent events).

Uses plain HTTP so timeouts, retries and connection pooling stay under the service's control and
no SDK is needed. Tool schemas are passed as ``parametersJsonSchema`` (full JSON Schema), so
MCP input schemas need no translation.

Gemini 2.5+ models attach a ``thoughtSignature`` to function-call parts; it must be sent back
with that call in later requests, so it travels in :attr:`ToolCall.provider_metadata`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Sequence
from typing import Any

import httpx

from agent.config import GeminiSettings, LlmSettings
from agent.errors import (
    LlmError,
    LlmRateLimitedError,
    LlmRejectedError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from agent.llm.base import (
    ChatMessage,
    FinishReason,
    LlmRequest,
    LlmResponse,
    TextSink,
    ToolCall,
)
from agent.metrics import Metrics

logger = logging.getLogger(__name__)

_FINISH_REASONS = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.MAX_TOKENS,
    "SAFETY": FinishReason.BLOCKED,
    "RECITATION": FinishReason.BLOCKED,
    "BLOCKLIST": FinishReason.BLOCKED,
    "PROHIBITED_CONTENT": FinishReason.BLOCKED,
    "SPII": FinishReason.BLOCKED,
    "IMAGE_SAFETY": FinishReason.BLOCKED,
    "MALFORMED_FUNCTION_CALL": FinishReason.MALFORMED_TOOL_CALL,
    "UNEXPECTED_TOOL_CALL": FinishReason.MALFORMED_TOOL_CALL,
}
_MAX_LOGGED_ERROR_CHARS = 300
_MAX_ERROR_BODY_BYTES = 16 * 1024


def build_llm_http_client(settings: LlmSettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_seconds, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        follow_redirects=False,
    )


class GeminiProvider:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        llm: LlmSettings,
        gemini: GeminiSettings,
        metrics: Metrics,
    ) -> None:
        if gemini.api_key is None:
            raise ValueError("GEMINI_API_KEY is not set")
        self._http = http
        self._llm = llm
        self._api_key = gemini.api_key
        self._url = f"{gemini.base_url.rstrip('/')}/models/{llm.model}:streamGenerateContent"
        self._metrics = metrics

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._llm.model

    async def generate(self, request: LlmRequest, on_text: TextSink) -> LlmResponse:
        body = _request_body(request)
        attempt = 0
        while True:
            started = time.perf_counter()
            # Retrying is only safe while the caller has seen nothing of this response.
            streamed = False

            async def forward(text: str) -> None:
                nonlocal streamed
                streamed = True
                await on_text(text)

            outcome = "error"
            try:
                async with asyncio.timeout(self._llm.request_timeout_seconds):
                    response = await self._stream_once(body, forward)
                outcome = "success"
                self._record_tokens(response)
                return response
            except TimeoutError as exc:
                outcome = "timeout"
                raise LlmTimeoutError("The model did not answer in time.") from exc
            except LlmError as exc:
                outcome = exc.code
                if exc.retryable and not streamed and attempt < self._llm.max_retries:
                    attempt += 1
                    delay = min(0.5 * 2**attempt, 4.0) + random.uniform(0, 0.25)  # noqa: S311
                    logger.info("retrying model call", extra={"attempt": attempt, "code": exc.code})
                    await asyncio.sleep(delay)
                    continue
                raise
            finally:
                self._metrics.llm_requests.labels(
                    provider=self.name, model=self.model, outcome=outcome
                ).inc()
                self._metrics.llm_latency.labels(provider=self.name, model=self.model).observe(
                    time.perf_counter() - started
                )

    async def _stream_once(self, body: dict[str, Any], on_text: TextSink) -> LlmResponse:
        accumulator = _ResponseAccumulator(on_text)
        try:
            async with self._http.stream(
                "POST",
                self._url,
                params={"alt": "sse"},
                json=body,
                headers={"x-goog-api-key": self._api_key.get_secret_value()},
            ) as response:
                if response.status_code != 200:
                    raise await self._http_error(response)
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        await accumulator.feed(_parse_chunk(line[5:].strip()))
        except httpx.TimeoutException as exc:
            raise LlmTimeoutError("The model did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise LlmUnavailableError("The model provider could not be reached.") from exc
        return accumulator.result()

    async def _http_error(self, response: httpx.Response) -> LlmError:
        raw = b""
        async for piece in response.aiter_bytes():
            raw += piece
            if len(raw) >= _MAX_ERROR_BODY_BYTES:
                break
        status_text, message = _error_details(raw)
        # Provider messages describe the request shape, never our key; still bounded.
        logger.warning(
            "model provider error",
            extra={
                "provider": self.name,
                "http_status": response.status_code,
                "provider_status": status_text,
                "provider_message": message[:_MAX_LOGGED_ERROR_CHARS],
            },
        )
        code = response.status_code
        if code == 429:
            return LlmRateLimitedError("The model provider is rate limiting requests.")
        if code in {500, 502, 503}:
            return LlmUnavailableError("The model provider is temporarily unavailable.")
        if code == 504:
            return LlmTimeoutError("The model did not answer in time.")
        if code in {401, 403}:
            return LlmRejectedError("The model provider rejected the service's credentials.")
        if code == 404:
            return LlmRejectedError("The configured model is not available.")
        return LlmRejectedError("The model provider rejected the request.")

    def _record_tokens(self, response: LlmResponse) -> None:
        tokens = self._metrics.llm_tokens
        tokens.labels(provider=self.name, model=self.model, direction="input").inc(
            response.input_tokens
        )
        tokens.labels(provider=self.name, model=self.model, direction="output").inc(
            response.output_tokens
        )


# --- request translation ------------------------------------------------------------------------


def _request_body(request: LlmRequest) -> dict[str, Any]:
    body: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": request.system}]},
        "contents": _contents(request.messages),
        "generationConfig": {
            "maxOutputTokens": request.max_output_tokens,
            "temperature": request.temperature,
        },
    }
    if request.tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parametersJsonSchema": tool.parameters,
                    }
                    for tool in request.tools
                ]
            }
        ]
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
    return body


def _contents(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    pending_responses: list[dict[str, Any]] = []

    def flush_responses() -> None:
        if pending_responses:
            contents.append({"role": "user", "parts": list(pending_responses)})
            pending_responses.clear()

    for message in messages:
        if message.role == "tool":
            response: dict[str, Any] = {
                "name": message.tool_name or "",
                "response": message.tool_result or {},
            }
            provider_id = _provider_call_id(message.tool_call_id)
            if provider_id:
                response["id"] = provider_id
            pending_responses.append({"functionResponse": response})
            continue
        flush_responses()
        if message.role == "user":
            contents.append({"role": "user", "parts": [{"text": message.content or ""}]})
            continue
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for call in message.tool_calls:
            function_call: dict[str, Any] = {"name": call.name, "args": call.arguments}
            if call.provider_metadata.get("id"):
                function_call["id"] = call.provider_metadata["id"]
            part: dict[str, Any] = {"functionCall": function_call}
            if call.provider_metadata.get("thought_signature"):
                part["thoughtSignature"] = call.provider_metadata["thought_signature"]
            parts.append(part)
        if parts:
            contents.append({"role": "model", "parts": parts})
    flush_responses()
    return contents


def _provider_call_id(call_id: str | None) -> str | None:
    """Our call ids embed Gemini's own id (when it sent one) after ``gm:``."""
    if call_id and call_id.startswith("gm:"):
        return call_id[3:]
    return None


# --- response parsing ---------------------------------------------------------------------------


class _ResponseAccumulator:
    """Folds streamed chunks into one response, forwarding answer text as it arrives."""

    def __init__(self, on_text: TextSink) -> None:
        self._on_text = on_text
        self._text: list[str] = []
        self._calls: list[ToolCall] = []
        self._finish = FinishReason.OTHER
        self._input_tokens = 0
        self._output_tokens = 0

    async def feed(self, chunk: dict[str, Any]) -> None:
        _raise_if_prompt_blocked(chunk)
        usage = chunk.get("usageMetadata")
        if isinstance(usage, dict):  # cumulative; the last chunk carries the totals
            self._input_tokens = _int(usage.get("promptTokenCount"))
            self._output_tokens = _int(usage.get("candidatesTokenCount")) + _int(
                usage.get("thoughtsTokenCount")
            )
        candidates = chunk.get("candidates")
        candidate = candidates[0] if isinstance(candidates, list) and candidates else None
        if not isinstance(candidate, dict):
            return
        for part in _parts(candidate):
            if isinstance(part.get("functionCall"), dict):
                self._calls.append(_tool_call(part))
            elif isinstance(part.get("text"), str) and not part.get("thought"):
                self._text.append(part["text"])
                await self._on_text(part["text"])
        reason = candidate.get("finishReason")
        if isinstance(reason, str):
            self._finish = _FINISH_REASONS.get(reason, FinishReason.OTHER)

    def result(self) -> LlmResponse:
        finish = self._finish
        if self._calls and finish in {FinishReason.STOP, FinishReason.OTHER}:
            finish = FinishReason.TOOL_CALLS
        return LlmResponse(
            text="".join(self._text),
            tool_calls=tuple(self._calls),
            finish_reason=finish,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


def _parse_chunk(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except ValueError as exc:
        raise LlmUnavailableError("The model provider sent an unreadable response.") from exc
    if not isinstance(chunk, dict):
        raise LlmUnavailableError("The model provider sent an unreadable response.")
    return chunk


def _raise_if_prompt_blocked(chunk: dict[str, Any]) -> None:
    feedback = chunk.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        logger.warning("prompt blocked by provider", extra={"reason": feedback["blockReason"]})
        raise LlmRejectedError("The model provider blocked this request.")


def _parts(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return []
    return [p for p in parts if isinstance(p, dict)]


def _tool_call(part: dict[str, Any]) -> ToolCall:
    function_call = part["functionCall"]
    provider_id = function_call.get("id")
    metadata: dict[str, Any] = {}
    if isinstance(provider_id, str) and provider_id:
        metadata["id"] = provider_id
        call_id = f"gm:{provider_id}"
    else:
        call_id = f"call_{uuid.uuid4().hex[:24]}"
    if isinstance(part.get("thoughtSignature"), str):
        metadata["thought_signature"] = part["thoughtSignature"]
    args = function_call.get("args")
    return ToolCall(
        id=call_id[:128],
        name=str(function_call.get("name") or ""),
        arguments={} if args is None else args,
        provider_metadata=metadata,
    )


def _error_details(raw: bytes) -> tuple[str, str]:
    try:
        body = json.loads(raw)
    except ValueError:
        return "", ""
    if isinstance(body, list) and body:
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", ""
    return str(error.get("status") or ""), str(error.get("message") or "")


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0
