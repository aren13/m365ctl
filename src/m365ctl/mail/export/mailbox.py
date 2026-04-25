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
    """Export every folder; write a manifest.json at out_dir.

    Resumes mid-folder when the manifest has an ``in_progress`` entry with
    ``last_exported_id`` + ``last_exported_received_at`` populated. The
    cursor is forwarded to ``export_folder_to_mbox`` and the manifest is
    checkpointed after every successfully written message.
    """
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
        existing = manifest.folders.get(folder.id)
        # Resume cursor if the folder was in_progress with a last-exported id.
        resume_after: tuple[str, str] | None = None
        if (existing
                and existing.status == "in_progress"
                and existing.last_exported_id
                and existing.last_exported_received_at):
            resume_after = (
                existing.last_exported_received_at,
                existing.last_exported_id,
            )

        safe = _sanitise(folder.path)
        mbox_path = out_dir / f"{safe}.mbox"
        rel_mbox = str(mbox_path.relative_to(out_dir))
        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=rel_mbox,
            status="in_progress",
            count=existing.count if existing else 0,
        )
        write_manifest(manifest, manifest_path)

        # Per-message checkpoint callback. Default-kwarg capture sidesteps
        # the late-binding loop-variable gotcha so each iteration's closure
        # binds *its* folder.id / folder.path, not the loop's final values.
        def _checkpoint(
            mid: str,
            ts: str,
            *,
            _fid: str = folder.id,
            _fp: str = folder.path,
            _rel: str = rel_mbox,
        ) -> None:
            entry = manifest.folders[_fid]
            manifest.update_folder(
                _fid,
                folder_path=_fp,
                mbox_path=_rel,
                status="in_progress",
                count=entry.count + 1,
                last_exported_id=mid,
                last_exported_received_at=ts,
            )
            write_manifest(manifest, manifest_path)

        try:
            _count, last_id, last_ts = export_folder_to_mbox(
                graph,
                mailbox_spec=mailbox_spec,
                auth_mode=auth_mode,
                folder_id=folder.id,
                folder_path=folder.path,
                out_path=mbox_path,
                resume_after=resume_after,
                progress_callback=_checkpoint,
            )
        except Exception:
            # Persist whatever progress we had; surface the error to caller.
            write_manifest(manifest, manifest_path)
            raise

        manifest.update_folder(
            folder.id,
            folder_path=folder.path,
            mbox_path=rel_mbox,
            status="done",
            # Callback already advanced the count; preserve it.
            count=manifest.folders[folder.id].count,
            last_exported_id=last_id,
            last_exported_received_at=last_ts,
        )
        write_manifest(manifest, manifest_path)
    return manifest


def _sanitise(path: str) -> str:
    """Replace path separators so the mbox lives at the export root."""
    return path.replace("/", "_").replace("\\", "_")
