"""Idempotent write execution against a real (throwaway) PostgreSQL database."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from woocommerce_mcp.config import DatabaseSettings
from woocommerce_mcp.db.engine import create_engine, create_session_factory
from woocommerce_mcp.db.operations import OperationStore
from woocommerce_mcp.errors import (
    IdempotencyConflictError,
    OperationInProgressError,
    OutcomeUnknownError,
    UpstreamRejectedError,
)
from woocommerce_mcp.services.operations import OperationRunner, OperationSpec

pytestmark = pytest.mark.usefixtures("clean_operations")

CREATE = OperationSpec(name="create_thing", resource_type="thing", reexecutable=False)
SET_STATUS = OperationSpec(name="set_status", resource_type="thing", reexecutable=True)


class Thing(BaseModel):
    id: int
    label: str


class Counter:
    def __init__(self, *outcomes: Exception | None) -> None:
        self.calls = 0
        self._outcomes = list(outcomes)

    async def __call__(self) -> Thing:
        self.calls += 1
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        return Thing(id=self.calls, label="created")


@pytest.fixture
async def runner(database_url: str) -> AsyncIterator[OperationRunner]:
    engine = create_engine(
        DatabaseSettings(_env_file=None, url=database_url), application_name="tests"
    )
    yield OperationRunner(
        OperationStore(create_session_factory(engine)),
        retention=timedelta(hours=1),
        in_progress_lease=timedelta(seconds=60),
    )
    await engine.dispose()


async def _run(
    runner: OperationRunner,
    action: Counter,
    *,
    key: str | None = "key-00000001",
    request: dict[str, object] | None = None,
    spec: OperationSpec = CREATE,
) -> Thing:
    return await runner.run(
        spec,
        request=request or {"label": "a"},
        idempotency_key=key,
        result_type=Thing,
        resource_id=lambda t: str(t.id),
        action=action,
    )


async def test_same_key_and_request_replays_the_stored_result(runner: OperationRunner) -> None:
    action = Counter()

    first = await _run(runner, action)
    second = await _run(runner, action)

    assert first == second
    assert action.calls == 1


async def test_same_key_with_different_arguments_is_rejected(runner: OperationRunner) -> None:
    action = Counter()
    await _run(runner, action, request={"label": "a"})

    with pytest.raises(IdempotencyConflictError):
        await _run(runner, action, request={"label": "b"})
    assert action.calls == 1


async def test_writes_without_a_key_always_execute(runner: OperationRunner) -> None:
    action = Counter()

    await _run(runner, action, key=None)
    await _run(runner, action, key=None)

    assert action.calls == 2


async def test_definite_failure_is_executed_again(runner: OperationRunner) -> None:
    action = Counter(UpstreamRejectedError("bad sku"))

    with pytest.raises(UpstreamRejectedError):
        await _run(runner, action)
    result = await _run(runner, action)

    assert result.id == 2
    assert action.calls == 2


async def test_ambiguous_create_is_not_executed_again(runner: OperationRunner) -> None:
    action = Counter(OutcomeUnknownError("maybe applied"))

    with pytest.raises(OutcomeUnknownError):
        await _run(runner, action)
    with pytest.raises(OutcomeUnknownError):
        await _run(runner, action)
    assert action.calls == 1


async def test_ambiguous_reexecutable_write_runs_again(runner: OperationRunner) -> None:
    action = Counter(OutcomeUnknownError("maybe applied"))

    with pytest.raises(OutcomeUnknownError):
        await _run(runner, action, spec=SET_STATUS)
    await _run(runner, action, spec=SET_STATUS)

    assert action.calls == 2


async def test_concurrent_requests_with_one_key_apply_the_write_once(
    runner: OperationRunner,
) -> None:
    release = asyncio.Event()
    calls = 0

    async def slow_action() -> Thing:
        nonlocal calls
        calls += 1
        await release.wait()
        return Thing(id=calls, label="created")

    async def call() -> Thing:
        return await runner.run(
            CREATE,
            request={"label": "a"},
            idempotency_key="key-concurrent",
            result_type=Thing,
            resource_id=lambda t: str(t.id),
            action=slow_action,
        )

    first = asyncio.create_task(call())
    await asyncio.sleep(0.2)  # first request holds the pending record
    with pytest.raises(OperationInProgressError):
        await call()
    release.set()

    assert (await first).id == 1
    assert calls == 1


async def test_purge_removes_only_expired_records(database_url: str) -> None:
    engine = create_engine(
        DatabaseSettings(_env_file=None, url=database_url), application_name="tests"
    )
    store = OperationStore(create_session_factory(engine))
    now = datetime.now(UTC)
    try:
        expired = OperationRunner(
            store,
            retention=timedelta(hours=1),
            in_progress_lease=timedelta(seconds=60),
            clock=lambda: now - timedelta(hours=2),
        )
        current = OperationRunner(
            store, retention=timedelta(hours=1), in_progress_lease=timedelta(seconds=60)
        )
        await _run(expired, Counter(), key="key-expired")
        await _run(current, Counter(), key="key-current")

        assert await current.purge_expired() == 1
        assert await store.get_by_key(CREATE.name, "key-expired") is None
        assert await store.get_by_key(CREATE.name, "key-current") is not None
    finally:
        await engine.dispose()
