"""Kubernetes objects for one hosted MCP server, built from its spec. Pure functions.

Every hosted server gets four objects in the hosted namespace, all named after it and labelled
so they can be found and removed together:

* ``Secret`` ``<name>-env``: the server's secret environment variables (values live only here).
* ``Deployment`` ``<name>``: one non-root, read-only pod with CPU/memory requests and limits,
  no Kubernetes API token, and TCP probes on the MCP port.
* ``Service`` ``<name>``: ClusterIP on the MCP port; the gateway's connector URL points at it.
* ``NetworkPolicy`` ``<name>``: ingress only from gateway pods; egress only DNS and, if allowed,
  the public internet (never cluster or private addresses, so not the store or its database).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

MANAGED_BY = "urumi-gateway"
COMPONENT = "hosted-mcp"
CONNECTOR_LABEL = "urumi.dev/connector-id"
SECRET_SUFFIX = "-env"  # noqa: S105 - a name suffix, not a secret
PRIVATE_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10"]
LINK_LOCAL = "169.254.0.0/16"  # cloud metadata endpoints live here
POD_TMP = "/tmp"  # noqa: S108 - the pod's own emptyDir, not a shared host directory

Egress = Literal["internet", "none"]


@dataclass(frozen=True, slots=True)
class HostedSpec:
    connector_id: uuid.UUID
    name: str
    image: str
    port: int
    args: tuple[str, ...]
    env: dict[str, str]
    cpu_millicores: int
    memory_mib: int
    egress: Egress
    replicas: int = 1
    # Changes the pod template so pods restart and pick up a changed Secret.
    config_revision: str = field(default="")


@dataclass(frozen=True, slots=True)
class ClusterPolicy:
    namespace: str
    gateway_namespace: str
    gateway_pod_label: tuple[str, str]
    run_as_user: int


def secret_name(name: str) -> str:
    return name + SECRET_SUFFIX


def labels(spec_name: str, connector_id: uuid.UUID) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": spec_name,
        "app.kubernetes.io/component": COMPONENT,
        "app.kubernetes.io/managed-by": MANAGED_BY,
        CONNECTOR_LABEL: str(connector_id),
    }


def selector(name: str) -> dict[str, str]:
    return {"app.kubernetes.io/name": name, "app.kubernetes.io/component": COMPONENT}


def label_selector(name: str) -> str:
    return ",".join(f"{k}={v}" for k, v in selector(name).items())


def _meta(spec: HostedSpec, policy: ClusterPolicy, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "namespace": policy.namespace,
        "labels": labels(spec.name, spec.connector_id),
    }


def secret(spec: HostedSpec, policy: ClusterPolicy, values: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(spec, policy, secret_name(spec.name)),
        "type": "Opaque",
        "stringData": values,
    }


def deployment(spec: HostedSpec, policy: ClusterPolicy) -> dict[str, Any]:
    cpu_request = max(10, spec.cpu_millicores // 4)
    memory_request = max(16, spec.memory_mib // 2)
    probe = {"tcpSocket": {"port": "mcp"}}
    container: dict[str, Any] = {
        "name": "mcp",
        "image": spec.image,
        "imagePullPolicy": "IfNotPresent",
        "ports": [{"name": "mcp", "containerPort": spec.port, "protocol": "TCP"}],
        # HOME/TMPDIR point at the only writable directory: the root filesystem is read-only.
        "env": [{"name": "HOME", "value": POD_TMP}, {"name": "TMPDIR", "value": POD_TMP}]
        + [{"name": k, "value": v} for k, v in sorted(spec.env.items())],
        "envFrom": [{"secretRef": {"name": secret_name(spec.name), "optional": True}}],
        "resources": {
            "requests": {"cpu": f"{cpu_request}m", "memory": f"{memory_request}Mi"},
            "limits": {"cpu": f"{spec.cpu_millicores}m", "memory": f"{spec.memory_mib}Mi"},
        },
        "startupProbe": probe | {"periodSeconds": 2, "failureThreshold": 60},
        "readinessProbe": probe | {"periodSeconds": 10, "failureThreshold": 3},
        "livenessProbe": probe | {"periodSeconds": 20, "failureThreshold": 3},
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [{"name": "tmp", "mountPath": POD_TMP}],
    }
    if spec.args:
        container["args"] = list(spec.args)
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(spec, policy, spec.name),
        "spec": {
            "replicas": spec.replicas,
            "revisionHistoryLimit": 3,
            "progressDeadlineSeconds": 300,
            "selector": {"matchLabels": selector(spec.name)},
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            },
            "template": {
                "metadata": {
                    "labels": labels(spec.name, spec.connector_id),
                    "annotations": {"urumi.dev/config-revision": spec.config_revision},
                },
                "spec": {
                    "automountServiceAccountToken": False,
                    "enableServiceLinks": False,
                    "terminationGracePeriodSeconds": 20,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": policy.run_as_user,
                        "runAsGroup": policy.run_as_user,
                        "fsGroup": policy.run_as_user,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                    "volumes": [{"name": "tmp", "emptyDir": {"sizeLimit": "64Mi"}}],
                },
            },
        },
    }


def service(spec: HostedSpec, policy: ClusterPolicy) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(spec, policy, spec.name),
        "spec": {
            "type": "ClusterIP",
            "selector": selector(spec.name),
            "ports": [{"name": "mcp", "port": spec.port, "targetPort": "mcp", "protocol": "TCP"}],
        },
    }


def network_policy(spec: HostedSpec, policy: ClusterPolicy) -> dict[str, Any]:
    label_key, label_value = policy.gateway_pod_label
    egress: list[dict[str, Any]] = [
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                    },
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                }
            ],
            "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
        }
    ]
    if spec.egress == "internet":
        egress.append(
            {
                "to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": [*PRIVATE_RANGES, LINK_LOCAL]}}],
                "ports": [{"port": 443, "protocol": "TCP"}, {"port": 80, "protocol": "TCP"}],
            }
        )
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": _meta(spec, policy, spec.name),
        "spec": {
            "podSelector": {"matchLabels": selector(spec.name)},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": policy.gateway_namespace
                                }
                            },
                            "podSelector": {"matchLabels": {label_key: label_value}},
                        }
                    ],
                    "ports": [{"port": spec.port, "protocol": "TCP"}],
                }
            ],
            "egress": egress,
        },
    }


def service_url(name: str, namespace: str, port: int, path: str) -> str:
    return f"http://{name}.{namespace}.svc.cluster.local:{port}{path}"
