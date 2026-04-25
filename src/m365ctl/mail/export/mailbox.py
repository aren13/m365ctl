"""Walk every folder + emit one mbox per folder + manifest."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode
from m365ctl.mail.export.manifest import (
    Manifest,
    read_manifest,
    write_manifest,
)
from m365ctl.mail.export.mbox import export_folder_to_mbox
from m365ctl.mail.folders import list_folders


def export_mailbox(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    mailbox_upn: str,
    auth_mode: AuthMode,
    out_dir: Path,
) -> Manifest:
    """Export every folder; write a manifest.json at out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest = read_manifest(manifest_path)
    if not manifest.mailbox_upn:
        manifest.mailbox_upn = mailbox_upn
    if not manifest.started_at:
        manifest.started_at = datetime.now(timezone.utc).isoformat()

    for folder in list_folders(graph, mailbox_spec=mailbox_spec, auth_mode=auth_mode):
        if manifest.should_skip(folder.id):
            continue
        safe = _sanitise(folder.path)
        mbox_path = out_dir / f"{safe}.mbox"
        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=str(mbox_path.relative_to(out_dir)),
            status="in_progress",
            count=0,
        )
        write_manifest(manifest, manifest_path)
        try:
            count = export_folder_to_mbox(
                graph,
                mailbox_spec=mailbox_spec,
                auth_mode=auth_mode,
                folder_id=folder.id,
                folder_path=folder.path,
                out_path=mbox_path,
            )
        except Exception:
            # Persist whatever progress we had; surface the error to caller.
            write_manifest(manifest, manifest_path)
            raise
        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=str(mbox_path.relative_to(out_dir)),
            status="done",
            count=count,
        )
        write_manifest(manifest, manifest_path)
    return manifest


def _sanitise(path: str) -> str:
    """Replace path separators so the mbox lives at the export root."""
    return path.replace("/", "_").replace("\\", "_")
