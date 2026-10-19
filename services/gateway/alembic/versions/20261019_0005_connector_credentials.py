"""Optional static credential (one encrypted header) per connector.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("connectors", sa.Column("credential_header", sa.String(64), nullable=True))
    op.add_column("connectors", sa.Column("credential_ciphertext", sa.Text(), nullable=True))
    op.create_check_constraint(
        "credential_complete",
        "connectors",
        "(credential_header IS NULL) = (credential_ciphertext IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_connectors_credential_complete", "connectors", type_="check")
    op.drop_column("connectors", "credential_ciphertext")
    op.drop_column("connectors", "credential_header")
