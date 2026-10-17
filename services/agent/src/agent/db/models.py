"""ORM schema. Alembic migrations in ``alembic/versions`` are the source of truth for DDL.

Users, teams and API keys live in the gateway; their ids are stored here as plain UUIDs (no
foreign keys across services). Every row carries ``team_id`` and ``user_id`` and every query
filters on both, so one user's conversations are never visible to another user or team.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Bumped by every migration; /health/ready fails until the database is at this revision.
SCHEMA_REVISION = "0001"
# Own version table so the agent can share a PostgreSQL database with other services.
VERSION_TABLE = "agent_alembic_version"

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    STEP_LIMIT = "step_limit"  # max model responses reached before a final answer
    TOOL_LIMIT = "tool_limit"  # max tool calls reached
    TOKEN_LIMIT = "token_limit"  # noqa: S105 - token budget of the run exhausted
    TIMEOUT = "timeout"
    FAILED = "failed"  # provider, gateway or internal error; see error_code
    CANCELLED = "cancelled"  # the client disconnected


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_team_id_user_id_updated_at", "team_id", "user_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()
    # Set while a run is in progress so two runs never interleave messages; a lock past
    # locked_until belongs to a replica that died and may be taken over.
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """One turn of context. Tool results are stored truncated so later turns can use them."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id_seq", "conversation_id", "seq"),
        CheckConstraint("role IN ('user', 'assistant', 'tool')", name="role_valid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    # Global insertion order; ordering by it inside a conversation is the message order.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str | None] = mapped_column(Text)
    # assistant: [{"id", "name", "arguments", "provider_metadata"}]
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # tool: which call this answers, and the (truncated) result given to the model
    tool_call_id: Mapped[str | None] = mapped_column(String(128))
    tool_name: Mapped[str | None] = mapped_column(String(200))
    tool_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()


class AgentRun(Base):
    """One chat request: its outcome, limits reached and latency/token metrics."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_team_id_user_id_created_at", "team_id", "user_id", "created_at"),
        Index("ix_agent_runs_team_id_created_at", "team_id", "created_at"),
        Index("ix_agent_runs_conversation_id_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_id: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.RUNNING.value)
    error_code: Mapped[str | None] = mapped_column(String(64))
    tools_available: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    llm_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tool_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolCallLog(Base):
    """One tool call requested by the model and what happened to it. Arguments and results are
    not stored here (the conversation keeps what the model saw)."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_run_id", "run_id"),
        Index("ix_tool_calls_team_id_created_at", "team_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    step: Mapped[int] = mapped_column(Integer)
    call_id: Mapped[str] = mapped_column(String(128))
    # The model-facing name; connector and tool are set only when it named an offered tool.
    function_name: Mapped[str] = mapped_column(String(200))
    connector_slug: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
