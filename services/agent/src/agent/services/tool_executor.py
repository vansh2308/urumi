"""Executes one model-requested tool call: name check, argument validation, gateway invocation,
and translation of every possible outcome into a truthful result for the model.

The model never decides where a call goes or under whose authority: the function name must be
one offered in this run, the arguments must satisfy the tool's input schema, and the call is
made with the caller's own API key, so the gateway enforces the caller's permissions. Failures
are returned to the model as ``{"ok": false, "error": {...}}`` so it can explain them; nothing
here ever reports success unless the gateway returned a successful ``CallToolResult``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent.config import RunSettings
from agent.errors import AuthenticationError
from agent.gateway.client import Caller, GatewayClient, GatewayToolResponse
from agent.llm.base import ToolCall
from agent.metrics import Metrics
from agent.services.toolbox import ToolBinding, Toolbox

_MAX_ERROR_DETAILS = 5
_MAX_ERROR_TEXT = 1000
# Upstream tool errors follow "<code>: <message>" (FastMCP prefixes "Error executing tool X: ").
_ERROR_CODE_RE = re.compile(r"^(?:Error executing tool [^:]{1,128}: )?([a-z][a-z0-9_]{1,63}):")


class ToolOutcome(StrEnum):
    SUCCESS = "success"
    TOOL_ERROR = "tool_error"  # the tool ran and reported an error
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_TOOL = "unknown_tool"  # the model named a tool it was not offered
    DENIED = "denied"  # the gateway refused (permissions changed, scope missing)
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"  # the MCP server failed or was unreachable
    GATEWAY_ERROR = "gateway_error"
    UNKNOWN = "unknown"  # timed out: it may or may not have been applied
    MALFORMED_RESULT = "malformed_result"
    SKIPPED = "skipped"  # not executed because a run limit was reached


@dataclass(frozen=True, slots=True)
class ToolExecution:
    call: ToolCall
    binding: ToolBinding | None
    outcome: ToolOutcome
    model_result: dict[str, Any]  # exactly what the model receives (and what is stored)
    http_status: int | None
    error_code: str | None
    latency_ms: int

    @property
    def ok(self) -> bool:
        return self.outcome is ToolOutcome.SUCCESS


class ToolExecutor:
    def __init__(self, gateway: GatewayClient, *, settings: RunSettings, metrics: Metrics) -> None:
        self._gateway = gateway
        self._settings = settings
        self._metrics = metrics

    def skipped(self, call: ToolCall, toolbox: Toolbox, reason: str) -> ToolExecution:
        """A call the run will not execute (limit reached). The model is told it did not run."""
        return self._failure(
            call, toolbox.get(call.name), ToolOutcome.SKIPPED, "not_executed", reason, 0
        )

    async def execute(self, caller: Caller, toolbox: Toolbox, call: ToolCall) -> ToolExecution:
        binding = toolbox.get(call.name)
        if binding is None:
            return self._finish(
                self._failure(
                    call,
                    None,
                    ToolOutcome.UNKNOWN_TOOL,
                    "unknown_tool",
                    f"No tool named '{call.name[:100]}' is available. Use only the tools provided.",
                    0,
                )
            )
        problems = _validate(binding, call.arguments)
        if problems:
            return self._finish(
                self._failure(
                    call,
                    binding,
                    ToolOutcome.INVALID_ARGUMENTS,
                    "invalid_arguments",
                    "The arguments do not match the tool's input schema; the tool was not called.",
                    0,
                    details=problems,
                )
            )

        started = time.perf_counter()
        response = await self._gateway.call_tool(
            caller, binding.connector, binding.tool, call.arguments
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.http_status == 401:
            # The key was revoked or expired mid-run: stop the run, do not let the model retry.
            raise AuthenticationError()
        return self._finish(self._from_response(call, binding, response, latency_ms))

    # --- translation ------------------------------------------------------------------------------

    def _from_response(
        self,
        call: ToolCall,
        binding: ToolBinding,
        response: GatewayToolResponse,
        latency_ms: int,
    ) -> ToolExecution:
        status = response.http_status
        if status == 200 and response.result is not None:
            return self._from_call_result(call, binding, response.result, latency_ms)
        code = response.error_code or "gateway_error"
        message = response.error_message or "The tool call failed."
        outcome = _outcome_for(status, code)
        return self._failure(
            call,
            binding,
            outcome,
            code,
            message,
            latency_ms,
            http_status=status,
            retryable=response.retryable,
        )

    def _from_call_result(
        self, call: ToolCall, binding: ToolBinding, result: dict[str, Any], latency_ms: int
    ) -> ToolExecution:
        content = result.get("content")
        if not isinstance(content, list):
            return self._failure(
                call,
                binding,
                ToolOutcome.MALFORMED_RESULT,
                "malformed_tool_result",
                "The tool returned a result in an unexpected format; treat the outcome as unknown.",
                latency_ms,
                http_status=200,
            )
        texts = [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        if result.get("isError") is True:
            text = "\n".join(texts).strip() or "The tool reported an error."
            match = _ERROR_CODE_RE.match(text)
            return self._failure(
                call,
                binding,
                ToolOutcome.TOOL_ERROR,
                match.group(1) if match else "tool_error",
                text[:_MAX_ERROR_TEXT],
                latency_ms,
                http_status=200,
            )
        data = _result_data(result, content, texts)
        return ToolExecution(
            call=call,
            binding=binding,
            outcome=ToolOutcome.SUCCESS,
            model_result=self._bounded({"ok": True, "result": data}),
            http_status=200,
            error_code=None,
            latency_ms=latency_ms,
        )

    def _failure(
        self,
        call: ToolCall,
        binding: ToolBinding | None,
        outcome: ToolOutcome,
        code: str,
        message: str,
        latency_ms: int,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        details: list[dict[str, str]] | None = None,
    ) -> ToolExecution:
        error: dict[str, Any] = {"code": code, "message": message}
        if retryable:
            error["retryable"] = True
        if details:
            error["details"] = details
        result: dict[str, Any] = {"ok": False, "error": error}
        if outcome is ToolOutcome.UNKNOWN:
            result["outcome_unknown"] = True
        return ToolExecution(
            call=call,
            binding=binding,
            outcome=outcome,
            model_result=self._bounded(result),
            http_status=http_status,
            error_code=code[:64],
            latency_ms=latency_ms,
        )

    def _bounded(self, result: dict[str, Any]) -> dict[str, Any]:
        """Cap what the model sees (and what is stored) at ``max_tool_result_chars``."""
        limit = self._settings.max_tool_result_chars
        encoded = json.dumps(result, ensure_ascii=False, default=str)
        if len(encoded) <= limit:
            return result
        payload = result.get("result", result.get("error"))
        preview = json.dumps(payload, ensure_ascii=False, default=str)[:limit]
        bounded = {k: v for k, v in result.items() if k not in {"result", "error"}}
        bounded["truncated"] = True
        bounded["result_preview" if result.get("ok") else "error_preview"] = preview
        return bounded

    def _finish(self, execution: ToolExecution) -> ToolExecution:
        binding = execution.binding
        # Only offered (gateway-returned) names become label values, keeping cardinality bounded.
        connector = binding.connector if binding else "unknown"
        tool = binding.tool if binding else "unknown"
        self._metrics.tool_calls.labels(
            connector=connector, tool=tool, outcome=execution.outcome.value
        ).inc()
        if execution.latency_ms:
            self._metrics.tool_latency.labels(connector=connector).observe(
                execution.latency_ms / 1000
            )
        return execution


def _validate(binding: ToolBinding, arguments: Any) -> list[dict[str, str]]:
    if not isinstance(arguments, dict):
        return [{"path": "$", "message": "arguments must be a JSON object"}]
    errors = sorted(binding.validator.iter_errors(arguments), key=lambda e: list(e.path))
    return [
        {
            "path": "$" + "".join(f"[{p!r}]" if isinstance(p, int) else f".{p}" for p in e.path),
            "message": e.message[:300],
        }
        for e in errors[:_MAX_ERROR_DETAILS]
    ]


_OUTCOME_BY_STATUS = {
    200: ToolOutcome.MALFORMED_RESULT,  # a 200 without a usable result
    403: ToolOutcome.DENIED,
    404: ToolOutcome.NOT_FOUND,
    422: ToolOutcome.INVALID_ARGUMENTS,
    429: ToolOutcome.RATE_LIMITED,
}


def _outcome_for(status: int | None, code: str) -> ToolOutcome:
    if code in {"gateway_timeout", "upstream_timeout"}:
        return ToolOutcome.UNKNOWN  # the call may have been applied
    if status is not None and status in _OUTCOME_BY_STATUS:
        return _OUTCOME_BY_STATUS[status]
    if code.startswith("upstream_"):
        return ToolOutcome.UPSTREAM_ERROR
    return ToolOutcome.GATEWAY_ERROR


def _result_data(result: dict[str, Any], content: list[Any], texts: list[str]) -> Any:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    other = [
        {"type": b.get("type")} for b in content if isinstance(b, dict) and b.get("type") != "text"
    ]
    return {"text": texts, "other_content": other} if other else texts
