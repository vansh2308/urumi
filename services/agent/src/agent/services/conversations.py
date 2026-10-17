"""Conversation persistence: ownership, the per-conversation run lock, context history, run
records and tool call logs.

Every read and write is scoped by ``(team_id, user_id)`` taken from the gateway-verified
identity, never from the request body, so a conversation id from another user or team behaves
exactly like one that does not exist (404).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update

from agent.config import RunSettings
from agent.db.engine import Database
from agent.db.models import (
    AgentRun,
    Conversation,
    Message,
    MessageRole,
    RunStatus,
    ToolCallLog,
)
from agent.errors import ConflictError, NotFoundError, RateLimitedError
from agent.gateway.client import Identity
from agent.llm.base import ChatMessage, ToolCall
from agent.metrics import Metrics
from agent.services.tool_executor import ToolExecution

_TITLE_CHARS = 80
_INTERRUPTED_RESULT: dict[str, Any] = {
    "ok": False,
    "error": {
        "code": "interrupted",
        "message": "This call was interrupted before a result was recorded; its outcome is "
        "unknown.",
    },
    "outcome_unknown": True,
}


@dataclass(frozen=True, slots=True)
class RunHandle:
    run_id: uuid.UUID
    conversation_id: uuid.UUID
    conversation_created: bool


@dataclass(slots=True)
class RunStats:
    status: RunStatus = RunStatus.RUNNING
    error_code: str | None = None
    tools_available: int = 0
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int | None = None
    first_token_ms: int | None = None
    llm_latency_ms: int = 0
    tool_latency_ms: int = 0


class ConversationStore:
    def __init__(self, db: Database, *, settings: RunSettings, metrics: Metrics) -> None:
        self._db = db
        self._settings = settings
        self._metrics = metrics

    # --- run lifecycle ---------------------------------------------------------------------------

    async def start_run(
        self,
        identity: Identity,
        *,
        conversation_id: uuid.UUID | None,
        text: str,
        provider: str,
        model: str,
        request_id: str | None,
    ) -> RunHandle:
        """Rate-limit, lock (or create) the conversation, record the run and the user message,
        all in one transaction."""
        run_id = uuid.uuid4()
        lock_until = datetime.now(UTC) + timedelta(seconds=self._settings.conversation_lock_seconds)
        async with self._db.transaction() as session:
            await self._check_rate_limit(session, identity)
            created = conversation_id is None
            if conversation_id is None:
                conversation = Conversation(
                    id=uuid.uuid4(),
                    team_id=identity.team_id,
                    user_id=identity.user_id,
                    title=_title(text),
                    active_run_id=run_id,
                    locked_until=lock_until,
                )
                session.add(conversation)
                await session.flush()
                conversation_id = conversation.id
            else:
                locked = await session.scalar(
                    update(Conversation)
                    .where(
                        *_owned(identity, conversation_id),
                        (Conversation.active_run_id.is_(None))
                        | (Conversation.locked_until < func.now()),
                    )
                    .values(active_run_id=run_id, locked_until=lock_until, updated_at=func.now())
                    .returning(Conversation.id)
                )
                if locked is None:
                    exists = await session.scalar(
                        select(Conversation.id).where(*_owned(identity, conversation_id))
                    )
                    if exists is None:
                        raise NotFoundError("Conversation not found.")
                    raise ConflictError(
                        "Another message in this conversation is still being answered."
                    )
            session.add(
                AgentRun(
                    id=run_id,
                    conversation_id=conversation_id,
                    team_id=identity.team_id,
                    user_id=identity.user_id,
                    api_key_id=identity.api_key_id,
                    request_id=request_id,
                    provider=provider,
                    model=model,
                    status=RunStatus.RUNNING.value,
                )
            )
            await session.flush()
            session.add(
                Message(
                    id=uuid.uuid4(),
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role=MessageRole.USER.value,
                    content=text,
                )
            )
        return RunHandle(
            run_id=run_id, conversation_id=conversation_id, conversation_created=created
        )

    async def _check_rate_limit(self, session: Any, identity: Identity) -> None:
        window_start = datetime.now(UTC) - timedelta(seconds=60)
        count, oldest = (
            await session.execute(
                select(func.count(AgentRun.id), func.min(AgentRun.created_at)).where(
                    AgentRun.team_id == identity.team_id,
                    AgentRun.user_id == identity.user_id,
                    AgentRun.created_at > window_start,
                )
            )
        ).one()
        if count >= self._settings.rate_limit_per_minute:
            self._metrics.rate_limited.inc()
            retry_after = 1
            if oldest is not None:
                retry_after = max(1, math.ceil(60 - (datetime.now(UTC) - oldest).total_seconds()))
            raise RateLimitedError(
                "Too many messages; wait before sending another.",
                retry_after_seconds=retry_after,
            )

    async def finish_run(self, handle: RunHandle, stats: RunStats) -> None:
        """Record the outcome and release the conversation lock held by this run."""
        async with self._db.transaction() as session:
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == handle.run_id)
                .values(
                    status=stats.status.value,
                    error_code=stats.error_code,
                    tools_available=stats.tools_available,
                    steps=stats.steps,
                    tool_calls=stats.tool_calls,
                    input_tokens=stats.input_tokens,
                    output_tokens=stats.output_tokens,
                    latency_ms=stats.latency_ms,
                    first_token_ms=stats.first_token_ms,
                    llm_latency_ms=stats.llm_latency_ms,
                    tool_latency_ms=stats.tool_latency_ms,
                    finished_at=func.now(),
                )
            )
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == handle.conversation_id,
                    Conversation.active_run_id == handle.run_id,
                )
                .values(active_run_id=None, locked_until=None, updated_at=func.now())
            )

    # --- context -------------------------------------------------------------------------------

    async def load_history(self, conversation_id: uuid.UUID) -> list[ChatMessage]:
        """The most recent messages as model context, starting at a user message, with any
        tool call that never got a result answered as ``interrupted``."""
        async with self._db.transaction() as session:
            rows = (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.seq.desc())
                    .limit(self._settings.history_max_messages)
                )
            ).all()
        messages = list(reversed(rows))
        while messages and messages[0].role != MessageRole.USER.value:
            messages.pop(0)
        return _repair(messages)

    async def add_assistant_message(
        self, handle: RunHandle, text: str | None, tool_calls: Sequence[ToolCall] = ()
    ) -> uuid.UUID:
        message_id = uuid.uuid4()
        async with self._db.transaction() as session:
            session.add(
                Message(
                    id=message_id,
                    conversation_id=handle.conversation_id,
                    run_id=handle.run_id,
                    role=MessageRole.ASSISTANT.value,
                    content=text or None,
                    tool_calls=[_call_json(c) for c in tool_calls] or None,
                )
            )
        return message_id

    async def add_tool_results(
        self, handle: RunHandle, identity: Identity, step: int, results: Sequence[ToolExecution]
    ) -> None:
        """The results the model saw, plus one log row per call, in one transaction."""
        async with self._db.transaction() as session:
            for execution in results:
                binding = execution.binding
                session.add(
                    Message(
                        id=uuid.uuid4(),
                        conversation_id=handle.conversation_id,
                        run_id=handle.run_id,
                        role=MessageRole.TOOL.value,
                        tool_call_id=execution.call.id,
                        tool_name=execution.call.name[:200],
                        tool_result=execution.model_result,
                    )
                )
                session.add(
                    ToolCallLog(
                        id=uuid.uuid4(),
                        run_id=handle.run_id,
                        conversation_id=handle.conversation_id,
                        team_id=identity.team_id,
                        user_id=identity.user_id,
                        step=step,
                        call_id=execution.call.id[:128],
                        function_name=execution.call.name[:200],
                        connector_slug=binding.connector[:64] if binding else None,
                        tool_name=binding.tool[:128] if binding else None,
                        outcome=execution.outcome.value,
                        error_code=execution.error_code,
                        http_status=execution.http_status,
                        latency_ms=execution.latency_ms,
                    )
                )

    # --- reads for the API -----------------------------------------------------------------------

    async def list_conversations(
        self, identity: Identity, *, limit: int, before: datetime | None
    ) -> list[Conversation]:
        query = select(Conversation).where(
            Conversation.team_id == identity.team_id, Conversation.user_id == identity.user_id
        )
        if before is not None:
            query = query.where(Conversation.updated_at < before)
        async with self._db.transaction() as session:
            return list(
                (
                    await session.scalars(
                        query.order_by(Conversation.updated_at.desc()).limit(limit)
                    )
                ).all()
            )

    async def get_conversation(
        self, identity: Identity, conversation_id: uuid.UUID, *, message_limit: int
    ) -> tuple[Conversation, list[Message]]:
        async with self._db.transaction() as session:
            conversation = await session.scalar(
                select(Conversation).where(*_owned(identity, conversation_id))
            )
            if conversation is None:
                raise NotFoundError("Conversation not found.")
            rows = (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.seq.desc())
                    .limit(message_limit)
                )
            ).all()
        return conversation, list(reversed(rows))

    async def delete_conversation(self, identity: Identity, conversation_id: uuid.UUID) -> None:
        async with self._db.transaction() as session:
            conversation = await session.scalar(
                select(Conversation).where(*_owned(identity, conversation_id)).with_for_update()
            )
            if conversation is None:
                raise NotFoundError("Conversation not found.")
            if conversation.active_run_id is not None and (
                conversation.locked_until is not None
                and conversation.locked_until > datetime.now(UTC)
            ):
                raise ConflictError("A message in this conversation is still being answered.")
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))

    async def list_runs(
        self, identity: Identity, conversation_id: uuid.UUID, *, limit: int
    ) -> list[tuple[AgentRun, list[ToolCallLog]]]:
        async with self._db.transaction() as session:
            if (
                await session.scalar(
                    select(Conversation.id).where(*_owned(identity, conversation_id))
                )
            ) is None:
                raise NotFoundError("Conversation not found.")
            runs = (
                await session.scalars(
                    select(AgentRun)
                    .where(AgentRun.conversation_id == conversation_id)
                    .order_by(AgentRun.created_at.desc())
                    .limit(limit)
                )
            ).all()
            logs = (
                await session.scalars(
                    select(ToolCallLog)
                    .where(ToolCallLog.run_id.in_([r.id for r in runs]))
                    .order_by(ToolCallLog.created_at)
                )
            ).all()
        by_run: dict[uuid.UUID, list[ToolCallLog]] = {}
        for log in logs:
            by_run.setdefault(log.run_id, []).append(log)
        return [(run, by_run.get(run.id, [])) for run in runs]


def _owned(identity: Identity, conversation_id: uuid.UUID) -> tuple[Any, ...]:
    return (
        Conversation.id == conversation_id,
        Conversation.team_id == identity.team_id,
        Conversation.user_id == identity.user_id,
    )


def _title(text: str) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else "New conversation"
    return first_line[:_TITLE_CHARS]


def _call_json(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": call.arguments,
        "provider_metadata": call.provider_metadata,
    }


def _repair(rows: Sequence[Message]) -> list[ChatMessage]:
    """Convert rows to model messages so every tool call is followed by its results."""
    answered = {m.tool_call_id for m in rows if m.role == MessageRole.TOOL.value}
    messages: list[ChatMessage] = []
    for row in rows:
        if row.role == MessageRole.USER.value:
            messages.append(ChatMessage(role="user", content=row.content or ""))
        elif row.role == MessageRole.ASSISTANT.value:
            calls = tuple(
                ToolCall(
                    id=str(c.get("id")),
                    name=str(c.get("name")),
                    arguments=c.get("arguments"),
                    provider_metadata=c.get("provider_metadata") or {},
                )
                for c in (row.tool_calls or [])
            )
            messages.append(ChatMessage(role="assistant", content=row.content, tool_calls=calls))
            messages.extend(
                ChatMessage(
                    role="tool",
                    tool_call_id=call.id,
                    tool_name=call.name,
                    tool_result=_INTERRUPTED_RESULT,
                )
                for call in calls
                if call.id not in answered
            )
        else:
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=row.tool_call_id,
                    tool_name=row.tool_name,
                    tool_result=row.tool_result or {},
                )
            )
    return messages
