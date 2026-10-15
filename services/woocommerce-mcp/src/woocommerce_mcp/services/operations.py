"""Idempotent execution of WooCommerce write operations.

``OperationRunner.run`` records each write in PostgreSQL before calling WooCommerce and records
the outcome afterwards. When the caller supplies an idempotency key:

* the same key + same request after success replays the stored result without calling
  WooCommerce again;
* the same key with a different request is rejected (``idempotency_conflict``);
* the same key while the first request is still running is rejected (``operation_in_progress``);
* after a definite failure (the store rejected or never received the write) the request is
  executed again;
* after an ambiguous failure (timeout after sending, crash mid-request) the request is executed
  again only if the operation is naturally idempotent (e.g. setting an order status). For
  non-idempotent creates the caller gets ``outcome_unknown`` and must check the store and use a
  new key, because re-sending could create a duplicate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from woocommerce_mcp.db.models import OperationStatus
from woocommerce_mcp.db.operations import NewOperation, OperationRecord, OperationStore
from woocommerce_mcp.errors import (
    AppError,
    IdempotencyConflictError,
    OperationInProgressError,
    OutcomeUnknownError,
)
from woocommerce_mcp.log import correlation_id_var

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: str
    resource_type: str
    reexecutable: bool
    """True if re-sending after an ambiguous failure cannot duplicate side effects."""


def fingerprint(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class OperationRunner:
    def __init__(
        self,
        store: OperationStore,
        *,
        retention: timedelta,
        in_progress_lease: timedelta,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._retention = retention
        self._lease = in_progress_lease
        self._clock = clock

    async def run[R: BaseModel](
        self,
        spec: OperationSpec,
        *,
        request: Mapping[str, Any],
        idempotency_key: str | None,
        result_type: type[R],
        resource_id: Callable[[R], str],
        action: Callable[[], Awaitable[R]],
    ) -> R:
        request_fp = fingerprint(request)
        claim = await self._claim(spec, idempotency_key, request_fp)
        if isinstance(claim, dict):
            logger.info("idempotent replay", extra={"operation": spec.name})
            return result_type.model_validate(claim)
        op_id = claim

        try:
            result = await action()
        except OutcomeUnknownError as exc:
            await self._record_failure(op_id, OperationStatus.UNKNOWN, exc.code, exc)
            raise
        except AppError as exc:
            await self._record_failure(op_id, OperationStatus.FAILED, exc.code, exc)
            raise
        except BaseException as exc:
            # Unexpected error or cancellation (client disconnect, shutdown): we cannot tell
            # whether WooCommerce applied the write.
            await self._record_failure(op_id, OperationStatus.UNKNOWN, "interrupted", exc)
            raise

        try:
            await self._store.mark_succeeded(
                op_id,
                resource_id=resource_id(result),
                result=result.model_dump(mode="json"),
                now=self._clock(),
            )
        except AppError:
            # The write succeeded upstream; returning it is more useful than failing. A retry
            # with the same key will see a stale 'pending' record and be handled safely.
            logger.exception("failed to record operation success", extra={"operation_id": op_id})
        return result

    async def purge_expired(self) -> int:
        return await self._store.purge_expired(self._clock())

    async def _claim(
        self, spec: OperationSpec, key: str | None, request_fp: str
    ) -> uuid.UUID | dict[str, Any]:
        now = self._clock()
        new = NewOperation(
            operation=spec.name,
            idempotency_key=key,
            request_fingerprint=request_fp,
            resource_type=spec.resource_type,
            correlation_id=correlation_id_var.get(),
            now=now,
            expires_at=now + self._retention,
        )
        op_id = await self._store.insert_pending(new)
        if op_id is not None:
            return op_id
        assert key is not None  # noqa: S101 - rows without a key never conflict

        existing = await self._store.get_by_key(spec.name, key)
        if existing is None:  # purged between the insert and the read
            op_id = await self._store.insert_pending(new)
            if op_id is None:
                raise OperationInProgressError(_IN_PROGRESS)
            return op_id

        decision = self._decide(spec, existing, request_fp, now)
        if isinstance(decision, dict):
            return decision
        restarted = await self._store.restart(
            existing,
            request_fingerprint=request_fp,
            correlation_id=new.correlation_id,
            now=now,
            expires_at=new.expires_at,
        )
        if not restarted:
            raise OperationInProgressError(_IN_PROGRESS)
        return existing.id

    def _decide(
        self, spec: OperationSpec, record: OperationRecord, request_fp: str, now: datetime
    ) -> dict[str, Any] | None:
        """Return a stored result to replay, ``None`` to execute again, or raise."""
        if record.expires_at <= now:
            return None  # the key has expired; treat it as new
        if record.request_fingerprint != request_fp:
            raise IdempotencyConflictError(
                "This idempotency key was already used with different parameters. "
                "Use a new key for a different request."
            )
        match record.status:
            case OperationStatus.SUCCEEDED if record.result is not None:
                return record.result
            case OperationStatus.FAILED:
                return None
            case OperationStatus.PENDING if now - record.updated_at < self._lease:
                raise OperationInProgressError(_IN_PROGRESS)
            case _ if spec.reexecutable:
                return None
            case _:
                raise OutcomeUnknownError(
                    "A previous attempt with this idempotency key did not finish cleanly, so the "
                    "write may or may not have been applied. Check the store, then retry with a "
                    "new idempotency key if needed."
                )

    async def _record_failure(
        self, op_id: uuid.UUID, status: OperationStatus, code: str, exc: BaseException
    ) -> None:
        message = exc.public_message if isinstance(exc, AppError) else type(exc).__name__
        try:
            # Shield so the outcome is still recorded when the request task is being cancelled.
            await asyncio.shield(
                self._store.mark_failed(
                    op_id, status=status, error_code=code, message=message, now=self._clock()
                )
            )
        except (AppError, asyncio.CancelledError):
            logger.warning(
                "failed to record operation outcome",
                extra={"operation_id": str(op_id), "status": status.value},
            )


_IN_PROGRESS = (
    "A request with this idempotency key is still in progress. Retry shortly with the same key."
)
