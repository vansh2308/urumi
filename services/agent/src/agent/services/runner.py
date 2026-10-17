"""The agent loop: one model with native tool calling, bounded in steps, tool calls, tokens and
wall clock.

A run is one chat request. :meth:`AgentRunner.start` does everything that can be refused with an
HTTP status (validation, rate limit, conversation ownership and lock). :meth:`AgentRunner.execute`
then runs the loop and reports progress through an event sink; it never raises application
errors: they are emitted as an ``error`` event and recorded on the run, so the streamed and the
non-streamed API behave the same. The run row and the conversation lock are always finalised,
also when the client disconnects.

Events (``type`` and data):

* ``run.started``      run and conversation ids, provider and model
* ``tools.discovered`` the tools offered to the model for this caller
* ``llm.request``      a model call starts (step number, context size)
* ``message.delta``    answer text as it streams
* ``llm.response``     a model call finished (finish reason, tokens, latency, tool calls)
* ``tool.call``        the model chose a tool, with its arguments
* ``tool.result``      what happened (outcome, HTTP status, latency) and what the model sees
* ``message.completed`` the final answer
* ``error``            why the run stopped early
* ``run.completed``    final status, step and token counts, latencies
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from agent.config import LlmSettings, RunSettings
from agent.context import current_request_id
from agent.db.models import RunStatus
from agent.errors import (
    AppError,
    InvalidInputError,
    LlmError,
    LlmRejectedError,
    PersistenceUnavailableError,
    RunTimeoutError,
)
from agent.gateway.client import Caller
from agent.llm.base import ChatMessage, FinishReason, LlmProvider, LlmRequest, LlmResponse
from agent.metrics import Metrics
from agent.services.conversations import ConversationStore, RunHandle, RunStats
from agent.services.prompt import system_prompt
from agent.services.tool_executor import ToolExecution, ToolExecutor
from agent.services.toolbox import Toolbox, ToolDiscovery

logger = logging.getLogger(__name__)

EventSink = Callable[[str, dict[str, Any]], Awaitable[None]]

_LIMIT_MESSAGES = {
    RunStatus.STEP_LIMIT: "I stopped before finishing because this request reached the maximum "
    "number of steps. Some of the actions above may have completed; check their results before "
    "asking me to retry.",
    RunStatus.TOOL_LIMIT: "I stopped before finishing because this request reached the maximum "
    "number of tool calls. Some of the actions above may have completed; check their results "
    "before asking me to retry.",
    RunStatus.TOKEN_LIMIT: "I stopped before finishing because this request used its token "
    "budget. Some of the actions above may have completed; check their results before asking "
    "me to retry.",
}
_TRUNCATED_NOTE = "\n\n[The answer was cut off at the output length limit.]"


class MalformedToolCallError(LlmError):
    code = "llm_malformed_tool_call"


class EmptyAnswerError(LlmError):
    code = "llm_empty_answer"


class AgentRunner:
    def __init__(
        self,
        *,
        store: ConversationStore,
        discovery: ToolDiscovery,
        executor: ToolExecutor,
        llm: LlmProvider,
        settings: RunSettings,
        llm_settings: LlmSettings,
        metrics: Metrics,
    ) -> None:
        self._store = store
        self._discovery = discovery
        self._executor = executor
        self._llm = llm
        self._settings = settings
        self._llm_settings = llm_settings
        self._metrics = metrics

    async def start(self, caller: Caller, conversation_id: Any, message: str) -> RunHandle:
        text = message.strip()
        if not text:
            raise InvalidInputError("The message is empty.")
        if len(text) > self._settings.max_message_chars:
            raise InvalidInputError(
                f"The message is longer than {self._settings.max_message_chars} characters."
            )
        return await self._store.start_run(
            caller.identity,
            conversation_id=conversation_id,
            text=text,
            provider=self._llm.name,
            model=self._llm.model,
            request_id=current_request_id(),
        )

    async def execute(self, caller: Caller, handle: RunHandle, emit: EventSink) -> None:
        started = time.perf_counter()
        stats = RunStats()
        await emit(
            "run.started",
            {
                "run_id": str(handle.run_id),
                "conversation_id": str(handle.conversation_id),
                "conversation_created": handle.conversation_created,
                "provider": self._llm.name,
                "model": self._llm.model,
                "request_id": current_request_id(),
            },
        )
        try:
            try:
                async with asyncio.timeout(self._settings.timeout_seconds):
                    await self._loop(caller, handle, emit, stats, started)
            except TimeoutError as exc:
                raise RunTimeoutError(
                    f"The request did not finish within {self._settings.timeout_seconds:g} seconds."
                ) from exc
        except AppError as exc:
            stats.status = RunStatus.TIMEOUT if exc.code == "run_timeout" else RunStatus.FAILED
            stats.error_code = exc.code
            await emit(
                "error",
                {
                    "code": exc.code,
                    "message": exc.public_message,
                    "retryable": exc.retryable,
                    "request_id": current_request_id(),
                },
            )
        except asyncio.CancelledError:
            stats.status = RunStatus.CANCELLED
            raise
        except Exception:
            logger.exception("run failed unexpectedly")
            stats.status = RunStatus.FAILED
            stats.error_code = "internal_error"
            await emit(
                "error",
                {
                    "code": "internal_error",
                    "message": "Unexpected server error.",
                    "retryable": False,
                    "request_id": current_request_id(),
                },
            )
        finally:
            stats.latency_ms = round((time.perf_counter() - started) * 1000)
            # Shielded so a client disconnect cannot leave the run "running" or the lock held.
            await asyncio.shield(self._finalise(handle, stats))
        await emit("run.completed", _completion(handle, stats))

    # --- loop -------------------------------------------------------------------------------------

    async def _loop(
        self,
        caller: Caller,
        handle: RunHandle,
        emit: EventSink,
        stats: RunStats,
        started: float,
    ) -> None:
        toolbox = await self._discovery.discover(caller)
        stats.tools_available = len(toolbox.bindings)
        await emit("tools.discovered", _tools_event(toolbox))

        messages = await self._store.load_history(handle.conversation_id)
        specs = toolbox.specs
        system = system_prompt()
        malformed_retry_used = False
        step = 0
        while step < self._settings.max_steps:
            step += 1
            stats.steps = step
            if stats.input_tokens + stats.output_tokens >= self._settings.max_tokens:
                await self._stop(handle, emit, stats, RunStatus.TOKEN_LIMIT)
                return
            response = await self._call_model(emit, stats, started, step, system, messages, specs)

            if not response.tool_calls:
                if response.finish_reason is FinishReason.MALFORMED_TOOL_CALL:
                    if malformed_retry_used:
                        raise MalformedToolCallError("The model produced an invalid tool call.")
                    malformed_retry_used = True
                    continue
                await self._final_answer(handle, emit, stats, step, response)
                return

            await self._store.add_assistant_message(handle, response.text, response.tool_calls)
            messages.append(
                ChatMessage(
                    role="assistant", content=response.text or None, tool_calls=response.tool_calls
                )
            )
            results, limit_hit = await self._run_tools(caller, toolbox, emit, stats, step, response)
            await self._store.add_tool_results(handle, caller.identity, step, results)
            messages.extend(
                ChatMessage(
                    role="tool",
                    tool_call_id=r.call.id,
                    tool_name=r.call.name,
                    tool_result=r.model_result,
                )
                for r in results
            )
            if limit_hit:
                await self._stop(handle, emit, stats, RunStatus.TOOL_LIMIT)
                return
        await self._stop(handle, emit, stats, RunStatus.STEP_LIMIT)

    async def _call_model(
        self,
        emit: EventSink,
        stats: RunStats,
        started: float,
        step: int,
        system: str,
        messages: list[ChatMessage],
        specs: list[Any],
    ) -> LlmResponse:
        await emit(
            "llm.request",
            {"step": step, "context_messages": len(messages), "tools_offered": len(specs)},
        )

        async def on_text(delta: str) -> None:
            if stats.first_token_ms is None:
                stats.first_token_ms = round((time.perf_counter() - started) * 1000)
                self._metrics.first_token_latency.observe(stats.first_token_ms / 1000)
            await emit("message.delta", {"step": step, "text": delta})

        llm_started = time.perf_counter()
        response = await self._llm.generate(
            LlmRequest(
                system=system,
                messages=messages,
                tools=specs,
                max_output_tokens=self._llm_settings.max_output_tokens,
                temperature=self._llm_settings.temperature,
            ),
            on_text,
        )
        latency_ms = round((time.perf_counter() - llm_started) * 1000)
        stats.llm_latency_ms += latency_ms
        stats.input_tokens += response.input_tokens
        stats.output_tokens += response.output_tokens
        await emit(
            "llm.response",
            {
                "step": step,
                "finish_reason": response.finish_reason.value,
                "tool_calls": [c.name for c in response.tool_calls],
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": latency_ms,
            },
        )
        if response.finish_reason is FinishReason.BLOCKED and not response.tool_calls:
            raise LlmRejectedError("The model provider declined to answer this request.")
        return response

    async def _run_tools(
        self,
        caller: Caller,
        toolbox: Toolbox,
        emit: EventSink,
        stats: RunStats,
        step: int,
        response: LlmResponse,
    ) -> tuple[list[ToolExecution], bool]:
        """Execute the requested calls one after another (later calls may depend on earlier
        effects). Every call gets a result, executed or not, so the context stays consistent."""
        results: list[ToolExecution] = []
        limit_hit = False
        for index, call in enumerate(response.tool_calls):
            binding = toolbox.get(call.name)
            await emit(
                "tool.call",
                {
                    "step": step,
                    "call_id": call.id,
                    "name": call.name,
                    "connector": binding.connector if binding else None,
                    "tool": binding.tool if binding else None,
                    "arguments": call.arguments,
                },
            )
            if index >= self._settings.max_parallel_tool_calls:
                execution = self._executor.skipped(
                    call, toolbox, "Too many tool calls in one response; request fewer at once."
                )
            elif stats.tool_calls >= self._settings.max_tool_calls:
                limit_hit = True
                execution = self._executor.skipped(
                    call, toolbox, "The tool call limit for this request was reached."
                )
            else:
                stats.tool_calls += 1
                execution = await self._executor.execute(caller, toolbox, call)
                stats.tool_latency_ms += execution.latency_ms
            results.append(execution)
            await emit("tool.result", _tool_result_event(step, execution))
        return results, limit_hit

    async def _final_answer(
        self,
        handle: RunHandle,
        emit: EventSink,
        stats: RunStats,
        step: int,
        response: LlmResponse,
    ) -> None:
        text = response.text.strip()
        if not text:
            raise EmptyAnswerError("The model returned an empty answer.")
        if response.finish_reason is FinishReason.MAX_TOKENS:
            text += _TRUNCATED_NOTE
            await emit("message.delta", {"step": step, "text": _TRUNCATED_NOTE})
        message_id = await self._store.add_assistant_message(handle, text)
        stats.status = RunStatus.COMPLETED
        await emit("message.completed", {"message_id": str(message_id), "text": text})

    async def _stop(
        self, handle: RunHandle, emit: EventSink, stats: RunStats, status: RunStatus
    ) -> None:
        """End at a limit with an honest, fixed message (not model-generated)."""
        text = _LIMIT_MESSAGES[status]
        stats.status = status
        stats.error_code = status.value
        message_id = await self._store.add_assistant_message(handle, text)
        await emit("message.delta", {"step": stats.steps, "text": text})
        await emit("message.completed", {"message_id": str(message_id), "text": text})

    async def _finalise(self, handle: RunHandle, stats: RunStats) -> None:
        if stats.status is RunStatus.RUNNING:  # loop ended without setting a status
            stats.status = RunStatus.FAILED
            stats.error_code = stats.error_code or "internal_error"
        try:
            await self._store.finish_run(handle, stats)
        except PersistenceUnavailableError:
            logger.warning("run outcome not persisted; the conversation lock will expire")
        self._metrics.runs.labels(status=stats.status.value).inc()
        if stats.latency_ms is not None:
            self._metrics.run_latency.observe(stats.latency_ms / 1000)
        logger.info(
            "run finished",
            extra={
                "run_id": str(handle.run_id),
                "conversation_id": str(handle.conversation_id),
                "status": stats.status.value,
                "error_code": stats.error_code,
                "steps": stats.steps,
                "tool_calls": stats.tool_calls,
                "input_tokens": stats.input_tokens,
                "output_tokens": stats.output_tokens,
                "latency_ms": stats.latency_ms,
                "first_token_ms": stats.first_token_ms,
            },
        )


def _tools_event(toolbox: Toolbox) -> dict[str, Any]:
    return {
        "count": len(toolbox.bindings),
        "tools": [
            {
                "name": b.function_name,
                "connector": b.connector,
                "tool": b.tool,
                "description": b.description,
            }
            for b in toolbox.bindings.values()
        ],
        "unavailable_connectors": list(toolbox.unavailable_connectors),
    }


def _tool_result_event(step: int, execution: ToolExecution) -> dict[str, Any]:
    return {
        "step": step,
        "call_id": execution.call.id,
        "name": execution.call.name,
        "outcome": execution.outcome.value,
        "ok": execution.ok,
        "http_status": execution.http_status,
        "error_code": execution.error_code,
        "latency_ms": execution.latency_ms,
        "result": execution.model_result,
    }


def _completion(handle: RunHandle, stats: RunStats) -> dict[str, Any]:
    return {
        "run_id": str(handle.run_id),
        "conversation_id": str(handle.conversation_id),
        "status": stats.status.value,
        "error_code": stats.error_code,
        "steps": stats.steps,
        "tool_calls": stats.tool_calls,
        "tools_available": stats.tools_available,
        "usage": {"input_tokens": stats.input_tokens, "output_tokens": stats.output_tokens},
        "latency_ms": stats.latency_ms,
        "first_token_ms": stats.first_token_ms,
        "llm_latency_ms": stats.llm_latency_ms,
        "tool_latency_ms": stats.tool_latency_ms,
    }
