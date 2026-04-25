from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.export.mailbox import export_mailbox
from m365ctl.mail.export.manifest import Manifest, read_manifest, write_manifest
from m365ctl.mail.models import Folder


def _folder(fid: str, path: str, *, mailbox_upn: str = "alice@example.com") -> Folder:
    return Folder(
        id=fid,
        mailbox_upn=mailbox_upn,
        display_name=path.rsplit("/", 1)[-1],
        parent_id=None,
        path=path,
        total_items=0,
        unread_items=0,
        child_folder_count=0,
        well_known_name=None,
    )


def test_walks_all_folders_exports_each_and_marks_done(tmp_path: Path) -> None:
    graph = MagicMock()
    folders = [
        _folder("fld-1", "Inbox"),
        _folder("fld-2", "Sent"),
        _folder("fld-3", "Archive"),
    ]

    with (
        patch(
            "m365ctl.mail.export.mailbox.list_folders",
            return_value=iter(folders),
        ) as mock_list,
        patch(
            "m365ctl.mail.export.mailbox.export_folder_to_mbox",
            return_value=(7, "m7", "2026-04-25T07:00:00Z"),
        ) as mock_export,
    ):
        manifest = export_mailbox(
            graph,
            mailbox_spec="me",
            mailbox_upn="alice@example.com",
            auth_mode="delegated",
            out_dir=tmp_path,
        )

    mock_list.assert_called_once_with(
        graph, mailbox_spec="me", auth_mode="delegated"
    )

    # All three folders had export_folder_to_mbox called.
    assert mock_export.call_count == 3
    call_args = [c.kwargs for c in mock_export.call_args_list]
    folder_ids = sorted(ka["folder_id"] for ka in call_args)
    assert folder_ids == ["fld-1", "fld-2", "fld-3"]

    # Verify out_path argument routed to <out_dir>/<sanitised>.mbox
    out_paths = sorted(str(ka["out_path"]) for ka in call_args)
    assert out_paths == [
        str(tmp_path / "Archive.mbox"),
        str(tmp_path / "Inbox.mbox"),
        str(tmp_path / "Sent.mbox"),
    ]

    # Each call passed mailbox_spec, auth_mode, folder_path
    for ka in call_args:
        assert ka["mailbox_spec"] == "me"
        assert ka["auth_mode"] == "delegated"
        assert "folder_path" in ka

    # All three folders marked status=done with count from the export.
    for fid in ("fld-1", "fld-2", "fld-3"):
        assert manifest.folders[fid].status == "done"
        assert manifest.folders[fid].count == 7

    # Manifest file persisted on disk.
    persisted = read_manifest(tmp_path / "manifest.json")
    assert persisted == manifest


def test_skips_folder_already_marked_done(tmp_path: Path) -> None:
    # Pre-write a manifest where fld-1 is already done.
    pre = Manifest(mailbox_upn="alice@example.com", started_at="2026-04-25T00:00:00+00:00")
    pre.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="done",
        count=99,
    )
    write_manifest(pre, tmp_path / "manifest.json")

    graph = MagicMock()
    folders = [_folder("fld-1", "Inbox"), _folder("fld-2", "Sent")]

    with (
        patch(
            "m365ctl.mail.export.mailbox.list_folders",
            return_value=iter(folders),
        ),
        patch(
            "m365ctl.mail.export.mailbox.export_folder_to_mbox",
            return_value=(4, "m4", "2026-04-25T04:00:00Z"),
        ) as mock_export,
    ):
        manifest = export_mailbox(
            graph,
            mailbox_spec="me",
            mailbox_upn="alice@example.com",
            auth_mode="delegated",
            out_dir=tmp_path,
        )

    # Only fld-2 actually exported.
    assert mock_export.call_count == 1
    assert mock_export.call_args.kwargs["folder_id"] == "fld-2"

    # fld-1 unchanged at count=99, still done.
    assert manifest.folders["fld-1"].status == "done"
    assert manifest.folders["fld-1"].count == 99
    assert manifest.folders["fld-2"].status == "done"
    assert manifest.folders["fld-2"].count == 4


def test_first_run_populates_mailbox_upn_and_started_at(tmp_path: Path) -> None:
    graph = MagicMock()
    with (
        patch(
            "m365ctl.mail.export.mailbox.list_folders",
            return_value=iter([_folder("fld-1", "Inbox")]),
        ),
        patch(
            "m365ctl.mail.export.mailbox.export_folder_to_mbox",
            return_value=(2, "m2", "2026-04-25T02:00:00Z"),
        ),
    ):
        manifest = export_mailbox(
            graph,
            mailbox_spec="upn:alice@example.com",
            mailbox_upn="alice@example.com",
            auth_mode="app-only",
            out_dir=tmp_path,
        )

    assert manifest.mailbox_upn == "alice@example.com"
    assert manifest.started_at != ""
    # ISO-8601 sanity: contains a date.
    assert "T" in manifest.started_at


def test_folder_path_with_slash_gets_sanitised(tmp_path: Path) -> None:
    graph = MagicMock()
    folders = [_folder("fld-1", "Inbox/Triage")]

    with (
        patch(
            "m365ctl.mail.export.mailbox.list_folders",
            return_value=iter(folders),
        ),
        patch(
            "m365ctl.mail.export.mailbox.export_folder_to_mbox",
            return_value=(1, "m1", "2026-04-25T01:00:00Z"),
        ) as mock_export,
    ):
        manifest = export_mailbox(
            graph,
            mailbox_spec="me",
            mailbox_upn="alice@example.com",
            auth_mode="delegated",
            out_dir=tmp_path,
        )

    out_path = mock_export.call_args.kwargs["out_path"]
    assert out_path == tmp_path / "Inbox_Triage.mbox"
    # folder_path is preserved unsanitised in the call (export_folder_to_mbox
    # uses it for traversal, not for writing).
    assert mock_export.call_args.kwargs["folder_path"] == "Inbox/Triage"
    # Manifest entry's mbox_path also sanitised.
    assert manifest.folders["fld-1"].mbox_path == "Inbox_Triage.mbox"


def test_empty_mailbox_writes_empty_manifest_and_exits_cleanly(tmp_path: Path) -> None:
    graph = MagicMock()
    with (
        patch(
            "m365ctl.mail.export.mailbox.list_folders",
            return_value=iter([]),
        ),
        patch(
            "m365ctl.mail.export.mailbox.export_folder_to_mbox",
        ) as mock_export,
    ):
        manifest = export_mailbox(
            graph,
            mailbox_spec="me",
            mailbox_upn="alice@example.com",
            auth_mode="delegated",
            out_dir=tmp_path,
        )

    assert mock_export.call_count == 0
    assert manifest.folders == {}
    assert manifest.mailbox_upn == "alice@example.com"
    assert manifest.started_at != ""
    # No file written to disk yet — only the per-folder writes persist; on
    # zero folders there's no write. The orchestrator writes only when it
    # does work. (Implementation contract: empty mailbox is allowed to
    # leave no manifest.json; verify it didn't crash and returned the
    # in-memory manifest.)
