"""Data access for the ``operations`` table.

Every method is a single short transaction so no database connection is held while WooCommerce
is being called. Concurrency between replicas is resolved by the unique constraint on
``(operation, idempotency_key)`` and compare-and-set updates, not by long-lived row locks.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from woocommerce_mcp.db.models import Operation, OperationStatus
from woocommerce_mcp.errors import PersistenceUnavailableError


@dataclass(frozen=True, slots=True)
class OperationRecord:
    id: uuid.UUID
    status: OperationStatus
    request_fingerprint: str
    attempts: int
    result: dict[str, Any] | None
    error_code: str | None
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NewOperation:
    operation: str
    idempotency_key: str | None
    request_fingerprint: str
    resource_type: str
    correlation_id: str | None
    now: datetime
    expires_at: datetime


class OperationStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self._session_factory() as session, session.begin():
                yield session
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise PersistenceUnavailableError(
                "Operation tracking storage is unavailable; the request was not sent."
            ) from exc

    async def insert_pending(self, new: NewOperation) -> uuid.UUID | None:
        """Insert a pending record; return its id, or ``None`` if the key already exists."""
        stmt = (
            insert(Operation)
            .values(
                id=uuid.uuid4(),
                operation=new.operation,
                idempotency_key=new.idempotency_key,
                request_fingerprint=new.request_fingerprint,
                status=OperationStatus.PENDING.value,
                attempts=1,
                resource_type=new.resource_type,
                correlation_id=new.correlation_id,
                created_at=new.now,
                updated_at=new.now,
                expires_at=new.expires_at,
            )
            .on_conflict_do_nothing(constraint="uq_operations_operation_idempotency_key")
            .returning(Operation.id)
        )
        async with self._transaction() as session:
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_by_key(self, operation: str, idempotency_key: str) -> OperationRecord | None:
        stmt = select(Operation).where(
            Operation.operation == operation, Operation.idempotency_key == idempotency_key
        )
        async with self._transaction() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _to_record(row) if row is not None else None

    async def restart(
        self,
        record: OperationRecord,
        *,
        request_fingerprint: str,
        correlation_id: str | None,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Compare-and-set a previous attempt back to pending. ``False`` if another request won."""
        stmt = (
            update(Operation)
            .where(
                Operation.id == record.id,
                Operation.attempts == record.attempts,
                Operation.status == record.status.value,
            )
            .values(
                status=OperationStatus.PENDING.value,
                attempts=Operation.attempts + 1,
                request_fingerprint=request_fingerprint,
                correlation_id=correlation_id,
                result=None,
                resource_id=None,
                error_code=None,
                error_message=None,
                completed_at=None,
                updated_at=now,
                expires_at=expires_at,
            )
        )
        async with self._transaction() as session:
            result = await session.execute(stmt)
            return bool(getattr(result, "rowcount", 0))

    async def mark_succeeded(
        self, op_id: uuid.UUID, *, resource_id: str, result: dict[str, Any], now: datetime
    ) -> None:
        await self._finish(
            op_id, OperationStatus.SUCCEEDED, now, resource_id=resource_id, result=result
        )

    async def mark_failed(
        self,
        op_id: uuid.UUID,
        *,
        status: OperationStatus,
        error_code: str,
        message: str,
        now: datetime,
    ) -> None:
        await self._finish(op_id, status, now, error_code=error_code, error_message=message)

    async def _finish(
        self, op_id: uuid.UUID, status: OperationStatus, now: datetime, **values: Any
    ) -> None:
        stmt = (
            update(Operation)
            .where(Operation.id == op_id, Operation.status == OperationStatus.PENDING.value)
            .values(status=status.value, updated_at=now, completed_at=now, **values)
        )
        async with self._transaction() as session:
            await session.execute(stmt)

    async def purge_expired(self, now: datetime) -> int:
        stmt = delete(Operation).where(Operation.expires_at < now)
        async with self._transaction() as session:
            result = await session.execute(stmt)
            return int(getattr(result, "rowcount", 0) or 0)

    async def check_ready(self) -> None:
        """Verify connectivity and that migrations have created the schema."""
        async with self._transaction() as session:
            await session.execute(text("SELECT 1 FROM operations LIMIT 0"))


def _to_record(row: Operation) -> OperationRecord:
    return OperationRecord(
        id=row.id,
        status=OperationStatus(row.status),
        request_fingerprint=row.request_fingerprint,
        attempts=row.attempts,
        result=row.result,
        error_code=row.error_code,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
    )
