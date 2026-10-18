"""Runtime configuration loaded from environment variables (and an optional ``.env`` file).

Settings are split per concern so that tools which only need one part (for example Alembic,
which only needs the database) do not require WooCommerce credentials to be present.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_ENV_FILE = ".env"


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class WooCommerceAuthMethod(StrEnum):
    """How requests to the WooCommerce REST API are authenticated.

    WooCommerce only accepts OAuth 1.0a (one-legged, query-string signed) over plain HTTP and
    only accepts HTTP Basic auth with the consumer key/secret over HTTPS, so the method is
    derived from the store URL scheme rather than configured separately.
    """

    OAUTH1 = "oauth1"
    BASIC = "basic"


class WooCommerceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WC_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    base_url: str = Field(
        description=(
            "Store root URL exactly as configured in WordPress 'home' "
            "(e.g. http://wordpress.local). OAuth1 signatures are computed against it."
        )
    )
    consumer_key: SecretStr
    consumer_secret: SecretStr
    api_path: str = "/wp-json/wc/v3"
    allow_insecure_http: bool = Field(
        default=False,
        description="Permit a plain-HTTP store URL (OAuth1). Only for local/private networks.",
    )
    oauth_signature_method: Literal["HMAC-SHA256", "HMAC-SHA1"] = "HMAC-SHA256"
    verify_tls: bool = True
    ip_family: Literal["auto", "ipv4", "ipv6"] = Field(
        default="auto",
        description="Restrict DNS resolution/connections to one IP family. 'ipv4' avoids the "
        "~5 s IPv6 (AAAA) mDNS lookup macOS does for *.local names listed in /etc/hosts, which "
        "otherwise eats the whole connect timeout.",
    )

    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    read_timeout_seconds: float = Field(default=20.0, gt=0)
    write_timeout_seconds: float = Field(default=20.0, gt=0)
    pool_timeout_seconds: float = Field(default=5.0, gt=0)
    max_connections: int = Field(default=50, gt=0)
    max_keepalive_connections: int = Field(default=20, ge=0)

    max_attempts: int = Field(default=3, ge=1, le=10, description="Attempts incl. the first.")
    retry_backoff_base_seconds: float = Field(default=0.25, ge=0)
    retry_backoff_max_seconds: float = Field(default=4.0, ge=0)

    max_page_size: int = Field(default=50, ge=1, le=100)

    @field_validator("base_url")
    @classmethod
    def _normalise_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("WC_BASE_URL must be an absolute http(s) URL")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("WC_BASE_URL must not contain credentials, a query or a fragment")
        return value.rstrip("/")

    @field_validator("api_path")
    @classmethod
    def _normalise_api_path(cls, value: str) -> str:
        return "/" + value.strip("/")

    @model_validator(mode="after")
    def _require_tls_unless_allowed(self) -> WooCommerceSettings:
        if self.auth_method is WooCommerceAuthMethod.OAUTH1 and not self.allow_insecure_http:
            raise ValueError(
                "WC_BASE_URL uses plain HTTP; set WC_ALLOW_INSECURE_HTTP=true only for a "
                "local or private-network store"
            )
        return self

    @property
    def auth_method(self) -> WooCommerceAuthMethod:
        if urlsplit(self.base_url).scheme == "https":
            return WooCommerceAuthMethod.BASIC
        return WooCommerceAuthMethod.OAUTH1

    @property
    def api_base_url(self) -> str:
        return f"{self.base_url}{self.api_path}"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DB_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    url: SecretStr = Field(
        description="PostgreSQL URL, e.g. postgresql+asyncpg://user:pass@host:5432/woocommerce_mcp"
    )
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=5, ge=0)
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


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MCP_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    path: str = "/mcp"
    stateless_http: bool = Field(
        default=True,
        description="Stateless Streamable HTTP lets any replica serve any request (no sticky "
        "sessions). Tools here need no server-initiated messages, so this is the default.",
    )
    json_response: bool = True
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*", "[::1]:*"],
        description="Host header allow-list for DNS-rebinding protection (comma separated). "
        "Add the in-cluster service name, e.g. woocommerce-mcp:*, woocommerce-mcp.ns.svc:*",
    )
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    max_request_body_bytes: int = Field(default=1024 * 1024, gt=0)
    shutdown_grace_seconds: int = Field(default=25, ge=0)
    forwarded_allow_ips: str = "127.0.0.1"
    access_log: bool = False
    readiness_checks_woocommerce: bool = Field(
        default=False,
        description="Also require the WooCommerce API to answer for /readyz. Off by default so "
        "an upstream outage does not remove every replica from the Service.",
    )
    readiness_timeout_seconds: float = Field(default=2.0, gt=0)

    @field_validator("allowed_hosts", "allowed_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("path")
    @classmethod
    def _normalise_path(cls, value: str) -> str:
        return "/" + value.strip("/")


class OperationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPERATIONS_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    retention_hours: int = Field(
        default=72, ge=1, description="How long idempotency/operation records are kept."
    )
    in_progress_lease_seconds: int = Field(
        default=120,
        ge=10,
        description="After this long a 'pending' record is treated as abandoned (crash/kill).",
    )
    purge_interval_seconds: int = Field(
        default=900, ge=0, description="Background purge of expired records; 0 disables it."
    )


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore", frozen=True)

    app_env: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    service_name: str = "woocommerce-mcp"


class Settings:
    """Aggregate of every settings group, built once at process start and injected."""

    def __init__(
        self,
        *,
        app: AppSettings | None = None,
        server: ServerSettings | None = None,
        woocommerce: WooCommerceSettings | None = None,
        database: DatabaseSettings | None = None,
        operations: OperationSettings | None = None,
    ) -> None:
        self.app = app or AppSettings()
        self.server = server or ServerSettings()
        self.woocommerce = woocommerce or WooCommerceSettings()
        self.database = database or DatabaseSettings()
        self.operations = operations or OperationSettings()
        if (
            self.app.app_env is Environment.PRODUCTION
            and self.woocommerce.auth_method is WooCommerceAuthMethod.OAUTH1
        ):
            raise ValueError("Production requires an HTTPS WooCommerce store URL")
