"""Read-only mailbox settings + auto-reply fetchers."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import AutomaticRepliesSetting, MailboxSettings


def get_settings(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> MailboxSettings:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailboxSettings")
    return MailboxSettings.from_graph_json(raw)


def get_auto_reply(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> AutomaticRepliesSetting:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailboxSettings/automaticRepliesSetting")
    return AutomaticRepliesSetting.from_graph_json(raw)
