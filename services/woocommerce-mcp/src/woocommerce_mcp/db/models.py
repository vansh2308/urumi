"""ORM schema. Alembic migrations in ``alembic/versions`` are the source of truth for DDL."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class OperationStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # definitively not applied upstream; safe to retry
    UNKNOWN = "unknown"  # may or may not have been applied upstream


class Operation(Base):
    """One write operation sent to WooCommerce, used for idempotency and outcome tracking.

    This is operational metadata with a bounded retention period, not an audit log: it stores
    the request fingerprint (a hash, never the payload), the resulting resource id and the
    constrained tool result needed to replay an idempotent response.
    """

    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint(
            "operation", "idempotency_key", name="uq_operations_operation_idempotency_key"
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'unknown')", name="status_valid"
        ),
        Index("ix_operations_expires_at", "expires_at"),
        Index("ix_operations_status_updated_at", "status", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
