"""Session-bound API keys for the dashboard.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys", sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_api_keys_session_id_auth_sessions",
        "api_keys",
        "auth_sessions",
        ["session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_api_keys_session_id",
        "api_keys",
        ["session_id"],
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_api_keys_session_id", table_name="api_keys")
    op.drop_constraint("fk_api_keys_session_id_auth_sessions", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "session_id")
