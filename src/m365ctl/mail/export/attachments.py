"""Export message attachments to a directory.

File attachments (`#microsoft.graph.fileAttachment`) are written by name;
inline attachments are skipped by default. Item and reference attachments
are skipped (item attachments are nested Graph entities — out of scope
here; reference attachments are URLs to OneDrive items, exported via the
OneDrive side instead).

Filename collisions get ` (N)` suffixes. Names containing path separators
or `..` are reduced to a safe basename.
"""
from __future__ import annotations

import base64
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base


def export_attachments(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    message_id: str,
    out_dir: Path,
    include_inline: bool = False,
) -> list[Path]:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/messages/{message_id}/attachments")
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    used_names: set[str] = set()
    for att in resp.get("value", []) or []:
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        if not include_inline and att.get("isInline"):
            continue
        raw_b64 = att.get("contentBytes")
        if not raw_b64:
            continue
        safe = _safe_name(att.get("name") or att.get("id") or "attachment")
        path = _disambiguate(out_dir / safe, used_names)
        path.write_bytes(base64.b64decode(raw_b64))
        used_names.add(path.name)
        written.append(path)
    return written


def _safe_name(name: str) -> str:
    """Strip path separators / parent-traversal; default if empty."""
    base = Path(name).name
    base = base.replace("..", "_")
    return base or "attachment"


def _disambiguate(target: Path, used: set[str]) -> Path:
    """Return a unique sibling path; appends ' (N)' before extension if needed."""
    if target.name not in used and not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    n = 1
    while True:
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if candidate.name not in used and not candidate.exists():
            return candidate
        n += 1
