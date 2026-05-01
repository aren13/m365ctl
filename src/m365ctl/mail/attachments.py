"""Read-only attachments list + single-attachment content fetcher."""
from __future__ import annotations

from m365ctl.common.graph import GraphClient, GraphError
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


def list_attachments_for_messages(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_ids: list[str],
) -> dict[str, list[Attachment]]:
    """Batched fan-out of ``list_attachments`` over many message ids.

    Issues one ``/$batch`` POST per chunk of 20. Returns ``{message_id: [attachments]}``;
    messages whose listing GET returned an error are simply omitted (callers
    that need hard error semantics can compare ``len(out) == len(message_ids)``).
    """
    if not message_ids:
        return {}
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    out: dict[str, list[Attachment]] = {}
    # ``BatchSession`` auto-flushes every 20 enqueued sub-requests, so we can
    # enqueue them all under one with-block and let the session handle
    # chunking — no manual chunk loop needed.
    with graph.batch() as b:
        futs = [
            (mid, b.get(f"{ub}/messages/{mid}/attachments"))
            for mid in message_ids
        ]
    for mid, fut in futs:
        try:
            body = fut.result()
        except GraphError:
            continue
        items = body.get("value", []) if isinstance(body, dict) else []
        out[mid] = [Attachment.from_graph_json(raw, message_id=mid) for raw in items]
    return out


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
