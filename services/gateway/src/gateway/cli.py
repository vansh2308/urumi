"""Operator CLI: ``gateway-admin``. Needs only ``DB_URL`` (key generation needs nothing).

Platform-admin status (which allows registering MCP connectors) is never grantable through the
API, so the first operator is bootstrapped here, by someone with database access.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from cryptography.fernet import Fernet
from sqlalchemy import update

from gateway.config import DatabaseSettings
from gateway.db.engine import Database, create_engine, create_session_factory
from gateway.db.models import User
from gateway.services.audit import AuditLog
from gateway.services.auth import normalise_email


async def _set_platform_admin(email: str, *, value: bool) -> bool:
    engine = create_engine(DatabaseSettings(), application_name="gateway-admin")
    db = Database(create_session_factory(engine))
    try:
        async with db.transaction() as session:
            user_id = await session.scalar(
                update(User)
                .where(User.email == normalise_email(email))
                .values(is_platform_admin=value)
                .returning(User.id)
            )
            if user_id is not None:
                AuditLog(db).add(
                    session,
                    action="user.platform_admin." + ("grant" if value else "revoke"),
                    target_type="user",
                    target_id=user_id,
                    details={"via": "cli"},
                )
        return user_id is not None
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="gateway-admin")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("grant-platform-admin", "allow a user to manage the MCP connector registry"),
        ("revoke-platform-admin", "remove that permission"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("email")
    sub.add_parser("generate-credential-key", help="print a new key for UPSTREAM_CREDENTIAL_KEYS")
    args = parser.parse_args()

    if args.command == "generate-credential-key":
        print(Fernet.generate_key().decode())
        return

    found = asyncio.run(
        _set_platform_admin(args.email, value=args.command == "grant-platform-admin")
    )
    if not found:
        print("No user with that email.", file=sys.stderr)
        raise SystemExit(1)
    print("Done.")


if __name__ == "__main__":
    main()
