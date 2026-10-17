"""The chat endpoint: one message in, one agent run out, streamed or as a single JSON body.

Everything that can be refused (authentication, validation, rate limit, conversation ownership,
a run already active in the conversation) is decided before the response starts, so those
answer with a normal HTTP error. Once a run starts, its progress and any failure are reported
as events.

The run executes in its own task and the response only reads its events from a queue, so the
run's deadline never cancels the response writer, and a client that disconnects cancels the run
(which still records itself as ``cancelled`` and releases the conversation).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse

from agent.api.deps import API_PREFIX, CallerDep, ContainerDep
from agent.api.schemas import ChatRequest, ChatResponse, ToolCallTrace
from agent.container import Container
from agent.context import current_request_id
from agent.gateway.client import Caller
from agent.services.conversations import RunHandle

router = APIRouter(prefix=API_PREFIX, tags=["chat"])

_HEARTBEAT_SECONDS = 15.0
_FAILED_STATUS_CODES = {"timeout": 504}
Event = tuple[str, dict[str, Any]]


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        200: {
            "description": "With stream=true a text/event-stream of run events, otherwise JSON.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def chat(body: ChatRequest, caller: CallerDep, container: ContainerDep) -> Any:
    """Send a message to the agent. The agent discovers this caller's tools through the
    gateway, lets the model call them (through the gateway, as this caller) and answers."""
    handle = await container.runner.start(caller, body.conversation_id, body.message)
    if body.stream:
        return StreamingResponse(
            _event_stream(container, caller, handle),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    events: list[Event] = []

    async def collect(event_type: str, data: dict[str, Any]) -> None:
        events.append((event_type, data))

    await container.runner.execute(caller, handle, collect)
    response = _aggregate(handle, events)
    status_code = 200
    if response.error is not None:
        status_code = _FAILED_STATUS_CODES.get(response.status, 502)
    return JSONResponse(response.model_dump(mode="json"), status_code=status_code)


async def _event_stream(
    container: Container, caller: Caller, handle: RunHandle
) -> AsyncIterator[str]:
    queue: asyncio.Queue[Event | None] = asyncio.Queue()

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        queue.put_nowait((event_type, data))

    task = asyncio.create_task(
        container.runner.execute(caller, handle, emit), name=f"run-{handle.run_id}"
    )
    container.run_tasks.add(task)
    task.add_done_callback(container.run_tasks.discard)
    task.add_done_callback(lambda _: queue.put_nowait(None))
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": keep-alive\n\n"  # SSE comment; keeps idle proxies from closing
                continue
            if item is None:
                break
            yield _sse(*item)
    finally:
        # Client gone (or stream finished): stop the run. It finalises itself; awaiting it here
        # would be cancelled again by the server's cancel scope.
        if not task.done():
            task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task  # surfaces unexpected failures in the logs; normally already finished


def _sse(event_type: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload}\n\n"


def _aggregate(handle: RunHandle, events: list[Event]) -> ChatResponse:
    tools: list[str] = []
    unavailable: list[str] = []
    model_calls: list[dict[str, Any]] = []
    calls: dict[str, ToolCallTrace] = {}
    message: str | None = None
    error: dict[str, Any] | None = None
    completion: dict[str, Any] = {}
    for event_type, data in events:
        if event_type == "tools.discovered":
            tools = [t["name"] for t in data["tools"]]
            unavailable = list(data["unavailable_connectors"])
        elif event_type == "llm.response":
            model_calls.append(data)
        elif event_type == "tool.call":
            calls[data["call_id"]] = ToolCallTrace(**data)
        elif event_type == "tool.result" and data["call_id"] in calls:
            trace = calls[data["call_id"]]
            for field in ("outcome", "ok", "http_status", "error_code", "latency_ms", "result"):
                setattr(trace, field, data[field])
        elif event_type == "message.completed":
            message = data["text"]
        elif event_type == "error":
            error = data
        elif event_type == "run.completed":
            completion = data
    return ChatResponse(
        run_id=handle.run_id,
        conversation_id=handle.conversation_id,
        status=completion.get("status", "failed"),
        message=message,
        tools_available=tools,
        unavailable_connectors=unavailable,
        model_calls=model_calls,
        tool_calls=list(calls.values()),
        usage=completion.get("usage", {}),
        latency_ms=completion.get("latency_ms"),
        first_token_ms=completion.get("first_token_ms"),
        error=error,
        request_id=current_request_id(),
    )
