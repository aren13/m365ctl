"""Per-export manifest for resume-on-interrupt.

A ``Manifest`` records, per folder:
  - status: 'pending' | 'in_progress' | 'done'
  - count:  messages exported so far
  - mbox_path: relative path under the export root
  - started_at / completed_at: ISO timestamps

Re-running ``export_mailbox`` reads the manifest first; folders marked
``done`` are skipped. ``in_progress`` folders are restarted (the mbox
file is overwritten — the per-folder unit isn't restartable mid-stream
in this first cut; cancel during a folder = redo that folder).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

CURRENT_MANIFEST_VERSION = 1


class ManifestError(ValueError):
    """Raised when the manifest is unreadable or has the wrong shape."""


@dataclass
class FolderEntry:
    folder_id: str
    folder_path: str
    mbox_path: str
    status: str = "pending"        # 'pending' | 'in_progress' | 'done'
    count: int = 0
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class Manifest:
    version: int = CURRENT_MANIFEST_VERSION
    mailbox_upn: str = ""
    started_at: str = ""
    folders: dict[str, FolderEntry] = field(default_factory=dict)

    def update_folder(
        self, folder_id: str, *,
        folder_path: str, mbox_path: str,
        status: str, count: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.folders.get(folder_id)
        if existing is None:
            existing = FolderEntry(
                folder_id=folder_id, folder_path=folder_path, mbox_path=mbox_path,
                started_at=now,
            )
            self.folders[folder_id] = existing
        existing.status = status
        existing.count = count
        if status == "in_progress" and existing.started_at is None:
            existing.started_at = now
        if status == "done":
            existing.completed_at = now


    def should_skip(self, folder_id: str) -> bool:
        e = self.folders.get(folder_id)
        return e is not None and e.status == "done"


def write_manifest(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": manifest.version,
        "mailbox_upn": manifest.mailbox_upn,
        "started_at": manifest.started_at,
        "folders": {fid: asdict(fe) for fid, fe in manifest.folders.items()},
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def read_manifest(path: Path) -> Manifest:
    if not path.exists():
        return Manifest()
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ManifestError(f"invalid JSON in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest must be an object: {path}")
    if raw.get("version") != CURRENT_MANIFEST_VERSION:
        raise ManifestError(
            f"unsupported manifest version {raw.get('version')!r} in {path}"
        )
    folders = {
        fid: FolderEntry(**fe) for fid, fe in (raw.get("folders") or {}).items()
    }
    return Manifest(
        version=raw["version"],
        mailbox_upn=raw.get("mailbox_upn", ""),
        started_at=raw.get("started_at", ""),
        folders=folders,
    )
