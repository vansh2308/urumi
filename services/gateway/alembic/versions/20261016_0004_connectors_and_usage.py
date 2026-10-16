"""Connector registry, team tool policies, member grants and tool invocation records.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TOOLS = postgresql.ARRAY(sa.String(128))


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", _UUID, nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_connectors"),
        sa.UniqueConstraint("slug", name="uq_connectors_slug"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_connectors_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name="ck_connectors_slug_format"),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_connectors_timeout_positive"),
        sa.CheckConstraint("max_concurrency > 0", name="ck_connectors_concurrency_positive"),
    )

    op.create_table(
        "team_connectors",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("connector_id", _UUID, nullable=False),
        sa.Column("allow_all_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_tools", _TOOLS, nullable=False, server_default="{}"),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_team_connectors"),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_team_connectors_team_id_teams", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_team_connectors_connector_id_connectors",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "team_id", "connector_id", name="uq_team_connectors_team_id_connector_id"
        ),
    )

    op.create_table(
        "member_tool_grants",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("membership_id", _UUID, nullable=False),
        sa.Column("connector_id", _UUID, nullable=False),
        sa.Column("allow_all_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tools", _TOOLS, nullable=False, server_default="{}"),
        sa.Column("granted_by_user_id", _UUID, nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_member_tool_grants"),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["team_memberships.id"],
            name="fk_member_tool_grants_membership_id_team_memberships",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_member_tool_grants_connector_id_connectors",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name="fk_member_tool_grants_granted_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "membership_id",
            "connector_id",
            name="uq_member_tool_grants_membership_id_connector_id",
        ),
    )

    op.create_table(
        "tool_invocations",
        sa.Column("id", _UUID, nullable=False),
        _ts("created_at", default=True),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("api_key_id", _UUID, nullable=True),
        sa.Column("connector_id", _UUID, nullable=True),
        sa.Column("connector_slug", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("transport", sa.String(8), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tool_invocations"),
    )
    op.create_index(
        "ix_tool_invocations_team_id_created_at", "tool_invocations", ["team_id", "created_at"]
    )
    op.create_index(
        "ix_tool_invocations_team_id_user_id_created_at",
        "tool_invocations",
        ["team_id", "user_id", "created_at"],
    )
    op.create_index("ix_tool_invocations_created_at", "tool_invocations", ["created_at"])


def downgrade() -> None:
    op.drop_table("tool_invocations")
    op.drop_table("member_tool_grants")
    op.drop_table("team_connectors")
    op.drop_table("connectors")
