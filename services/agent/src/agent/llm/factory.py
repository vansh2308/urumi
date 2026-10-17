"""Selects the model provider from configuration. Register new adapters here."""

from __future__ import annotations

import httpx

from agent.config import Settings
from agent.llm.base import LlmProvider
from agent.llm.gemini import GeminiProvider
from agent.metrics import Metrics


def build_provider(settings: Settings, http: httpx.AsyncClient, metrics: Metrics) -> LlmProvider:
    match settings.llm.provider:
        case "gemini":
            return GeminiProvider(http, llm=settings.llm, gemini=settings.gemini, metrics=metrics)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm.provider}")
