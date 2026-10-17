"""Runtime configuration loaded from environment variables (and an optional ``.env`` file).

Settings are split per concern so that tools which only need one part (Alembic only needs the
database) do not require secrets they never use. Everything here is trusted application
configuration: nothing a client or the model sends can change it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_ENV_FILE = ".env"


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore", frozen=True)

    app_env: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    service_name: str = "agent"


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8200, ge=1, le=65535)
    shutdown_grace_seconds: int = Field(
        default=30,
        ge=0,
        description="How long in-flight chats may finish on SIGTERM. Keep it at or above "
        "RUN_TIMEOUT_SECONDS if runs should never be cut short by a deploy.",
    )
    forwarded_allow_ips: str = Field(
        default="127.0.0.1",
        description="Proxies whose X-Forwarded-For is trusted for the client IP.",
    )
    access_log: bool = False
    max_request_body_bytes: int = Field(default=64 * 1024, gt=0)
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Browser origins allowed to call the API (comma separated). Empty disables "
        "CORS.",
    )
    readiness_timeout_seconds: float = Field(default=2.0, gt=0)
    metrics_enabled: bool = Field(
        default=True,
        description="Serve Prometheus metrics at /metrics. Restrict it to the cluster network.",
    )
    docs_enabled: bool = Field(default=True, description="Serve OpenAPI docs at /docs.")

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        return _split_csv(value)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DB_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    url: SecretStr = Field(
        description="PostgreSQL URL, e.g. postgresql+asyncpg://user:pass@host:5432/agent"
    )
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout_seconds: float = Field(default=5.0, gt=0)
    pool_recycle_seconds: int = Field(default=1800, ge=-1)
    statement_timeout_ms: int = Field(default=5000, ge=0)
    echo: bool = False

    @field_validator("url")
    @classmethod
    def _force_asyncpg_driver(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        scheme, sep, rest = raw.partition("://")
        if not sep:
            raise ValueError("DB_URL must be a URL")
        if scheme in {"postgres", "postgresql", "postgresql+asyncpg"}:
            return SecretStr(f"postgresql+asyncpg://{rest}")
        raise ValueError("DB_URL must be a PostgreSQL URL")


class GatewaySettings(BaseSettings):
    """How the agent reaches the gateway. The agent holds no gateway credential of its own:
    every call carries the end user's API key."""

    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    url: str = Field(
        default="http://127.0.0.1:8100",
        description="Base URL of the gateway, e.g. http://gateway.urumi.svc.cluster.local:8100",
    )
    connect_timeout_seconds: float = Field(default=3.0, gt=0)
    request_timeout_seconds: float = Field(
        default=15.0, gt=0, description="Deadline for identity and discovery requests."
    )
    tool_call_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Deadline for one tool invocation. Keep it above the connector's own "
        "timeout_seconds so the gateway reports upstream timeouts itself.",
    )
    max_connections: int = Field(default=100, gt=0)
    max_keepalive_connections: int = Field(default=20, ge=0)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("GATEWAY_URL must be an http(s) URL")
        return value.rstrip("/")


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    provider: Literal["gemini"] = "gemini"
    model: str = Field(default="gemini-2.5-flash", min_length=1, max_length=128)
    request_timeout_seconds: float = Field(
        default=60.0, gt=0, description="Deadline for one model response, streaming included."
    )
    max_output_tokens: int = Field(default=4096, ge=64, le=65536)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries for rate-limited or unavailable responses, only before any output "
        "was received.",
    )


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GEMINI_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    api_key: SecretStr | None = Field(default=None, description="Google AI Studio API key.")
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    @field_validator("api_key")
    @classmethod
    def _blank_is_unset(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        return value


class RunSettings(BaseSettings):
    """Bounds on one agent run (one chat request) and on conversation context."""

    model_config = SettingsConfigDict(
        env_prefix="RUN_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    max_steps: int = Field(
        default=8, ge=1, le=50, description="Model responses per run (each may call tools)."
    )
    max_tool_calls: int = Field(default=12, ge=0, le=100, description="Tool calls per run.")
    max_parallel_tool_calls: int = Field(
        default=5, ge=1, le=20, description="Tool calls the model may request in one response."
    )
    timeout_seconds: float = Field(default=120.0, gt=0, description="Wall clock per run.")
    max_tokens: int = Field(
        default=200_000,
        ge=1000,
        description="Input plus output tokens summed over every model call of a run.",
    )
    max_message_chars: int = Field(default=8000, ge=1, le=100_000)
    history_max_messages: int = Field(
        default=40, ge=2, le=500, description="Earlier messages sent to the model as context."
    )
    max_tool_result_chars: int = Field(
        default=12_000,
        ge=500,
        description="Tool output beyond this is truncated before the model sees or we store it.",
    )
    max_tools: int = Field(default=64, ge=1, le=512, description="Tools offered to the model.")
    rate_limit_per_minute: int = Field(
        default=20, ge=1, description="Runs per user per team per minute."
    )
    conversation_lock_seconds: int = Field(
        default=300,
        ge=10,
        description="A conversation is locked while a run is active; a lock older than this "
        "(a crashed replica) is taken over.",
    )


class Settings:
    """Aggregate of every settings group, built once at process start and injected."""

    def __init__(
        self,
        *,
        app: AppSettings | None = None,
        server: ServerSettings | None = None,
        database: DatabaseSettings | None = None,
        gateway: GatewaySettings | None = None,
        llm: LlmSettings | None = None,
        gemini: GeminiSettings | None = None,
        run: RunSettings | None = None,
    ) -> None:
        self.app = app or AppSettings()
        self.server = server or ServerSettings()
        self.database = database or DatabaseSettings()
        self.gateway = gateway or GatewaySettings()
        self.llm = llm or LlmSettings()
        self.gemini = gemini or GeminiSettings()
        self.run = run or RunSettings()
        if self.llm.provider == "gemini" and self.gemini.api_key is None:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        if self.run.conversation_lock_seconds <= self.run.timeout_seconds:
            raise ValueError("RUN_CONVERSATION_LOCK_SECONDS must exceed RUN_TIMEOUT_SECONDS")
        if self.app.app_env is Environment.PRODUCTION and "*" in self.server.cors_allowed_origins:
            raise ValueError("AGENT_CORS_ALLOWED_ORIGINS must list origins in production")
