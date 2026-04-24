"""Read-only attachments list + single-attachment content fetcher."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Attachment


def list_attachments(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
) -> list[Attachment]:
    """List attachments for ``message_id`` as metadata records (no file bodies)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/messages/{message_id}/attachments")
    return [
        Attachment.from_graph_json(raw, message_id=message_id)
        for raw in resp.get("value", [])
    ]


def get_attachment_content(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    attachment_id: str,
) -> bytes:
    """Fetch the raw body of a single attachment (``$value`` endpoint)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    return graph.get_bytes(
        f"{ub}/messages/{message_id}/attachments/{attachment_id}/$value"
    )
