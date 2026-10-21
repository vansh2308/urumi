"""Objects the provisioner creates itself, around the Helm release: the namespace with its
guardrails (quota, limits, default-deny, the RoleBinding Helm works under) and the Secrets.

Everything else in a store namespace comes from the ``urumi-store`` chart (values.py).
"""

from __future__ import annotations

import base64
import secrets
from typing import Any

from gateway.config import ProvisionerSettings
from gateway.db.models import Store

MANAGED_BY = "urumi.io/managed-by"
MANAGED_BY_VALUE = "provisioner"
STORE_LABEL = "urumi.io/store"
CREDENTIALS_SECRET = "store-credentials"  # noqa: S105 - a Secret name
MCP_SECRET = "store-mcp"  # noqa: S105 - a Secret name

# Fits the Bitnami defaults of one store with some room (docs/stores.md): WordPress, MariaDB,
# the MCP server, its rolling update and the setup Job.
QUOTA: dict[str, str] = {
    "pods": "6",
    "requests.cpu": "1",
    "requests.memory": "1536Mi",
    "limits.memory": "3Gi",
    "persistentvolumeclaims": "2",
    "requests.storage": "6Gi",
    "services": "5",
    "secrets": "10",
}
LIMITS_DEFAULT = {"cpu": "250m", "memory": "256Mi"}
LIMITS_DEFAULT_REQUEST = {"cpu": "50m", "memory": "64Mi"}
LIMITS_MAX = {"memory": "1Gi"}


def labels(store: Store) -> dict[str, str]:
    return {
        MANAGED_BY: MANAGED_BY_VALUE,
        STORE_LABEL: str(store.id),
        "urumi.io/store-slug": store.slug,
        "app.kubernetes.io/part-of": "urumi-store",
    }


def namespace(store: Store) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": store.namespace,
            "labels": labels(store)
            | {
                # WordPress and MariaDB meet "baseline"; "restricted" is reported as warnings.
                "pod-security.kubernetes.io/enforce": "baseline",
                "pod-security.kubernetes.io/enforce-version": "latest",
                "pod-security.kubernetes.io/warn": "restricted",
            },
        },
    }


def role_binding(store: Store, settings: ProvisionerSettings) -> dict[str, Any]:
    """Lets the provisioner (and so Helm) manage objects in this namespace only."""
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {
            "name": "urumi-provisioner",
            "namespace": store.namespace,
            "labels": labels(store),
        },
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": settings.store_cluster_role,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": settings.service_account_name,
                "namespace": settings.service_account_namespace,
            }
        ],
    }


def resource_quota(store: Store) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {"name": "store-quota", "namespace": store.namespace, "labels": labels(store)},
        "spec": {"hard": QUOTA},
    }


def limit_range(store: Store) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "LimitRange",
        "metadata": {"name": "store-limits", "namespace": store.namespace, "labels": labels(store)},
        "spec": {
            "limits": [
                {
                    "type": "Container",
                    "default": LIMITS_DEFAULT,
                    "defaultRequest": LIMITS_DEFAULT_REQUEST,
                    "max": LIMITS_MAX,
                }
            ]
        },
    }


def default_deny(store: Store) -> dict[str, Any]:
    """In place before any pod exists; the chart adds the allow rules (docs/stores.md)."""
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "default-deny", "namespace": store.namespace, "labels": labels(store)},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
    }


def _secret(store: Store, name: str, values: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": name, "namespace": store.namespace, "labels": labels(store)},
        "data": {k: base64.b64encode(v.encode()).decode() for k, v in values.items()},
    }


def credentials_secret(store: Store, admin_password: str | None) -> dict[str, Any]:
    return _secret(
        store,
        CREDENTIALS_SECRET,
        {
            "wordpress-password": admin_password or secrets.token_urlsafe(18),
            "mariadb-root-password": secrets.token_urlsafe(24),
            "mariadb-password": secrets.token_urlsafe(24),
            "mariadb-replication-password": secrets.token_urlsafe(24),
        },
    )


def new_db_password() -> str:
    return secrets.token_urlsafe(30)


def mcp_secret(store: Store, db_url: str) -> dict[str, Any]:
    # WooCommerce REST credentials: the setup Job stores wc_api_hash(ck) and cs in WordPress,
    # so nothing ever has to be read back from the store.
    return _secret(
        store,
        MCP_SECRET,
        {
            "WC_CONSUMER_KEY": "ck_" + secrets.token_hex(20),
            "WC_CONSUMER_SECRET": "cs_" + secrets.token_hex(20),
            "DB_URL": db_url,
        },
    )


def secret_value(secret: dict[str, Any], key: str) -> str | None:
    raw = (secret.get("data") or {}).get(key)
    return base64.b64decode(raw).decode() if raw else None
