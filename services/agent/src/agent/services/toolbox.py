"""Per-run tool discovery through the gateway, and the trusted map from model-facing function
names to gateway tools.

Tools are discovered with the caller's own API key on every run, so the model is offered exactly
what the gateway lets this user call right now (team policy, member grant, key restrictions).
Nothing is hardcoded: any connector the team enables shows up. The model only ever sees function
names; which connector and tool a name maps to is decided here, never by model output.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from agent.config import RunSettings
from agent.errors import AppError
from agent.gateway.client import Caller, GatewayClient, GatewayTool
from agent.llm.base import ToolSpec

logger = logging.getLogger(__name__)

# Function names accepted by the providers we support: start with a letter, then letters,
# digits, underscores, dots or dashes, at most 64 characters.
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_NAME = 64
_MAX_DESCRIPTION = 1024


@dataclass(frozen=True, slots=True)
class ToolBinding:
    function_name: str
    connector: str
    tool: str
    description: str
    input_schema: dict[str, Any]
    validator: Draft202012Validator

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.function_name, description=self.description, parameters=self.input_schema
        )


@dataclass(frozen=True, slots=True)
class Toolbox:
    bindings: dict[str, ToolBinding] = field(default_factory=dict)
    unavailable_connectors: tuple[str, ...] = ()

    def get(self, function_name: str) -> ToolBinding | None:
        return self.bindings.get(function_name)

    @property
    def specs(self) -> list[ToolSpec]:
        return [b.spec for b in self.bindings.values()]


class ToolDiscovery:
    def __init__(self, gateway: GatewayClient, *, settings: RunSettings) -> None:
        self._gateway = gateway
        self._settings = settings

    async def discover(self, caller: Caller) -> Toolbox:
        """Tools the caller may use now. A connector that fails to list is skipped (and named
        in ``unavailable_connectors``) rather than failing the whole run."""
        connectors = await self._gateway.list_connectors(caller)
        results = await asyncio.gather(
            *(self._gateway.list_tools(caller, c.slug) for c in connectors),
            return_exceptions=True,
        )
        bindings: dict[str, ToolBinding] = {}
        unavailable: list[str] = []
        for connector, result in zip(connectors, results, strict=True):
            if isinstance(result, AppError):
                logger.warning(
                    "tool discovery failed for connector",
                    extra={"connector": connector.slug, "code": result.code},
                )
                unavailable.append(connector.slug)
                continue
            if isinstance(result, BaseException):
                raise result
            for tool in sorted(result, key=lambda t: t.name):
                binding = _bind(tool, taken=bindings)
                if binding is None:
                    continue
                if len(bindings) >= self._settings.max_tools:
                    logger.warning("tool limit reached; remaining tools not offered")
                    break
                bindings[binding.function_name] = binding
        return Toolbox(bindings=bindings, unavailable_connectors=tuple(unavailable))


def _bind(tool: GatewayTool, *, taken: dict[str, ToolBinding]) -> ToolBinding | None:
    schema = tool.input_schema
    if schema.get("type", "object") != "object":
        logger.warning(
            "tool skipped: input schema is not an object",
            extra={"connector": tool.connector, "tool": tool.name},
        )
        return None
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        logger.warning(
            "tool skipped: invalid input schema",
            extra={"connector": tool.connector, "tool": tool.name},
        )
        return None
    name = function_name(tool.connector, tool.name)
    if name in taken:
        logger.warning(
            "tool skipped: duplicate function name",
            extra={"connector": tool.connector, "tool": tool.name},
        )
        return None
    description = tool.description or tool.title or tool.name
    return ToolBinding(
        function_name=name,
        connector=tool.connector,
        tool=tool.name,
        description=f"[{tool.connector}] {description}"[:_MAX_DESCRIPTION],
        input_schema={"type": "object", **schema},
        validator=Draft202012Validator(schema),
    )


def function_name(connector: str, tool: str) -> str:
    """``<connector>__<tool>``, made safe for providers; long names keep a hash suffix so they
    stay unique."""
    raw = f"{connector}__{tool}"
    name = _NAME_UNSAFE.sub("_", raw)
    if not name[0].isalpha():
        name = f"t_{name}"
    if len(name) > _MAX_NAME:
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        name = f"{name[: _MAX_NAME - 9]}_{digest}"
    return name
