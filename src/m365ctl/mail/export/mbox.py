"""MBOX export — sequential ``From `` separator format.

Each message is wrapped:

    From <sender> <RFC-2822 date>
    <EML bytes, with body lines starting with "From " escaped to ">From ">

Bodies streamed message-by-message — never buffer the whole folder.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.export.eml import fetch_eml_bytes


_MBOX_DATE_FMT = "%a %b %d %H:%M:%S %Y"


class MboxWriter:
    """Stream-write mbox records to a file. Use as a context manager."""

    def __init__(self, path: Path):
        self.path = path
        self._fh: BinaryIO | None = None

    def __enter__(self) -> "MboxWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "wb")
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def append(self, eml_bytes: bytes, *, sender_addr: str, received_at: datetime) -> None:
        if self._fh is None:
            raise RuntimeError("MboxWriter must be used as a context manager")
        header = f"From {sender_addr} {received_at.strftime(_MBOX_DATE_FMT)}\n".encode("utf-8")
        self._fh.write(header)
        # Escape body lines that begin with literal "From " by prefixing ">".
        # Operates on the EML's raw byte stream (line-oriented).
        escaped = re.sub(rb"(?m)^From ", b">From ", eml_bytes)
        self._fh.write(escaped)
        if not escaped.endswith(b"\n"):
            self._fh.write(b"\n")
        self._fh.write(b"\n")  # blank line between records


def export_folder_to_mbox(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    folder_id: str,
    folder_path: str,
    out_path: Path,
    page_size: int = 100,
) -> int:
    """Stream every message in ``folder_id`` into ``out_path``. Returns count."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    list_path = f"{ub}/mailFolders/{folder_id}/messages"
    params = {
        "$select": "id,from,receivedDateTime,subject",
        "$orderby": "receivedDateTime asc",
        "$top": page_size,
    }
    count = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Touch the file so empty folders still produce an mbox file.
    out_path.touch()
    with MboxWriter(out_path) as writer:
        for items, _ in graph.get_paginated(list_path, params=params):
            for raw in items:
                mid = raw["id"]
                sender = (raw.get("from") or {}).get("emailAddress", {}).get("address") or "unknown"
                received_str = raw.get("receivedDateTime") or ""
                received = _parse_iso(received_str)
                eml = fetch_eml_bytes(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    message_id=mid,
                )
                writer.append(eml, sender_addr=sender, received_at=received)
                count += 1
    return count


def _parse_iso(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
