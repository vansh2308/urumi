"""Conversations, messages, agent runs and tool call logs.

Revision ID: 0001
Revises:
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def _int(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(
        name, sa.Integer(), nullable=nullable, server_default=None if nullable else "0"
    )


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.Column("active_run_id", _UUID, nullable=True),
        _ts("locked_until", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index(
        "ix_conversations_team_id_user_id_updated_at",
        "conversations",
        ["team_id", "user_id", "updated_at"],
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("api_key_id", _UUID, nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        _int("tools_available"),
        _int("steps"),
        _int("tool_calls"),
        _int("input_tokens"),
        _int("output_tokens"),
        _int("latency_ms", nullable=True),
        _int("first_token_ms", nullable=True),
        _int("llm_latency_ms"),
        _int("tool_latency_ms"),
        _ts("created_at", default=True),
        _ts("finished_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_agent_runs_conversation_id_conversations",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_agent_runs_team_id_user_id_created_at",
        "agent_runs",
        ["team_id", "user_id", "created_at"],
    )
    op.create_index("ix_agent_runs_team_id_created_at", "agent_runs", ["team_id", "created_at"])
    op.create_index(
        "ix_agent_runs_conversation_id_created_at",
        "agent_runs",
        ["conversation_id", "created_at"],
    )

    op.create_table(
        "messages",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(), nullable=True),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("tool_name", sa.String(200), nullable=True),
        sa.Column("tool_result", postgresql.JSONB(), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_messages_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("role IN ('user', 'assistant', 'tool')", name="ck_messages_role_valid"),
    )
    op.create_index("ix_messages_conversation_id_seq", "messages", ["conversation_id", "seq"])

    op.create_table(
        "tool_calls",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("run_id", _UUID, nullable=False),
        sa.Column("conversation_id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(128), nullable=False),
        sa.Column("function_name", sa.String(200), nullable=False),
        sa.Column("connector_slug", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_tool_calls"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_tool_calls_run_id_agent_runs",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])
    op.create_index("ix_tool_calls_team_id_created_at", "tool_calls", ["team_id", "created_at"])


def downgrade() -> None:
    op.drop_table("tool_calls")
    op.drop_table("messages")
    op.drop_table("agent_runs")
    op.drop_table("conversations")
