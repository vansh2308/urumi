"""Prometheus metrics, held in a registry owned by the container (no module-level globals).

Label values are bounded: HTTP routes use the route template, tool labels carry only names the
gateway returned for the caller (never names the model invented), outcomes and statuses are
fixed vocabularies.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
_RUN_BUCKETS = (0.5, 1, 2, 4, 8, 15, 30, 60, 120, 300)

CONTENT_TYPE = CONTENT_TYPE_LATEST


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.http_requests = Counter(
            "agent_http_requests_total",
            "HTTP requests by route template and status code.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.http_latency = Histogram(
            "agent_http_request_duration_seconds",
            "HTTP request latency by route template (a streamed chat counts until it ends).",
            ["method", "route"],
            buckets=_RUN_BUCKETS,
            registry=self.registry,
        )
        self.runs = Counter(
            "agent_runs_total",
            "Agent runs (one per chat request) by final status.",
            ["status"],
            registry=self.registry,
        )
        self.run_latency = Histogram(
            "agent_run_duration_seconds",
            "Wall clock of an agent run from request to final answer.",
            buckets=_RUN_BUCKETS,
            registry=self.registry,
        )
        self.first_token_latency = Histogram(
            "agent_first_token_seconds",
            "Time from request to the first streamed answer text.",
            buckets=_RUN_BUCKETS,
            registry=self.registry,
        )
        self.llm_requests = Counter(
            "agent_llm_requests_total",
            "Model calls by provider, model and outcome.",
            ["provider", "model", "outcome"],
            registry=self.registry,
        )
        self.llm_latency = Histogram(
            "agent_llm_request_duration_seconds",
            "Latency of one model call, streaming included.",
            ["provider", "model"],
            buckets=_RUN_BUCKETS,
            registry=self.registry,
        )
        self.llm_tokens = Counter(
            "agent_llm_tokens_total",
            "Tokens used by direction (input, output).",
            ["provider", "model", "direction"],
            registry=self.registry,
        )
        self.tool_calls = Counter(
            "agent_tool_calls_total",
            "Tool calls requested by the model, by outcome.",
            ["connector", "tool", "outcome"],
            registry=self.registry,
        )
        self.tool_latency = Histogram(
            "agent_tool_call_duration_seconds",
            "Latency of a tool call through the gateway.",
            ["connector"],
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.gateway_failures = Counter(
            "agent_gateway_failures_total",
            "Failed requests to the gateway by operation and reason.",
            ["operation", "reason"],
            registry=self.registry,
        )
        self.rate_limited = Counter(
            "agent_rate_limited_total",
            "Chat requests rejected by the per-user run limit.",
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
