"""Prometheus metrics, held in a registry owned by the container (no module-level globals).

Label values are bounded: HTTP routes use the route template (never the raw path) and tool labels
only carry names of registered connectors and tools that passed authorization.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)

CONTENT_TYPE = CONTENT_TYPE_LATEST


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "gateway_http_requests_total",
            "HTTP requests by route template and status code.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "gateway_http_request_duration_seconds",
            "HTTP request latency by route template.",
            ["method", "route"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.tool_invocations = Counter(
            "gateway_tool_invocations_total",
            "Authorized tool invocations by connector, tool and outcome.",
            ["connector", "tool", "outcome"],
            registry=self.registry,
        )
        self.tool_latency = Histogram(
            "gateway_tool_invocation_duration_seconds",
            "Tool invocation latency including the upstream call.",
            ["connector"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.upstream_failures = Counter(
            "gateway_upstream_failures_total",
            "Failures talking to upstream MCP servers by reason.",
            ["connector", "reason"],
            registry=self.registry,
        )
        self.rate_limited = Counter(
            "gateway_rate_limited_total",
            "Requests rejected by rate limiting.",
            ["scope"],
            registry=self.registry,
        )
        self.auth_failures = Counter(
            "gateway_auth_failures_total",
            "Rejected credentials by kind.",
            ["kind"],
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
