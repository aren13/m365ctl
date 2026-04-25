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
from typing import Any, BinaryIO, Callable, Literal

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.export.eml import fetch_eml_bytes


_MBOX_DATE_FMT = "%a %b %d %H:%M:%S %Y"


class MboxWriter:
    """Stream-write mbox records to a file. Use as a context manager."""

    def __init__(self, path: Path, *, mode: Literal["w", "a"] = "w"):
        self.path = path
        self._mode = mode
        self._fh: BinaryIO | None = None

    def __enter__(self) -> "MboxWriter":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._mode == "a":
            self._fh = open(self.path, "ab")
        else:
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
    resume_after: tuple[str, str] | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> tuple[int, str | None, str | None]:
    """Stream every message in ``folder_id`` into ``out_path``.

    Returns ``(count, last_exported_id, last_exported_received_at)``.

    When ``resume_after`` is set to ``(received_at_iso, message_id)``, the
    mbox is opened in append mode and messages at or before the cursor are
    skipped (cursor message itself is treated as already-exported).

    ``progress_callback(message_id, received_at_iso)`` is invoked after each
    successful write so callers can checkpoint persistent state.
    """
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    list_path = f"{ub}/mailFolders/{folder_id}/messages"
    params = {
        "$select": "id,from,receivedDateTime,subject",
        "$orderby": "receivedDateTime asc",
        "$top": page_size,
    }

    cursor_ts, cursor_id = (resume_after if resume_after else (None, None))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not resume_after:
        # Fresh export — touch so empty folders still produce a file.
        out_path.touch()

    mode: Literal["w", "a"] = "a" if resume_after else "w"
    count = 0
    last_id: str | None = None
    last_ts: str | None = None
    with MboxWriter(out_path, mode=mode) as writer:
        for items, _ in graph.get_paginated(list_path, params=params):
            for raw in items:
                mid = raw["id"]
                received_str = raw.get("receivedDateTime") or ""
                if cursor_ts is not None:
                    # Skip messages strictly before the cursor.
                    if received_str <= cursor_ts and mid != cursor_id:
                        continue
                    if mid == cursor_id:
                        # Cursor message itself was already exported in the
                        # prior run; skip and clear the cursor so subsequent
                        # messages (received_at >= cursor_ts) export.
                        cursor_ts = None
                        continue
                    # Past the cursor — clear it.
                    cursor_ts = None

                sender = (
                    (raw.get("from") or {}).get("emailAddress", {}).get("address")
                    or "unknown"
                )
                received = _parse_iso(received_str)
                eml = fetch_eml_bytes(
                    graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode,
                    message_id=mid,
                )
                writer.append(eml, sender_addr=sender, received_at=received)
                count += 1
                last_id = mid
                last_ts = received_str
                if progress_callback is not None:
                    progress_callback(mid, received_str)
    return count, last_id, last_ts


def _parse_iso(s: str) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
