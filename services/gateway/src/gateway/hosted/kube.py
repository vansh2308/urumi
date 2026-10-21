"""Minimal async client for the Kubernetes API.

It covers what hosted MCP servers and the store provisioner need (server-side apply, create,
merge patch, get, list, delete) over the pooled ``httpx.AsyncClient`` it is given,
authenticating with the pod's ServiceAccount token. The token file is re-read periodically
because projected tokens rotate. Calls default to the configured namespace; the provisioner
passes ``namespace=`` per store. What a caller may do is decided by its RBAC, not here.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

from gateway.errors import AppError

logger = logging.getLogger(__name__)

FIELD_MANAGER = "urumi-gateway"
_TOKEN_REFRESH_SECONDS = 60.0

# kind -> (API path prefix, plural resource name)
_RESOURCES: dict[str, tuple[str, str]] = {
    "Secret": ("/api/v1", "secrets"),
    "Service": ("/api/v1", "services"),
    "Pod": ("/api/v1", "pods"),
    "Event": ("/api/v1", "events"),
    "ResourceQuota": ("/api/v1", "resourcequotas"),
    "LimitRange": ("/api/v1", "limitranges"),
    "PersistentVolumeClaim": ("/api/v1", "persistentvolumeclaims"),
    "Deployment": ("/apis/apps/v1", "deployments"),
    "StatefulSet": ("/apis/apps/v1", "statefulsets"),
    "Job": ("/apis/batch/v1", "jobs"),
    "NetworkPolicy": ("/apis/networking.k8s.io/v1", "networkpolicies"),
    "RoleBinding": ("/apis/rbac.authorization.k8s.io/v1", "rolebindings"),
}
# Cluster-scoped kinds (no namespace in the path).
_CLUSTER_RESOURCES: dict[str, tuple[str, str]] = {
    "Namespace": ("/api/v1", "namespaces"),
}


class KubeConnection(Protocol):
    """Where the API server is and how long to wait (HostedSettings, ProvisionerSettings)."""

    @property
    def namespace(self) -> str: ...
    @property
    def api_url(self) -> str: ...
    @property
    def service_account_dir(self) -> str: ...
    @property
    def request_timeout_seconds(self) -> float: ...


class ClusterError(AppError):
    """The Kubernetes API refused or failed a request."""

    code = "cluster_error"
    status_code = 502

    def __init__(self, public_message: str, *, status: int | None = None) -> None:
        super().__init__(public_message)
        self.status = status


class KubeApi:
    def __init__(self, http_client: httpx.AsyncClient, settings: KubeConnection) -> None:
        self._http = http_client
        self._settings = settings
        self._token_path = Path(settings.service_account_dir) / "token"
        self._token: tuple[float, str] | None = None

    @property
    def namespace(self) -> str:
        return self._settings.namespace

    async def apply(
        self, manifest: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any]:
        """Create or update an object (server-side apply). Idempotent, so safe to retry."""
        name = manifest["metadata"]["name"]
        return await self._request(
            "PATCH",
            self._path(manifest["kind"], name, namespace),
            params={"fieldManager": FIELD_MANAGER, "force": "true"},
            content=json.dumps(manifest),
            content_type="application/apply-patch+yaml",
        )

    async def create(
        self, manifest: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any] | None:
        """Create an object only if it does not exist; None if it already did (never
        overwrites, e.g. a password a database was already initialised with)."""
        try:
            return await self._request(
                "POST",
                self._path(manifest["kind"], None, namespace),
                content=json.dumps(manifest),
                content_type="application/json",
            )
        except ClusterError as exc:
            if exc.status == 409:
                return None
            raise

    async def merge_patch(
        self, kind: str, name: str, patch: dict[str, Any], *, namespace: str | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH",
            self._path(kind, name, namespace),
            content=json.dumps(patch),
            content_type="application/merge-patch+json",
        )

    async def get(
        self, kind: str, name: str, *, namespace: str | None = None
    ) -> dict[str, Any] | None:
        try:
            return await self._request("GET", self._path(kind, name, namespace))
        except ClusterError as exc:
            if exc.status == 404:
                return None
            raise

    async def list(
        self, kind: str, *, label_selector: str = "", namespace: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"labelSelector": label_selector} if label_selector else None
        body = await self._request("GET", self._path(kind, None, namespace), params=params)
        items: list[dict[str, Any]] = body.get("items", [])
        return items

    async def delete(self, kind: str, name: str, *, namespace: str | None = None) -> bool:
        """Delete an object; False if it did not exist."""
        try:
            await self._request(
                "DELETE",
                self._path(kind, name, namespace),
                content=json.dumps({"propagationPolicy": "Background"}),
                content_type="application/json",
            )
        except ClusterError as exc:
            if exc.status == 404:
                return False
            raise
        return True

    async def pod_log(self, name: str, *, namespace: str, tail_lines: int = 20) -> str:
        """The last lines of a pod's log (for failure reasons); empty if unavailable."""
        try:
            response = await self._http.get(
                self._settings.api_url.rstrip("/") + self._path("Pod", name, namespace) + "/log",
                params={"tailLines": str(tail_lines)},
                headers={"Authorization": f"Bearer {self._bearer()}"},
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.HTTPError:
            return ""
        return response.text if response.status_code == 200 else ""

    def _path(self, kind: str, name: str | None = None, namespace: str | None = None) -> str:
        if kind in _CLUSTER_RESOURCES:
            prefix, plural = _CLUSTER_RESOURCES[kind]
            path = f"{prefix}/{plural}"
        else:
            prefix, plural = _RESOURCES[kind]
            path = f"{prefix}/namespaces/{namespace or self.namespace}/{plural}"
        return f"{path}/{name}" if name else path

    def _bearer(self) -> str:
        now = time.monotonic()
        if self._token is None or self._token[0] < now:
            try:
                token = self._token_path.read_text().strip()
            except OSError as exc:
                raise ClusterError(
                    "The gateway has no Kubernetes ServiceAccount token; hosted servers need "
                    "the gateway to run in the cluster."
                ) from exc
            self._token = (now + _TOKEN_REFRESH_SECONDS, token)
        return self._token[1]

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        content: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._bearer()}", "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            response = await self._http.request(
                method,
                self._settings.api_url.rstrip("/") + path,
                params=params,
                content=content,
                headers=headers,
                timeout=self._settings.request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            logger.warning("kubernetes api unreachable", extra={"error": type(exc).__name__})
            raise ClusterError("The Kubernetes API is unreachable.") from exc
        if response.status_code >= 400:
            raise ClusterError(_status_message(response), status=response.status_code)
        if not response.content:
            return {}
        body: dict[str, Any] = response.json()
        return body


def _status_message(response: httpx.Response) -> str:
    """The API's own Status message (e.g. 'exceeded quota ...'); it never carries secrets."""
    try:
        message = str(response.json().get("message", ""))
    except ValueError:
        message = ""
    return f"Kubernetes API error (HTTP {response.status_code}): {message[:300]}".rstrip(": ")


def build_kube_http_client(settings: KubeConnection) -> httpx.AsyncClient:
    ca_file = Path(settings.service_account_dir) / "ca.crt"
    verify: ssl.SSLContext | bool = (
        ssl.create_default_context(cafile=str(ca_file)) if ca_file.exists() else True
    )
    return httpx.AsyncClient(
        verify=verify,
        timeout=settings.request_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )
