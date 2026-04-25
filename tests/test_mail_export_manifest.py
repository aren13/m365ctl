from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.mail.export.manifest import (
    CURRENT_MANIFEST_VERSION,
    FolderEntry,
    Manifest,
    ManifestError,
    read_manifest,
    write_manifest,
)


def test_empty_manifest_defaults() -> None:
    m = Manifest()
    assert m.version == CURRENT_MANIFEST_VERSION
    assert m.version == 2
    assert m.folders == {}
    assert m.mailbox_upn == ""
    assert m.started_at == ""


def test_round_trip_write_then_read(tmp_path: Path) -> None:
    m = Manifest(mailbox_upn="alice@example.com", started_at="2026-04-25T10:00:00+00:00")
    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="done",
        count=12,
    )
    p = tmp_path / "manifest.json"
    write_manifest(m, p)

    loaded = read_manifest(p)
    assert loaded == m
    assert loaded.folders["fld-1"].status == "done"
    assert loaded.folders["fld-1"].count == 12
    assert loaded.folders["fld-1"].mbox_path == "Inbox.mbox"


def test_update_folder_records_id_count_and_timestamps() -> None:
    m = Manifest()
    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="in_progress",
        count=0,
    )
    fe = m.folders["fld-1"]
    assert fe.folder_id == "fld-1"
    assert fe.folder_path == "Inbox"
    assert fe.status == "in_progress"
    assert fe.count == 0
    assert fe.started_at is not None  # timestamp populated on first record
    assert fe.completed_at is None

    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="done",
        count=42,
    )
    fe = m.folders["fld-1"]
    assert fe.status == "done"
    assert fe.count == 42
    assert fe.completed_at is not None


def test_should_skip_only_for_done_status() -> None:
    m = Manifest()
    # missing folder
    assert m.should_skip("nope") is False

    m.update_folder("fld-1", folder_path="A", mbox_path="A.mbox", status="pending", count=0)
    assert m.should_skip("fld-1") is False

    m.update_folder("fld-2", folder_path="B", mbox_path="B.mbox", status="in_progress", count=0)
    assert m.should_skip("fld-2") is False

    m.update_folder("fld-3", folder_path="C", mbox_path="C.mbox", status="done", count=5)
    assert m.should_skip("fld-3") is True


def test_read_manifest_missing_path_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "does-not-exist.json"
    m = read_manifest(p)
    assert isinstance(m, Manifest)
    assert m.folders == {}
    assert m.mailbox_upn == ""
    assert m.version == CURRENT_MANIFEST_VERSION


def test_read_manifest_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{ not valid json")
    with pytest.raises(ManifestError):
        read_manifest(p)


def test_read_manifest_non_object_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]")
    with pytest.raises(ManifestError):
        read_manifest(p)


def test_folder_entry_round_trip_via_double_star() -> None:
    """Sanity: FolderEntry(**asdict) reconstruction works."""
    from dataclasses import asdict

    fe = FolderEntry(
        folder_id="x",
        folder_path="Inbox/Triage",
        mbox_path="Inbox_Triage.mbox",
        status="done",
        count=3,
        started_at="2026-04-25T00:00:00+00:00",
        completed_at="2026-04-25T00:01:00+00:00",
    )
    rebuilt = FolderEntry(**asdict(fe))
    assert rebuilt == fe


def test_folder_entry_accepts_last_exported_kwargs_default_none() -> None:
    """Phase 11.x: FolderEntry exposes last_exported_id + last_exported_received_at."""
    fe = FolderEntry(
        folder_id="f",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
    )
    assert fe.last_exported_id is None
    assert fe.last_exported_received_at is None

    fe2 = FolderEntry(
        folder_id="g",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        last_exported_id="m42",
        last_exported_received_at="2026-04-10T00:00:00+00:00",
    )
    assert fe2.last_exported_id == "m42"
    assert fe2.last_exported_received_at == "2026-04-10T00:00:00+00:00"


def test_update_folder_stores_last_exported_fields() -> None:
    m = Manifest()
    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="in_progress",
        count=3,
        last_exported_id="m3",
        last_exported_received_at="2026-04-03T10:00:00+00:00",
    )
    fe = m.folders["fld-1"]
    assert fe.last_exported_id == "m3"
    assert fe.last_exported_received_at == "2026-04-03T10:00:00+00:00"


def test_update_folder_preserves_last_exported_when_omitted() -> None:
    """Calling update_folder without the new kwargs must not blank existing values."""
    m = Manifest()
    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="in_progress",
        count=3,
        last_exported_id="m3",
        last_exported_received_at="2026-04-03T10:00:00+00:00",
    )
    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="in_progress",
        count=4,
    )
    fe = m.folders["fld-1"]
    assert fe.count == 4
    assert fe.last_exported_id == "m3"
    assert fe.last_exported_received_at == "2026-04-03T10:00:00+00:00"


def test_round_trip_preserves_last_exported_fields(tmp_path: Path) -> None:
    m = Manifest(mailbox_upn="alice@example.com",
                 started_at="2026-04-25T10:00:00+00:00")
    m.update_folder(
        "fld-1",
        folder_path="Inbox",
        mbox_path="Inbox.mbox",
        status="in_progress",
        count=5,
        last_exported_id="m5",
        last_exported_received_at="2026-04-05T10:00:00+00:00",
    )
    p = tmp_path / "manifest.json"
    write_manifest(m, p)
    loaded = read_manifest(p)
    assert loaded.folders["fld-1"].last_exported_id == "m5"
    assert loaded.folders["fld-1"].last_exported_received_at == "2026-04-05T10:00:00+00:00"


def test_v1_manifest_loads_with_none_cursor_fields(tmp_path: Path) -> None:
    """A v1 manifest on disk should load as v2 with new fields = None."""
    import json

    v1_payload = {
        "version": 1,
        "mailbox_upn": "alice@example.com",
        "started_at": "2026-04-25T00:00:00+00:00",
        "folders": {
            "fld-1": {
                "folder_id": "fld-1",
                "folder_path": "Inbox",
                "mbox_path": "Inbox.mbox",
                "status": "done",
                "count": 7,
                "started_at": "2026-04-25T00:00:00+00:00",
                "completed_at": "2026-04-25T00:05:00+00:00",
            },
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(v1_payload))

    loaded = read_manifest(p)
    assert loaded.version == CURRENT_MANIFEST_VERSION
    assert loaded.version == 2
    fe = loaded.folders["fld-1"]
    assert fe.count == 7
    assert fe.status == "done"
    assert fe.last_exported_id is None
    assert fe.last_exported_received_at is None
