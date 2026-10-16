"""Process entry point: ``gateway`` or ``python -m gateway``.

Uvicorn handles SIGINT/SIGTERM: it stops accepting connections, waits up to
``GATEWAY_SHUTDOWN_GRACE_SECONDS`` for in-flight requests, then runs the application lifespan
shutdown, which stops the MCP proxy, closes the upstream HTTP pool and disposes the database
pool. Keep the Kubernetes ``terminationGracePeriodSeconds`` above that grace period.
"""

from __future__ import annotations

import sys

import uvicorn
from pydantic import ValidationError

from gateway.app import create_app
from gateway.config import Settings


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Report locations and messages only: input values may be credentials.
        problems = "\n".join(
            f"  {'.'.join(str(p) for p in err['loc']) or exc.title}: {err['msg']}"
            for err in exc.errors(include_url=False, include_input=False, include_context=False)
        )
        print(f"Invalid configuration ({exc.title}):\n{problems}", file=sys.stderr)
        raise SystemExit(2) from None
    except ValueError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def main() -> None:
    settings = load_settings()
    server = settings.server
    config = uvicorn.Config(
        create_app(settings),
        host=server.host,
        port=server.port,
        lifespan="on",
        log_config=None,  # logging is configured by create_app
        access_log=server.access_log,
        proxy_headers=True,
        forwarded_allow_ips=server.forwarded_allow_ips,
        server_header=False,
        timeout_graceful_shutdown=server.shutdown_grace_seconds,
        timeout_keep_alive=5,
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
