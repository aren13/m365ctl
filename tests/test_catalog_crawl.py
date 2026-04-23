from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.catalog.crawl import (
    CrawlResult,
    DriveSpec,
    crawl_drive,
    resolve_scope,
)
from fazla_od.catalog.db import open_catalog


def test_resolve_scope_me_calls_me_drive(mocker) -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "id": "drive-me-id",
        "driveType": "business",
        "owner": {"user": {"email": "arda@fazla.com"}},
        "name": "OneDrive - Fazla",
    }
    drives = resolve_scope("me", graph)
    assert drives == [
        DriveSpec(
            drive_id="drive-me-id",
            display_name="OneDrive - Fazla",
            owner="arda@fazla.com",
            drive_type="business",
            graph_path="/me/drive/root/delta",
        )
    ]
    graph.get.assert_called_once_with("/me/drive")


def test_resolve_scope_drive_by_id() -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "id": "drive-xyz",
        "driveType": "documentLibrary",
        "owner": {"user": {"email": "site-owner@fazla.com"}},
        "name": "Finance",
    }
    drives = resolve_scope("drive:drive-xyz", graph)
    assert drives[0].drive_id == "drive-xyz"
    assert drives[0].graph_path == "/drives/drive-xyz/root/delta"
    graph.get.assert_called_once_with("/drives/drive-xyz")


def test_resolve_scope_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError, match="unknown scope"):
        resolve_scope("site:Finance", MagicMock())


def test_crawl_drive_inserts_items_and_stores_delta_link(tmp_path: Path) -> None:
    drive = DriveSpec(
        drive_id="d1",
        display_name="OneDrive - Fazla",
        owner="arda@fazla.com",
        drive_type="business",
        graph_path="/me/drive/root/delta",
    )
    graph = MagicMock()
    graph.get_paginated.return_value = iter(
        [
            (
                [
                    {
                        "id": "i1",
                        "name": "a.txt",
                        "size": 10,
                        "createdDateTime": "2024-01-01T00:00:00Z",
                        "lastModifiedDateTime": "2024-01-02T00:00:00Z",
                        "parentReference": {"path": "/drive/root:"},
                        "file": {"mimeType": "text/plain"},
                    },
                    {
                        "id": "i2",
                        "name": "Folder",
                        "createdDateTime": "2024-01-01T00:00:00Z",
                        "lastModifiedDateTime": "2024-01-01T00:00:00Z",
                        "parentReference": {"path": "/drive/root:"},
                        "folder": {"childCount": 0},
                    },
                ],
                "https://graph/delta?token=DELTA1",
            )
        ]
    )

    db_path = tmp_path / "catalog.duckdb"
    with open_catalog(db_path) as conn:
        result = crawl_drive(drive, graph, conn)

    assert isinstance(result, CrawlResult)
    assert result.items_seen == 2
    assert result.delta_link == "https://graph/delta?token=DELTA1"

    with open_catalog(db_path) as conn:
        rows = conn.execute(
            "SELECT item_id, is_folder, is_deleted FROM items ORDER BY item_id"
        ).fetchall()
        assert rows == [("i1", False, False), ("i2", True, False)]
        (link,) = conn.execute(
            "SELECT delta_link FROM drives WHERE drive_id = 'd1'"
        ).fetchone()
        assert link == "https://graph/delta?token=DELTA1"


def test_crawl_drive_uses_stored_delta_link_on_second_call(tmp_path: Path) -> None:
    drive = DriveSpec(
        drive_id="d1",
        display_name="OneDrive - Fazla",
        owner="arda@fazla.com",
        drive_type="business",
        graph_path="/me/drive/root/delta",
    )
    db_path = tmp_path / "catalog.duckdb"

    # first crawl
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([], "https://graph/delta?token=FIRST")])
    with open_catalog(db_path) as conn:
        crawl_drive(drive, graph, conn)

    # second crawl should call get_paginated with the stored deltaLink URL
    graph2 = MagicMock()
    graph2.get_paginated.return_value = iter([([], "https://graph/delta?token=SECOND")])
    with open_catalog(db_path) as conn:
        crawl_drive(drive, graph2, conn)

    called_path = graph2.get_paginated.call_args[0][0]
    assert called_path == "https://graph/delta?token=FIRST"


def test_crawl_drive_handles_deleted_tombstones(tmp_path: Path) -> None:
    drive = DriveSpec(
        drive_id="d1",
        display_name="X",
        owner="o",
        drive_type="business",
        graph_path="/me/drive/root/delta",
    )
    graph = MagicMock()
    graph.get_paginated.return_value = iter(
        [
            (
                [
                    {
                        "id": "gone",
                        "name": "",
                        "deleted": {"state": "deleted"},
                        "parentReference": {"path": "/drive/root:"},
                    }
                ],
                "https://graph/delta?token=D",
            )
        ]
    )
    db_path = tmp_path / "catalog.duckdb"
    with open_catalog(db_path) as conn:
        crawl_drive(drive, graph, conn)
        (is_deleted,) = conn.execute(
            "SELECT is_deleted FROM items WHERE item_id='gone'"
        ).fetchone()
        assert is_deleted is True
