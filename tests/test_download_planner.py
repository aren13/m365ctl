from __future__ import annotations

import json
from pathlib import Path

import pytest

from m365ctl.catalog.db import open_catalog
from m365ctl.download.planner import (
    DownloadItem,
    PlanFileError,
    load_plan_file,
    plan_from_query,
    plan_from_single,
    write_plan_file,
)


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, full_path, is_folder,
                               is_deleted, size)
            VALUES
              ('d', 'i1', 'a.pdf', '/A/a.pdf', false, false, 100),
              ('d', 'i2', 'b.pdf', '/A/b.pdf', false, false, 200),
              ('d', 'f',  'A',     '/A',       true,  false, null)
            """
        )


def test_plan_from_query_returns_items(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        items = plan_from_query(
            conn,
            "SELECT drive_id, item_id, full_path FROM items "
            "WHERE is_folder = false AND name LIKE '%.pdf' ORDER BY item_id",
        )
    assert items == [
        DownloadItem(drive_id="d", item_id="i1", full_path="/A/a.pdf"),
        DownloadItem(drive_id="d", item_id="i2", full_path="/A/b.pdf"),
    ]


def test_plan_from_query_rejects_missing_columns(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        with pytest.raises(PlanFileError, match="drive_id"):
            plan_from_query(conn, "SELECT item_id FROM items")


def test_plan_from_single_builds_one_item() -> None:
    item = plan_from_single(drive_id="d", item_id="i", full_path="/x")
    assert item == DownloadItem(drive_id="d", item_id="i", full_path="/x")


def test_write_and_load_plan_file_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    items = [
        DownloadItem("d", "i1", "/a.pdf"),
        DownloadItem("d", "i2", "/b.pdf"),
    ]
    write_plan_file(p, items)
    raw = json.loads(p.read_text())
    assert raw[0] == {"action": "download", "drive_id": "d", "item_id": "i1",
                      "args": {"full_path": "/a.pdf"}}
    loaded = load_plan_file(p)
    assert loaded == items


def test_load_plan_file_rejects_non_download_actions(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps([
        {"action": "move", "drive_id": "d", "item_id": "i", "args": {}}
    ]))
    with pytest.raises(PlanFileError, match="action"):
        load_plan_file(p)


def test_load_plan_file_rejects_bad_shape(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(PlanFileError, match="list"):
        load_plan_file(p)
