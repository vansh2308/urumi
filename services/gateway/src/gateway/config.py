"""Runtime configuration loaded from environment variables (and an optional ``.env`` file).

Settings are split per concern so that tools which only need one part (Alembic and the admin
CLI only need the database) do not require secrets they never use.
"""

from __future__ import annotations

import fnmatch
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

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
    service_name: str = "gateway"


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GATEWAY_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8100, ge=1, le=65535)
    shutdown_grace_seconds: int = Field(default=25, ge=0)
    forwarded_allow_ips: str = Field(
        default="127.0.0.1",
        description="Proxies whose X-Forwarded-For is trusted for the client IP (rate limits, "
        "audit). Set to the ingress/load-balancer addresses in a cluster.",
    )
    access_log: bool = False
    max_request_body_bytes: int = Field(default=1024 * 1024, gt=0)
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Browser origins allowed to call the API (comma separated), e.g. the "
        "dashboard origin. Empty disables CORS.",
    )
    readiness_timeout_seconds: float = Field(default=2.0, gt=0)
    metrics_enabled: bool = Field(
        default=True,
        description="Serve Prometheus metrics at /metrics. Restrict it to the cluster network.",
    )
    metrics_via_proxy: bool = Field(
        default=False,
        description="Also answer /metrics for requests that came through a proxy or ingress "
        "(X-Forwarded-For set). Off: only direct in-cluster scrapes get metrics, so they are "
        "never public even though the API is behind an ingress.",
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
        description="PostgreSQL URL, e.g. postgresql+asyncpg://user:pass@host:5432/gateway"
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


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTH_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    token_pepper: SecretStr = Field(
        description="Server-side secret mixed into the HMAC of every session token, API key and "
        "invitation token before it is stored. At least 32 characters; rotating it invalidates "
        "all of them.",
    )
    session_ttl_hours: int = Field(default=12, ge=1, le=24 * 30)
    allow_signup: bool = Field(
        default=True,
        description="Allow self-registration. When false, registering requires a valid "
        "invitation token for the same email.",
    )
    invitation_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    password_min_length: int = Field(default=12, ge=8, le=128)

    @field_validator("token_pepper")
    @classmethod
    def _pepper_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("AUTH_TOKEN_PEPPER must be at least 32 characters")
        return value


class ApiKeySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_KEY_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    default_ttl_days: int = Field(default=90, ge=1)
    max_ttl_days: int = Field(
        default=365, ge=1, description="Ceiling for every team's own key lifetime policy."
    )
    max_rotation_grace_hours: int = Field(
        default=24, ge=0, description="Longest a rotated key may keep working after rotation."
    )
    max_keys_per_user_per_team: int = Field(default=20, ge=1)
    last_used_resolution_seconds: int = Field(
        default=60, ge=0, description="last_used_at is written at most this often per key."
    )


class RateLimitSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RATE_LIMIT_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    enabled: bool = True
    per_principal_per_minute: int = Field(
        default=120,
        ge=1,
        description="Default requests per minute for each API key or user session. Teams may "
        "set a lower or higher limit up to max_team_per_minute.",
    )
    max_team_per_minute: int = Field(default=6000, ge=1)
    auth_per_minute: int = Field(
        default=10, ge=1, description="Login/registration attempts per minute per IP and email."
    )


class UpstreamSettings(BaseSettings):
    """How the gateway talks to registered MCP servers."""

    model_config = SettingsConfigDict(
        env_prefix="UPSTREAM_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1:*", "localhost:*"],
        description="host:port patterns (fnmatch) a connector URL may point at, comma "
        "separated, e.g. woocommerce-mcp.urumi.svc.cluster.local:8000. Registration of any "
        "other URL is refused, so the registry cannot be used for SSRF.",
    )
    allow_insecure_http: bool = Field(
        default=True,
        description="Permit http:// connector URLs (in-cluster / localhost traffic).",
    )
    connect_timeout_seconds: float = Field(default=3.0, gt=0)
    default_call_timeout_seconds: float = Field(default=30.0, gt=0)
    max_call_timeout_seconds: float = Field(default=120.0, gt=0)
    max_connections: int = Field(default=100, gt=0)
    max_keepalive_connections: int = Field(default=20, ge=0)
    default_max_concurrency: int = Field(
        default=20, ge=1, description="In-flight calls per connector per gateway replica."
    )
    queue_timeout_seconds: float = Field(
        default=2.0, ge=0, description="How long a call waits for a free concurrency slot."
    )
    aggregate_list_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        description="On the all-servers MCP endpoint, a server whose tool list takes longer is "
        "left out of that listing instead of delaying it.",
    )
    tool_cache_ttl_seconds: float = Field(
        default=60.0, ge=0, description="How long a connector's tool list is cached."
    )
    ip_family: Literal["auto", "ipv4", "ipv6"] = Field(
        default="auto",
        description="'ipv4' avoids the macOS IPv6 mDNS stall for *.local upstream names.",
    )
    credential_keys: Annotated[list[SecretStr], NoDecode] = Field(
        default_factory=list,
        description="Fernet keys (comma separated) that encrypt connector credentials at rest. "
        "The first encrypts, all decrypt. Generate one with `poetry run gateway-admin "
        "generate-credential-key`. Empty disables connector credentials.",
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("credential_keys", mode="before")
    @classmethod
    def _split_keys(cls, value: object) -> object:
        return _split_csv(value)

    def is_exactly_allowed(self, host: str, port: int) -> bool:
        """The host is named without wildcards by some pattern (the port part may be '*')."""
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        for pattern in self.allowed_hosts:
            pattern_host = pattern.lower().rsplit(":", 1)[0]
            if not any(ch in pattern_host for ch in "*?[") and fnmatch.fnmatchcase(
                target, pattern.lower()
            ):
                return True
        return False

    def is_allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return False
        if parts.scheme == "http" and not self.allow_insecure_http:
            return False
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = parts.hostname.lower()
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        return any(fnmatch.fnmatchcase(target, pattern.lower()) for pattern in self.allowed_hosts)


class HostedSettings(BaseSettings):
    """Hosted MCP servers: images the gateway deploys into the cluster on an admin's request."""

    model_config = SettingsConfigDict(
        env_prefix="HOSTED_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    enabled: bool = Field(
        default=False,
        description="Allow platform admins to deploy MCP servers from container images. Needs "
        "the gateway to run in the cluster with a ServiceAccount allowed to manage Deployments, "
        "Services, Secrets and NetworkPolicies in HOSTED_NAMESPACE.",
    )
    namespace: str = Field(default="urumi-mcp", pattern=r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
    allowed_images: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Image patterns (fnmatch, comma separated) admins may deploy, e.g. "
        "'ghcr.io/github/github-mcp-server:*,mcp/duckduckgo:*'. Empty allows none.",
    )
    gateway_namespace: str = Field(
        default="urumi", description="Namespace of the gateway pods (NetworkPolicy ingress)."
    )
    gateway_pod_label: str = Field(
        default="app.kubernetes.io/name=gateway",
        pattern=r"^[A-Za-z0-9./_-]+=[A-Za-z0-9._-]+$",
        description="key=value label of the gateway pods; only they may reach hosted servers.",
    )
    run_as_user: int = Field(default=65532, ge=1, description="Non-root UID for hosted pods.")
    default_cpu_millicores: int = Field(default=250, ge=10)
    max_cpu_millicores: int = Field(default=1000, ge=10)
    default_memory_mib: int = Field(default=256, ge=32)
    max_memory_mib: int = Field(default=1024, ge=32)
    max_servers: int = Field(default=10, ge=1)
    api_url: str = Field(default="https://kubernetes.default.svc")
    service_account_dir: str = Field(default="/var/run/secrets/kubernetes.io/serviceaccount")
    request_timeout_seconds: float = Field(default=10.0, gt=0)

    @field_validator("allowed_images", mode="before")
    @classmethod
    def _split_images(cls, value: object) -> object:
        return _split_csv(value)

    def is_allowed_image(self, image: str) -> bool:
        return any(fnmatch.fnmatchcase(image, pattern) for pattern in self.allowed_images)

    @property
    def gateway_pod_selector(self) -> tuple[str, str]:
        key, _, value = self.gateway_pod_label.partition("=")
        return key, value


class OAuthSettings(BaseSettings):
    """OAuth 2.1 for MCP clients (MCP authorization spec): the gateway is the authorization
    server for its own MCP endpoint. Access tokens are short-lived platform API keys."""

    model_config = SettingsConfigDict(
        env_prefix="OAUTH_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    enabled: bool = False
    issuer_url: str = Field(
        default="",
        description="The gateway's public base URL as MCP clients reach it, e.g. "
        "https://gateway.example.com. The authorization server metadata and endpoints live "
        "under it; the protected resource is <issuer_url>/v1/mcp.",
    )
    consent_url: str = Field(
        default="",
        description="Dashboard page where a signed-in user approves a client, e.g. "
        "https://app.example.com/oauth/consent.",
    )
    allow_http_issuer: bool = Field(
        default=False,
        description="Allow a plain-HTTP issuer other than localhost (local clusters only). OAuth "
        "requires HTTPS everywhere else.",
    )
    access_token_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)
    authorization_request_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    code_ttl_seconds: int = Field(default=300, ge=30, le=600)
    requests_per_minute: int = Field(
        default=30, ge=1, description="Per client IP, on /register, /authorize and /token."
    )


class RetentionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RETENTION_", env_file=_ENV_FILE, extra="ignore", frozen=True
    )

    audit_days: int = Field(default=365, ge=1)
    usage_days: int = Field(default=90, ge=1)
    sessions_days: int = Field(default=7, ge=1, description="Keep expired sessions this long.")
    purge_interval_seconds: int = Field(
        default=3600, ge=0, description="Background purge of expired rows; 0 disables it."
    )


class Settings:
    """Aggregate of every settings group, built once at process start and injected."""

    def __init__(
        self,
        *,
        app: AppSettings | None = None,
        server: ServerSettings | None = None,
        database: DatabaseSettings | None = None,
        auth: AuthSettings | None = None,
        api_keys: ApiKeySettings | None = None,
        rate_limit: RateLimitSettings | None = None,
        upstream: UpstreamSettings | None = None,
        retention: RetentionSettings | None = None,
        hosted: HostedSettings | None = None,
        oauth: OAuthSettings | None = None,
    ) -> None:
        self.app = app or AppSettings()
        self.server = server or ServerSettings()
        self.database = database or DatabaseSettings()
        self.auth = auth or AuthSettings()
        self.api_keys = api_keys or ApiKeySettings()
        self.rate_limit = rate_limit or RateLimitSettings()
        self.upstream = upstream or UpstreamSettings()
        self.retention = retention or RetentionSettings()
        self.hosted = hosted or HostedSettings()
        self.oauth = oauth or OAuthSettings()
        if self.oauth.enabled and not (self.oauth.issuer_url and self.oauth.consent_url):
            raise ValueError("OAUTH_ENABLED needs OAUTH_ISSUER_URL and OAUTH_CONSENT_URL")
        if self.api_keys.default_ttl_days > self.api_keys.max_ttl_days:
            raise ValueError("API_KEY_DEFAULT_TTL_DAYS must not exceed API_KEY_MAX_TTL_DAYS")
        if self.app.app_env is Environment.PRODUCTION and "*" in self.server.cors_allowed_origins:
            raise ValueError("GATEWAY_CORS_ALLOWED_ORIGINS must list origins in production")
