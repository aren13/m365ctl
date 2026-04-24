from __future__ import annotations

from m365ctl.catalog.normalize import normalize_item

FILE_ITEM = {
    "id": "01ABCDEF",
    "name": "report.pdf",
    "size": 12345,
    "createdDateTime": "2024-06-01T10:00:00Z",
    "lastModifiedDateTime": "2024-06-02T11:30:00Z",
    "parentReference": {"path": "/drive/root:/Documents/Finance"},
    "file": {
        "mimeType": "application/pdf",
        "hashes": {"quickXorHash": "abc123=="},
    },
    "createdBy": {"user": {"email": "alice@fazla.com"}},
    "lastModifiedBy": {"user": {"email": "bob@fazla.com"}},
    "shared": {"scope": "users"},
    "eTag": '"{ETAG},1"',
}

FOLDER_ITEM = {
    "id": "01FOLDER",
    "name": "Finance",
    "createdDateTime": "2024-01-01T00:00:00Z",
    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
    "parentReference": {"path": "/drive/root:/Documents"},
    "folder": {"childCount": 12},
    "createdBy": {"user": {"email": "alice@fazla.com"}},
    "lastModifiedBy": {"user": {"email": "alice@fazla.com"}},
}

DELETED_ITEM = {
    "id": "01DELETED",
    "name": "old.txt",
    "deleted": {"state": "deleted"},
    "parentReference": {"path": "/drive/root:/Documents"},
}


def test_normalize_file_produces_expected_row() -> None:
    row = normalize_item("drive-X", FILE_ITEM)
    assert row["drive_id"] == "drive-X"
    assert row["item_id"] == "01ABCDEF"
    assert row["name"] == "report.pdf"
    assert row["parent_path"] == "/Documents/Finance"
    assert row["full_path"] == "/Documents/Finance/report.pdf"
    assert row["size"] == 12345
    assert row["mime_type"] == "application/pdf"
    assert row["is_folder"] is False
    assert row["is_deleted"] is False
    assert row["created_by"] == "alice@fazla.com"
    assert row["modified_by"] == "bob@fazla.com"
    assert row["has_sharing"] is True
    assert row["quick_xor_hash"] == "abc123=="


def test_normalize_folder_has_no_size_or_mime() -> None:
    row = normalize_item("drive-X", FOLDER_ITEM)
    assert row["is_folder"] is True
    assert row["size"] is None
    assert row["mime_type"] is None
    assert row["full_path"] == "/Documents/Finance"


def test_normalize_deleted_preserves_id_and_marks_deleted() -> None:
    row = normalize_item("drive-X", DELETED_ITEM)
    assert row["item_id"] == "01DELETED"
    assert row["is_deleted"] is True


def test_normalize_handles_root_parent_path() -> None:
    item = {
        "id": "ROOT_CHILD",
        "name": "top.txt",
        "size": 1,
        "createdDateTime": "2024-01-01T00:00:00Z",
        "lastModifiedDateTime": "2024-01-01T00:00:00Z",
        "parentReference": {"path": "/drive/root:"},
        "file": {"mimeType": "text/plain"},
    }
    row = normalize_item("drive-X", item)
    assert row["parent_path"] == "/"
    assert row["full_path"] == "/top.txt"


def test_normalize_missing_user_email_falls_back_to_displayName() -> None:
    item = {
        "id": "X",
        "name": "y",
        "size": 0,
        "createdDateTime": "2024-01-01T00:00:00Z",
        "lastModifiedDateTime": "2024-01-01T00:00:00Z",
        "parentReference": {"path": "/drive/root:"},
        "file": {},
        "createdBy": {"user": {"displayName": "Alice"}},
        "lastModifiedBy": {"application": {"displayName": "SyncEngine"}},
    }
    row = normalize_item("drive-X", item)
    assert row["created_by"] == "Alice"
    assert row["modified_by"] == "SyncEngine"
