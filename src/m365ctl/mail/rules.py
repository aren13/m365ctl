"""Read-only inbox rules list + single-fetch."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Rule


def list_rules(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> list[Rule]:
    """List inbox rules sorted by Graph's ``sequence`` (evaluation order)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/mailFolders/inbox/messageRules")
    rules = [Rule.from_graph_json(raw) for raw in resp.get("value", [])]
    rules.sort(key=lambda r: r.sequence)
    return rules


def get_rule(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    rule_id: str,
) -> Rule:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailFolders/inbox/messageRules/{rule_id}")
    return Rule.from_graph_json(raw)
