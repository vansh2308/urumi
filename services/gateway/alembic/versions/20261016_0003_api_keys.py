"""Platform API keys.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
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


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("tool_restrictions", postgresql.ARRAY(sa.String(200)), nullable=True),
        sa.Column("created_by_user_id", _UUID, nullable=True),
        _ts("created_at", default=True),
        _ts("expires_at"),
        _ts("last_used_at", nullable=True),
        _ts("revoked_at", nullable=True),
        sa.Column("revoked_by_user_id", _UUID, nullable=True),
        sa.Column("rotated_from_id", _UUID, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_api_keys_team_id_teams", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_api_keys_owner_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_api_keys_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_api_keys_revoked_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rotated_from_id"],
            ["api_keys.id"],
            name="fk_api_keys_rotated_from_id_api_keys",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        sa.CheckConstraint(
            "scopes <@ ARRAY['tools:read', 'tools:call', 'usage:read']::varchar[]"
            " AND cardinality(scopes) > 0",
            name="ck_api_keys_scopes_valid",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_api_keys_expiry_after_creation"),
    )
    op.create_index("ix_api_keys_team_id_owner_user_id", "api_keys", ["team_id", "owner_user_id"])


def downgrade() -> None:
    op.drop_table("api_keys")
