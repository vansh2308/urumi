"""Helm, driven as a subprocess with a pinned binary and a chart baked into the image.

Every call is short: ``upgrade --install`` never waits for workloads (the provisioner polls
readiness itself), so a provisioner killed mid-call leaves at most a ``pending-*`` release,
which :meth:`HelmRunner.recover` clears before the next attempt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

PENDING = frozenset({"pending-install", "pending-upgrade", "pending-rollback"})


class HelmError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class ReleaseStatus:
    status: str  # deployed | failed | pending-install | pending-upgrade | uninstalling | ...
    revision: int


class Helm(Protocol):
    async def status(self, release: str, namespace: str) -> ReleaseStatus | None: ...

    async def upgrade_install(
        self, release: str, namespace: str, values: dict[str, Any]
    ) -> ReleaseStatus: ...

    async def rollback(self, release: str, namespace: str) -> None: ...

    async def uninstall(self, release: str, namespace: str) -> bool: ...


async def recover(helm: Helm, release: str, namespace: str) -> ReleaseStatus | None:
    """Clear a release Helm left half-done (process killed mid-call): a first install is
    uninstalled, an interrupted upgrade or rollback is rolled back. Returns the status after."""
    current = await helm.status(release, namespace)
    if current is None or current.status not in PENDING:
        return current
    logger.warning(
        "recovering interrupted helm release",
        extra={"namespace": namespace, "status": current.status, "revision": current.revision},
    )
    if current.status == "pending-install" or current.revision <= 1:
        await helm.uninstall(release, namespace)
        return None
    await helm.rollback(release, namespace)
    return await helm.status(release, namespace)


class HelmRunner:
    def __init__(self, *, binary: str, chart: str, timeout_seconds: int) -> None:
        self._binary = binary
        self._chart = chart
        self._timeout = timeout_seconds
        # Helm writes caches and config; the image's filesystem is read-only.
        scratch = Path(tempfile.gettempdir()) / "helm"
        self._env = os.environ | {
            "HELM_CACHE_HOME": str(scratch / "cache"),
            "HELM_CONFIG_HOME": str(scratch / "config"),
            "HELM_DATA_HOME": str(scratch / "data"),
        }

    async def status(self, release: str, namespace: str) -> ReleaseStatus | None:
        code, out, err = await self._run("status", release, "-n", namespace, "-o", "json")
        if code != 0:
            if "not found" in err.lower():
                return None
            raise HelmError(_last_line(err) or "helm status failed")
        info = json.loads(out)
        return ReleaseStatus(status=str(info["info"]["status"]), revision=int(info["version"]))

    async def upgrade_install(
        self, release: str, namespace: str, values: dict[str, Any]
    ) -> ReleaseStatus:
        with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="store-values-") as handle:
            # JSON is valid YAML; the values hold no secrets (those live in Secrets).
            json.dump(values, handle)
            handle.flush()
            code, _, err = await self._run(
                "upgrade",
                "--install",
                release,
                self._chart,
                "-n",
                namespace,
                "-f",
                handle.name,
                "--history-max",
                "3",
                "--timeout",
                f"{self._timeout}s",
            )
        if code != 0:
            raise HelmError(_last_line(err) or "helm upgrade failed")
        status = await self.status(release, namespace)
        if status is None:
            raise HelmError("the release is missing right after helm upgrade")
        return status

    async def rollback(self, release: str, namespace: str) -> None:
        code, _, err = await self._run("rollback", release, "-n", namespace)
        if code != 0:
            raise HelmError(_last_line(err) or "helm rollback failed")

    async def uninstall(self, release: str, namespace: str) -> bool:
        code, _, err = await self._run("uninstall", release, "-n", namespace)
        if code != 0:
            if "not found" in err.lower():
                return False
            raise HelmError(_last_line(err) or "helm uninstall failed")
        return True

    async def _run(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            self._binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), self._timeout + 30)
        except TimeoutError:
            process.kill()
            await process.wait()
            return 1, "", f"helm {args[0]} timed out"
        return process.returncode or 0, out.decode(), err.decode()


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""
