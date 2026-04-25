"""EML (RFC 5322 / MIME) export via Graph ``/messages/{id}/$value``.

Graph returns the message's full MIME wire format on this endpoint —
exactly what `.eml` files contain. No client-side reassembly needed.
"""
from __future__ import annotations

from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base


def fetch_eml_bytes(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
) -> bytes:
    """GET /<ub>/messages/{id}/$value — returns RFC-5322 MIME bytes."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    return graph.get_bytes(f"{ub}/messages/{message_id}/$value")


def export_message_to_eml(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    out_path: Path,
) -> Path:
    """Fetch one message and write its MIME body to out_path."""
    raw = fetch_eml_bytes(
        graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode, message_id=message_id,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    return out_path
