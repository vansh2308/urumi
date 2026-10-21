"""Helm values of one store release (no secrets: those are in the namespace's Secrets)."""

from __future__ import annotations

from typing import Any

from gateway.config import ProvisionerSettings, StoreSettings
from gateway.db.models import Store
from gateway.provisioner.manifests import CREDENTIALS_SECRET, MCP_SECRET


def split_image(image: str) -> dict[str, str]:
    """``registry/repo:tag`` or ``registry/repo@sha256:..`` into Bitnami's image fields."""
    digest = ""
    if "@" in image:
        image, digest = image.split("@", 1)
        tag = ""
    else:
        slash = image.rfind("/")
        colon = image.rfind(":")
        tag = image[colon + 1 :] if colon > slash else "latest"
        image = image[:colon] if colon > slash else image
    first, _, rest = image.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = "docker.io", image
    return {"registry": registry, "repository": repository, "tag": tag, "digest": digest}


def store_values(
    store: Store,
    *,
    settings: ProvisionerSettings,
    stores: StoreSettings,
    setup_run: str | None = None,
) -> dict[str, Any]:
    scheme = "https" if stores.tls else "http"
    wp_image = split_image(settings.store_image)
    mcp_image = split_image(settings.mcp_image)
    gw_key, gw_value = settings.label_pair(settings.gateway_pod_label)
    ing_key, ing_value = settings.label_pair(settings.ingress_pod_label)
    ns = store.namespace
    ingress: dict[str, Any] = {
        "enabled": True,
        "ingressClassName": settings.ingress_class,
        "hostname": store.host,
        "tls": stores.tls,
    }
    if stores.tls and settings.tls_cluster_issuer:
        ingress["annotations"] = {"cert-manager.io/cluster-issuer": settings.tls_cluster_issuer}
    wp_persistence: dict[str, Any] = {"enabled": True, "size": settings.wordpress_volume}
    db_persistence: dict[str, Any] = {"enabled": True, "size": settings.mariadb_volume}
    if settings.storage_class:
        wp_persistence["storageClass"] = settings.storage_class
        db_persistence["storageClass"] = settings.storage_class
    mcp_image_values: dict[str, Any] = {
        "repository": "/".join(
            p for p in (mcp_image["registry"], mcp_image["repository"]) if p != "docker.io"
        )
    }
    if mcp_image["digest"]:
        mcp_image_values["digest"] = mcp_image["digest"]
    else:
        mcp_image_values["tag"] = mcp_image["tag"]
    mcp_image_values["pullPolicy"] = settings.image_pull_policy
    return {
        "store": {"name": store.name, "slug": store.slug, "host": store.host},
        "secrets": {"credentials": CREDENTIALS_SECRET, "mcp": MCP_SECRET},
        "platform": {
            "namespace": settings.gateway_namespace,
            "gatewayPodLabels": {gw_key: gw_value},
        },
        "networkPolicies": {
            "ingressController": {
                "namespace": settings.ingress_namespace,
                "podLabels": {ing_key: ing_value},
            }
        },
        "setup": {"runId": setup_run or "", "seedDemo": settings.seed_demo_products},
        "wordpress": {
            "image": wp_image | {"pullPolicy": settings.image_pull_policy},
            "wordpressHost": store.host,
            "wordpressScheme": scheme,
            "wordpressBlogName": store.name,
            "existingSecret": CREDENTIALS_SECRET,
            "ingress": ingress,
            "persistence": wp_persistence,
            "mariadb": {
                "auth": {"existingSecret": CREDENTIALS_SECRET},
                "primary": {"persistence": db_persistence},
            },
        },
        "store-mcp": {
            "image": mcp_image_values,
            "env": {
                "APP_ENV": settings.store_app_env.value,
                "STORE_NAME": store.name,
                # Signed URL + Host header: what WordPress sees as its own address. The REST
                # call itself stays inside the cluster (plain HTTP to the Service).
                "WC_BASE_URL": f"http://{store.host}",
                "WC_CONNECT_URL": f"http://store-wordpress.{ns}.svc.cluster.local",
                "WC_ALLOW_INSECURE_HTTP": "true",
                "MCP_ALLOWED_HOSTS": ",".join(
                    [
                        "store-mcp:*",
                        f"store-mcp.{ns}:*",
                        f"store-mcp.{ns}.svc:*",
                        f"store-mcp.{ns}.svc.cluster.local:*",
                        "localhost:*",
                        "127.0.0.1:*",
                    ]
                ),
            },
            "secretEnv": [
                {"name": "WC_CONSUMER_KEY", "secretName": MCP_SECRET},
                {"name": "WC_CONSUMER_SECRET", "secretName": MCP_SECRET},
                {"name": "DB_URL", "secretName": MCP_SECRET},
            ],
        },
    }
