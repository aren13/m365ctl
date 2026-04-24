"""Read-only master categories list."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Category


def list_master_categories(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> list[Category]:
    """Return the mailbox's master category list (single non-paginated call)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/outlook/masterCategories")
    return [Category.from_graph_json(raw) for raw in resp.get("value", [])]
