"""Teams, memberships with roles, and invitations.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
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
        "teams",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column(
            "members_can_create_keys", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("max_key_ttl_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.UniqueConstraint("slug", name="uq_teams_slug"),
        sa.CheckConstraint("max_key_ttl_days > 0", name="ck_teams_max_key_ttl_positive"),
        sa.CheckConstraint(
            "rate_limit_per_minute IS NULL OR rate_limit_per_minute > 0",
            name="ck_teams_rate_limit_positive",
        ),
    )

    op.create_table(
        "team_memberships",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        _ts("created_at", default=True),
        _ts("updated_at", default=True),
        sa.PrimaryKeyConstraint("id", name="pk_team_memberships"),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_team_memberships_team_id_teams",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_team_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_memberships_team_id_user_id"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_team_memberships_role_valid"),
    )
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"])

    op.create_table(
        "invitations",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("invited_by_user_id", _UUID, nullable=True),
        _ts("created_at", default=True),
        _ts("expires_at"),
        _ts("accepted_at", nullable=True),
        sa.Column("accepted_by_user_id", _UUID, nullable=True),
        _ts("revoked_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_invitations_team_id_teams", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name="fk_invitations_invited_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name="fk_invitations_accepted_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_invitations_role_valid"),
        sa.CheckConstraint("email = lower(email)", name="ck_invitations_email_lowercase"),
    )
    op.create_index(
        "uq_invitations_team_id_email_open",
        "invitations",
        ["team_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("invitations")
    op.drop_table("team_memberships")
    op.drop_table("teams")
