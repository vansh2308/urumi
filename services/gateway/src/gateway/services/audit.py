"""Platform audit trail writer.

Successful changes are audited inside the same transaction as the change, so an audit row exists
if and only if the change committed. Denials and failed logins happen in requests whose own
transaction rolls back, so those are written in a separate short transaction.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.context import current_client_ip, current_request_id
from gateway.db.engine import Database
from gateway.db.models import AuditEvent, AuditOutcome
from gateway.errors import PersistenceUnavailableError
from gateway.services.principal import Principal

logger = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        session: AsyncSession,
        *,
        action: str,
        actor: Principal | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_api_key_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | str | None = None,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Stage an audit row in the caller's transaction."""
        if actor is not None:
            actor_user_id = actor.user_id
            actor_api_key_id = actor_api_key_id or actor.api_key_id
        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                team_id=team_id,
                actor_user_id=actor_user_id,
                actor_api_key_id=actor_api_key_id,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                outcome=outcome.value,
                request_id=current_request_id(),
                ip=current_client_ip(),
                details=details or {},
            )
        )

    async def record(self, **kwargs: Any) -> None:
        """Write an audit row in its own transaction; never fails the calling request."""
        try:
            async with self._db.transaction() as session:
                self.add(session, **kwargs)
        except PersistenceUnavailableError:
            logger.warning("audit event not persisted", extra={"action": kwargs.get("action")})
