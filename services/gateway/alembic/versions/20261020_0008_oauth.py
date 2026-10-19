"""OAuth 2.1 authorization server tables, and OAuth-issued API keys.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_SCOPES = postgresql.ARRAY(sa.String(32))


def _ts(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def _client_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["client_id"],
        ["oauth_clients.client_id"],
        name=f"fk_{table}_client_id_oauth_clients",
        ondelete="CASCADE",
    )


def _owner_fks(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=f"fk_{table}_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=f"fk_{table}_team_id_teams", ondelete="CASCADE"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("client_name", sa.String(200), nullable=True),
        sa.Column("info", postgresql.JSONB(), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        _ts("created_at", default=True),
        sa.PrimaryKeyConstraint("client_id", name="pk_oauth_clients"),
    )
    op.create_table(
        "oauth_authorization_requests",
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        _ts("created_at", default=True),
        _ts("expires_at"),
        _ts("decided_at", nullable=True),
        sa.PrimaryKeyConstraint("request_hash", name="pk_oauth_authorization_requests"),
        _client_fk("oauth_authorization_requests"),
    )
    op.create_table(
        "oauth_grants",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("scopes", _SCOPES, nullable=False),
        sa.Column("resource", sa.String(500), nullable=True),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False),
        _ts("refresh_expires_at"),
        _ts("created_at", default=True),
        _ts("last_refreshed_at", nullable=True),
        _ts("revoked_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_oauth_grants"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_oauth_grants_refresh_token_hash"),
        _client_fk("oauth_grants"),
        *_owner_fks("oauth_grants"),
    )
    op.create_index("ix_oauth_grants_user_id", "oauth_grants", ["user_id"])
    op.create_table(
        "oauth_codes",
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("team_id", _UUID, nullable=False),
        sa.Column("scopes", _SCOPES, nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("redirect_uri", sa.String(2000), nullable=False),
        sa.Column("redirect_uri_provided_explicitly", sa.Boolean(), nullable=False),
        sa.Column("resource", sa.String(500), nullable=True),
        _ts("expires_at"),
        _ts("used_at", nullable=True),
        sa.PrimaryKeyConstraint("code_hash", name="pk_oauth_codes"),
        _client_fk("oauth_codes"),
        *_owner_fks("oauth_codes"),
    )
    op.add_column("api_keys", sa.Column("oauth_grant_id", _UUID, nullable=True))
    op.create_foreign_key(
        "fk_api_keys_oauth_grant_id_oauth_grants",
        "api_keys",
        "oauth_grants",
        ["oauth_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_api_keys_oauth_grant_id",
        "api_keys",
        ["oauth_grant_id"],
        postgresql_where=sa.text("oauth_grant_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_oauth_grant_id", table_name="api_keys")
    op.drop_constraint("fk_api_keys_oauth_grant_id_oauth_grants", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "oauth_grant_id")
    op.drop_table("oauth_codes")
    op.drop_index("ix_oauth_grants_user_id", table_name="oauth_grants")
    op.drop_table("oauth_grants")
    op.drop_table("oauth_authorization_requests")
    op.drop_table("oauth_clients")
