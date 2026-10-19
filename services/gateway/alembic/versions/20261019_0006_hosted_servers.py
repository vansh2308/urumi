"""Hosted MCP servers: connectors the gateway deploys into the cluster from an image.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connectors",
        sa.Column("kind", sa.String(16), nullable=False, server_default="remote"),
    )
    op.create_check_constraint("kind_known", "connectors", "kind IN ('remote', 'hosted')")

    op.create_table(
        "hosted_servers",
        sa.Column("connector_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("k8s_name", sa.String(63), nullable=False),
        sa.Column("namespace", sa.String(63), nullable=False),
        sa.Column("image", sa.String(300), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(200), nullable=False),
        sa.Column("args", postgresql.ARRAY(sa.String(500)), nullable=False, server_default="{}"),
        sa.Column("env", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "secret_keys", postgresql.ARRAY(sa.String(128)), nullable=False, server_default="{}"
        ),
        sa.Column("cpu_millicores", sa.Integer(), nullable=False),
        sa.Column("memory_mib", sa.Integer(), nullable=False),
        sa.Column("egress", sa.String(16), nullable=False),
        sa.Column("config_revision", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("connector_id", name="pk_hosted_servers"),
        sa.UniqueConstraint("k8s_name", name="uq_hosted_servers_k8s_name"),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["connectors.id"],
            name="fk_hosted_servers_connector_id_connectors",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_hosted_servers_port_range"),
        sa.CheckConstraint("egress IN ('internet', 'none')", name="ck_hosted_servers_egress_known"),
        sa.CheckConstraint("cpu_millicores > 0", name="ck_hosted_servers_cpu_positive"),
        sa.CheckConstraint("memory_mib > 0", name="ck_hosted_servers_memory_positive"),
    )


def downgrade() -> None:
    op.drop_table("hosted_servers")
    op.drop_constraint("ck_connectors_kind_known", "connectors", type_="check")
    op.drop_column("connectors", "kind")
