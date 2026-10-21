"""The Kubernetes calls the provisioner makes (implemented by gateway.hosted.kube.KubeApi;
tests use an in-memory fake)."""

from __future__ import annotations

from typing import Any, Protocol


class Kube(Protocol):
    async def apply(
        self, manifest: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any]: ...

    async def create(
        self, manifest: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any] | None: ...

    async def get(
        self, kind: str, name: str, *, namespace: str | None = None
    ) -> dict[str, Any] | None: ...

    async def list(
        self, kind: str, *, label_selector: str = "", namespace: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def delete(self, kind: str, name: str, *, namespace: str | None = None) -> bool: ...

    async def pod_log(self, name: str, *, namespace: str, tail_lines: int = 20) -> str: ...
