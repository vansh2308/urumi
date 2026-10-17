"""Request and response bodies of the REST API.

Request models forbid unknown fields, so a client cannot smuggle in a system prompt, a model,
a team or a user id: those come only from configuration and the gateway-verified identity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- identity and tools -------------------------------------------------------------------------


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    team_id: uuid.UUID
    team_name: str
    role: str
    api_key_id: uuid.UUID


class ToolOut(BaseModel):
    name: str = Field(description="Function name the model sees.")
    connector: str
    tool: str
    description: str
    input_schema: dict[str, Any]


class ToolsOut(BaseModel):
    tools: list[ToolOut]
    unavailable_connectors: list[str] = Field(
        description="Connectors whose tools could not be listed right now."
    )


# --- chat -----------------------------------------------------------------------------------------


class ChatRequest(RequestModel):
    message: str = Field(min_length=1, max_length=100_000)
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue this conversation; omit to start a new one."
    )
    stream: bool = Field(
        default=True,
        description="true: text/event-stream of run events. false: one JSON body at the end.",
    )


class ToolCallTrace(BaseModel):
    step: int
    call_id: str
    name: str
    connector: str | None
    tool: str | None
    arguments: Any
    outcome: str | None = None
    ok: bool | None = None
    http_status: int | None = None
    error_code: str | None = None
    latency_ms: int | None = None
    result: dict[str, Any] | None = None


class ChatResponse(BaseModel):
    """The non-streamed equivalent of the event stream."""

    run_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    message: str | None
    tools_available: list[str]
    unavailable_connectors: list[str]
    model_calls: list[dict[str, Any]]
    tool_calls: list[ToolCallTrace]
    usage: dict[str, int]
    latency_ms: int | None
    first_token_ms: int | None
    error: dict[str, Any] | None = None
    request_id: str | None


# --- conversations and runs -----------------------------------------------------------------------


class ConversationOut(ResponseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    busy: bool = Field(description="A message is being answered right now.")


class MessageOut(ResponseModel):
    id: uuid.UUID
    run_id: uuid.UUID | None
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None = Field(
        default=None, description="assistant: the tools the model chose, with arguments."
    )
    tool_call_id: str | None
    tool_name: str | None
    tool_result: dict[str, Any] | None = Field(
        default=None, description="tool: the (possibly truncated) result the model saw."
    )
    created_at: datetime


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class ToolCallLogOut(ResponseModel):
    step: int
    call_id: str
    function_name: str
    connector_slug: str | None
    tool_name: str | None
    outcome: str
    error_code: str | None
    http_status: int | None
    latency_ms: int
    created_at: datetime


class RunOut(ResponseModel):
    id: uuid.UUID
    request_id: str | None
    provider: str
    model: str
    status: str
    error_code: str | None
    tools_available: int
    steps: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int | None
    first_token_ms: int | None
    llm_latency_ms: int
    tool_latency_ms: int
    created_at: datetime
    finished_at: datetime | None
    tool_call_log: list[ToolCallLogOut] = Field(default_factory=list)


# --- usage ----------------------------------------------------------------------------------------


class ToolUsageOut(ResponseModel):
    connector: str | None
    tool: str | None
    calls: int
    failures: int
    avg_latency_ms: float | None


class UsageSummaryOut(ResponseModel):
    scope: str
    since: datetime
    runs: int
    runs_by_status: dict[str, int]
    avg_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    avg_first_token_ms: float | None
    avg_llm_latency_ms: float | None
    avg_steps: float | None
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_calls_by_outcome: dict[str, int]
    tools: list[ToolUsageOut]
