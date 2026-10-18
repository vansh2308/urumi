"""The Gemini adapter's wire format, against a mocked ``streamGenerateContent`` endpoint."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent.config import GeminiSettings, LlmSettings
from agent.errors import LlmRejectedError
from agent.llm.base import ChatMessage, FinishReason, LlmRequest, ToolCall, ToolSpec
from agent.llm.gemini import GeminiProvider
from agent.metrics import Metrics

API_KEY = "gemini-test-key"


def _sse(*chunks: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(c)}\r\n\r\n" for c in chunks).encode()


def _provider(handler: Any) -> GeminiProvider:
    return GeminiProvider(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        llm=LlmSettings(_env_file=None, model="gemini-test", max_retries=1),
        gemini=GeminiSettings(_env_file=None, api_key=API_KEY, base_url="https://gemini.test/v1"),
        metrics=Metrics(),
    )


def _request() -> LlmRequest:
    return LlmRequest(
        system="Be helpful.",
        messages=[
            ChatMessage(role="user", content="List products"),
            ChatMessage(
                role="assistant",
                tool_calls=(
                    ToolCall(
                        id="gm:abc",
                        name="shop__list_products",
                        arguments={"per_page": 1},
                        provider_metadata={"id": "abc", "thought_signature": "sig"},
                    ),
                ),
            ),
            ChatMessage(
                role="tool",
                tool_call_id="gm:abc",
                tool_name="shop__list_products",
                tool_result={"ok": True, "result": []},
            ),
        ],
        tools=[
            ToolSpec(name="shop__list_products", description="List", parameters={"type": "object"})
        ],
        max_output_tokens=256,
        temperature=0.2,
    )


async def test_request_translation_streaming_and_tool_call_parsing() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=_sse(
                {"candidates": [{"content": {"parts": [{"text": "Checking "}]}}]},
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "now."},
                                    {
                                        "functionCall": {
                                            "id": "xyz",
                                            "name": "shop__list_products",
                                            "args": {"per_page": 2},
                                        },
                                        "thoughtSignature": "sig2",
                                    },
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 7},
                },
            ),
            headers={"content-type": "text/event-stream"},
        )

    streamed: list[str] = []

    async def on_text(text: str) -> None:
        streamed.append(text)

    response = await _provider(handler).generate(_request(), on_text)

    request = seen[0]
    assert request.url.path == "/v1/models/gemini-test:streamGenerateContent"
    assert request.url.params["alt"] == "sse"
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)  # the key never appears in a URL (or its logs)
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "Be helpful."
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "shop__list_products"
    model_turn = body["contents"][1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0]["thoughtSignature"] == "sig"
    assert body["contents"][2]["parts"][0]["functionResponse"]["id"] == "abc"

    assert streamed == ["Checking ", "now."]
    assert response.text == "Checking now."
    assert response.finish_reason is FinishReason.TOOL_CALLS
    call = response.tool_calls[0]
    assert (call.id, call.name, call.arguments) == (
        "gm:xyz",
        "shop__list_products",
        {"per_page": 2},
    )
    assert call.provider_metadata == {"id": "xyz", "thought_signature": "sig2"}
    assert (response.input_tokens, response.output_tokens) == (40, 7)


async def test_rate_limit_is_retried_before_any_output() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})
        return httpx.Response(
            200,
            content=_sse(
                {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
            ),
        )

    async def ignore(_: str) -> None:
        return None

    response = await _provider(handler).generate(_request(), ignore)

    assert attempts == 2
    assert response.text == "ok"


async def test_bad_credentials_are_a_safe_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid."}}
        )

    async def ignore(_: str) -> None:
        return None

    with pytest.raises(LlmRejectedError) as raised:
        await _provider(handler).generate(_request(), ignore)
    assert API_KEY not in raised.value.public_message
