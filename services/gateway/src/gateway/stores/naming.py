"""Store slugs and the names derived from them.

A slug is ``[a-z][a-z0-9-]{0,19}[a-z0-9]`` (2-21 characters, no underscore): short enough that
``<slug>__update_order_status`` stays under the agent's 64-character tool-name limit, and free of
``_`` so the ``<slug>__<tool>`` split stays unambiguous.
"""

from __future__ import annotations

import re
import secrets

STORE_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,19}[a-z0-9]$")
MAX_SLUG = 21
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
RELEASE_NAME = "store"


def slug_base(name: str) -> str:
    """The slug a name would get if it were free, shortened to leave room for a suffix."""
    base = _NON_ALNUM.sub("-", name.lower()).strip("-")
    if not base or not base[0].isalpha():
        base = f"s-{base}" if base else "store"
    base = base[: MAX_SLUG - 4].strip("-")
    return base if len(base) >= 2 else f"{base}-store"


def slug_candidates(name: str) -> list[str]:
    """Slugs to try in order: the base, numbered variants, then a random suffix."""
    base = slug_base(name)
    candidates = [base] + [f"{base}-{n}" for n in range(2, 10)]
    candidates.append(f"{base[: MAX_SLUG - 7]}-{secrets.token_hex(3)}")
    return [c for c in candidates if STORE_SLUG_RE.match(c)]


def mcp_db_name(store_id: str) -> str:
    """Postgres role and schema of one store's MCP server (``store_<hex id>``)."""
    return "store_" + store_id.replace("-", "")


def mcp_url(namespace: str) -> str:
    return f"http://store-mcp.{namespace}.svc.cluster.local:8000/mcp"
