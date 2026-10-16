"""Fixed-window rate limiting backed by PostgreSQL.

One upsert per check keeps counters consistent across gateway replicas without adding Redis.
Windows are one minute; old rows are removed by the retention purge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from gateway.db.engine import Database
from gateway.db.models import RateLimitCounter
from gateway.errors import RateLimitedError
from gateway.metrics import Metrics

WINDOW = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class RateLimitStatus:
    limit: int
    remaining: int
    reset_seconds: int


class RateLimiter:
    def __init__(self, db: Database, metrics: Metrics, *, enabled: bool) -> None:
        self._db = db
        self._metrics = metrics
        self._enabled = enabled

    async def hit(self, bucket: str, *, limit: int, scope: str) -> RateLimitStatus | None:
        """Count one request against ``bucket``; raise ``RateLimitedError`` past ``limit``."""
        if not self._enabled:
            return None
        now = datetime.now(UTC)
        window_start = now.replace(second=0, microsecond=0)
        stmt = (
            insert(RateLimitCounter)
            .values(bucket=bucket, window_start=window_start, count=1)
            .on_conflict_do_update(
                index_elements=["bucket", "window_start"],
                set_={"count": RateLimitCounter.count + 1},
            )
            .returning(RateLimitCounter.count)
        )
        async with self._db.transaction() as session:
            count = (await session.execute(stmt)).scalar_one()
        reset = max(1, math.ceil((window_start + WINDOW - now).total_seconds()))
        if count > limit:
            self._metrics.rate_limited.labels(scope=scope).inc()
            raise RateLimitedError(
                "Too many requests; retry later.", retry_after_seconds=reset, limit=limit
            )
        return RateLimitStatus(limit=limit, remaining=limit - count, reset_seconds=reset)

    async def purge(self) -> int:
        cutoff = datetime.now(UTC) - 2 * WINDOW
        async with self._db.transaction() as session:
            result = await session.execute(
                delete(RateLimitCounter).where(RateLimitCounter.window_start < cutoff)
            )
        return int(getattr(result, "rowcount", 0) or 0)
