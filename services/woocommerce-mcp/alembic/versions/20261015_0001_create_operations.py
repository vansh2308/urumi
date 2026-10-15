"""Create the operations table for idempotency and write-outcome tracking.

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_operations"),
        sa.UniqueConstraint(
            "operation", "idempotency_key", name="uq_operations_operation_idempotency_key"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'unknown')",
            name="status_valid",  # expands to ck_operations_status_valid via naming convention
        ),
    )
    op.create_index("ix_operations_expires_at", "operations", ["expires_at"])
    op.create_index("ix_operations_status_updated_at", "operations", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_operations_status_updated_at", table_name="operations")
    op.drop_index("ix_operations_expires_at", table_name="operations")
    op.drop_table("operations")
