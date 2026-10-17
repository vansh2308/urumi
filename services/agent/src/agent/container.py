"""Composition root: builds the object graph once per process and owns resource lifetimes.

Nothing here is a global; ``create_app`` builds a container and stores it on the application so
request dependencies can reach it. Constructing the engine and HTTP clients is synchronous and
opens no connections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from agent.config import Settings
from agent.db.engine import Database, create_engine, create_session_factory
from agent.gateway.client import GatewayClient, build_gateway_http_client
from agent.llm.base import LlmProvider
from agent.llm.factory import build_provider
from agent.llm.gemini import build_llm_http_client
from agent.metrics import Metrics
from agent.services.conversations import ConversationStore
from agent.services.runner import AgentRunner
from agent.services.tool_executor import ToolExecutor
from agent.services.toolbox import ToolDiscovery
from agent.services.usage import UsageService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    gateway_http: httpx.AsyncClient
    llm_http: httpx.AsyncClient
    db: Database
    metrics: Metrics
    gateway: GatewayClient
    llm: LlmProvider
    conversations: ConversationStore
    tools: ToolDiscovery
    runner: AgentRunner
    usage: UsageService

    async def aclose(self) -> None:
        """Release pooled connections. Called once, after in-flight requests have drained."""
        try:
            await self.gateway_http.aclose()
            await self.llm_http.aclose()
        finally:
            await self.engine.dispose()
        logger.info("resources released")


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings.database, application_name=settings.app.service_name)
    metrics = Metrics()
    gateway_http = build_gateway_http_client(settings.gateway)
    llm_http = build_llm_http_client(settings.llm)
    db = Database(create_session_factory(engine))
    gateway = GatewayClient(gateway_http, settings=settings.gateway, metrics=metrics)
    llm = build_provider(settings, llm_http, metrics)
    conversations = ConversationStore(db, settings=settings.run, metrics=metrics)
    tools = ToolDiscovery(gateway, settings=settings.run)
    return Container(
        settings=settings,
        engine=engine,
        gateway_http=gateway_http,
        llm_http=llm_http,
        db=db,
        metrics=metrics,
        gateway=gateway,
        llm=llm,
        conversations=conversations,
        tools=tools,
        runner=AgentRunner(
            store=conversations,
            discovery=tools,
            executor=ToolExecutor(gateway, settings=settings.run, metrics=metrics),
            llm=llm,
            settings=settings.run,
            llm_settings=settings.llm,
            metrics=metrics,
        ),
        usage=UsageService(db),
    )
