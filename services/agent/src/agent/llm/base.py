"""Provider-neutral model interface.

The agent speaks only these types. A provider adapter translates them to its wire format and
returns an :class:`LlmResponse`, streaming answer text through a callback. Adding a provider
means writing one adapter and registering it in :func:`agent.llm.factory.build_provider`,
without touching the agent loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A function the model may call. ``parameters`` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Any  # whatever the model produced; validated by the agent before use
    # Opaque data the provider needs echoed back with this call (e.g. Gemini thought
    # signatures); persisted with the conversation, never interpreted by the agent.
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    # role == "tool": the call this answers and the JSON object returned to the model
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LlmRequest:
    system: str
    messages: Sequence[ChatMessage]
    tools: Sequence[ToolSpec]
    max_output_tokens: int
    temperature: float


class FinishReason(StrEnum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    BLOCKED = "blocked"  # safety or policy filter
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class LlmResponse:
    """One complete model response: answer text and/or the tool calls the model requested."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: FinishReason
    input_tokens: int
    output_tokens: int


# Receives answer text as it streams in, so the API can forward it before the response ends.
TextSink = Callable[[str], Awaitable[None]]


class LlmProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def generate(self, request: LlmRequest, on_text: TextSink) -> LlmResponse:
        """Run one model response, streaming answer text to ``on_text``. Raises
        :class:`agent.errors.LlmError` subclasses with client-safe messages."""
        ...
