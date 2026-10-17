"""The caller's own conversations, their messages and the recorded runs.

Scoped to the user and team of the API key; another user's conversation id answers 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from agent.api.deps import API_PREFIX, CallerDep, ContainerDep
from agent.api.schemas import (
    ConversationDetailOut,
    ConversationOut,
    MessageOut,
    RunOut,
    ToolCallLogOut,
)
from agent.db.models import Conversation

router = APIRouter(prefix=f"{API_PREFIX}/conversations", tags=["conversations"])


def _conversation_out(conversation: Conversation) -> ConversationOut:
    busy = (
        conversation.active_run_id is not None
        and conversation.locked_until is not None
        and conversation.locked_until > datetime.now(UTC)
    )
    return ConversationOut(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        busy=busy,
    )


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    caller: CallerDep,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[
        datetime | None, Query(description="Return conversations updated before this time.")
    ] = None,
) -> list[ConversationOut]:
    """Most recently updated first. Page with ``before`` = the last item's ``updated_at``."""
    rows = await container.conversations.list_conversations(
        caller.identity, limit=limit, before=before
    )
    return [_conversation_out(c) for c in rows]


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    caller: CallerDep,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=1000, description="Most recent messages.")] = 200,
) -> ConversationDetailOut:
    """The conversation with its messages, tool calls and the tool results the model saw."""
    conversation, messages = await container.conversations.get_conversation(
        caller.identity, conversation_id, message_limit=limit
    )
    return ConversationDetailOut(
        conversation=_conversation_out(conversation),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.get("/{conversation_id}/runs", response_model=list[RunOut])
async def list_runs(
    conversation_id: uuid.UUID,
    caller: CallerDep,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOut]:
    """Each chat request with its status, latencies, tokens and per-tool-call log."""
    runs = await container.conversations.list_runs(caller.identity, conversation_id, limit=limit)
    out: list[RunOut] = []
    for run, logs in runs:
        item = RunOut.model_validate(run)
        item.tool_call_log = [ToolCallLogOut.model_validate(log) for log in logs]
        out.append(item)
    return out


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, caller: CallerDep, container: ContainerDep
) -> None:
    """Delete the conversation, its messages, runs and tool call logs."""
    await container.conversations.delete_conversation(caller.identity, conversation_id)
