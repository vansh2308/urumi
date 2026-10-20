"""Provisioned stores, and team-owned store connectors.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
STATUSES = "'requested', 'provisioning', 'ready', 'failed', 'deleting', 'deleted'"


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.drop_constraint(op.f("ck_connectors_kind_known"), "connectors", type_="check")
    op.create_check_constraint("kind_known", "connectors", "kind IN ('remote', 'hosted', 'store')")
    op.add_column("connectors", sa.Column("owner_team_id", _UUID, nullable=True))
    op.create_foreign_key(
        "fk_connectors_owner_team_id_teams",
        "connectors",
        "teams",
        ["owner_team_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # A store connector always belongs to the team that owns the store.
    op.create_check_constraint(
        "store_has_owner", "connectors", "kind <> 'store' OR owner_team_id IS NOT NULL"
    )
    op.create_index("ix_connectors_owner_team_id", "connectors", ["owner_team_id"])

    op.create_table(
        "stores",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("created_by_user_id", _UUID, nullable=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("slug", sa.String(21), nullable=False),
        sa.Column("namespace", sa.String(63), nullable=False),
        sa.Column("release", sa.String(53), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("step", sa.String(32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        _ts("deadline_at", nullable=False),
        sa.Column("lease_owner", sa.String(100), nullable=True),
        _ts("lease_expires_at"),
        sa.Column("connector_id", _UUID, nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        # The WordPress admin password until the provisioner has put it in the store's Secret
        # (Fernet, UPSTREAM_CREDENTIAL_KEYS); cleared right after.
        sa.Column("admin_password_ciphertext", sa.Text(), nullable=True),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        _ts("ready_at"),
        _ts("deleted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_stores"),
        # Slugs are never reused, deleted stores included (usage history stays unambiguous).
        sa.UniqueConstraint("slug", name="uq_stores_slug"),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_stores_team_id_teams", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_stores_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_stores_connector_id_connectors",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_stores_status_known"),
        sa.CheckConstraint("attempts >= 0", name="ck_stores_attempts_non_negative"),
        sa.CheckConstraint("slug ~ '^[a-z][a-z0-9-]{0,19}[a-z0-9]$'", name="ck_stores_slug_format"),
    )
    op.create_index(
        "uq_stores_team_id_name_live",
        "stores",
        ["team_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("status <> 'deleted'"),
    )
    op.create_index(
        "uq_stores_created_by_user_id_idempotency_key",
        "stores",
        ["created_by_user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_stores_team_id_created_at", "stores", ["team_id", "created_at"])
    # The provisioner's work queue: rows that still need a step.
    op.create_index(
        "ix_stores_pending",
        "stores",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("status IN ('requested', 'provisioning', 'deleting')"),
    )


def downgrade() -> None:
    op.drop_table("stores")
    op.drop_index("ix_connectors_owner_team_id", table_name="connectors")
    op.drop_constraint(op.f("ck_connectors_store_has_owner"), "connectors", type_="check")
    op.drop_constraint("fk_connectors_owner_team_id_teams", "connectors", type_="foreignkey")
    op.drop_column("connectors", "owner_team_id")
    op.drop_constraint(op.f("ck_connectors_kind_known"), "connectors", type_="check")
    op.create_check_constraint("kind_known", "connectors", "kind IN ('remote', 'hosted')")
