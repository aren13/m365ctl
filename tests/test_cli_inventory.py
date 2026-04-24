from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.onedrive.cli.inventory import run_inventory


def _stub_config(tmp_path: Path):
    cfg = MagicMock()
    cfg.catalog.path = tmp_path / "catalog.duckdb"
    return cfg


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, is_folder, is_deleted,
                               size, modified_at, modified_by, quick_xor_hash)
            VALUES
              ('d', '1', 'big.mp4',  false, false, 5000000000, TIMESTAMP '2023-03-01 00:00:00', 'alice@example.com', 'H1'),
              ('d', '2', 'mid.zip',  false, false, 1000000000, TIMESTAMP '2024-01-01 00:00:00', 'alice@example.com', 'H2'),
              ('d', '3', 'dup-a',    false, false, 500,        TIMESTAMP '2024-10-01 00:00:00', 'bob@example.com',   'DUP'),
              ('d', '4', 'dup-b',    false, false, 500,        TIMESTAMP '2024-10-02 00:00:00', 'bob@example.com',   'DUP')
            """
        )


def test_top_by_size_tsv(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.inventory.load_config", return_value=cfg)
    _seed(cfg.catalog.path)
    rc = run_inventory(
        config_path=tmp_path / "config.toml",
        top_by_size=3,
        stale_since=None,
        by_owner=False,
        duplicates=False,
        sql=None,
        as_json=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "big.mp4" in out
    assert "5000000000" in out
    # TSV: first line is header, second is biggest
    lines = out.strip().splitlines()
    assert "\t" in lines[0]


def test_top_by_size_json(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.inventory.load_config", return_value=cfg)
    _seed(cfg.catalog.path)
    run_inventory(
        config_path=tmp_path / "config.toml",
        top_by_size=2,
        stale_since=None,
        by_owner=False,
        duplicates=False,
        sql=None,
        as_json=True,
    )
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "big.mp4"


def test_by_owner(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.inventory.load_config", return_value=cfg)
    _seed(cfg.catalog.path)
    run_inventory(
        config_path=tmp_path / "config.toml",
        top_by_size=None,
        stale_since=None,
        by_owner=True,
        duplicates=False,
        sql=None,
        as_json=True,
    )
    out = capsys.readouterr().out
    parsed = json.loads(out)
    owners = {r["owner"]: r["total_size"] for r in parsed}
    assert owners["alice@example.com"] == 6000000000
    assert owners["bob@example.com"] == 1000


def test_duplicates(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.inventory.load_config", return_value=cfg)
    _seed(cfg.catalog.path)
    run_inventory(
        config_path=tmp_path / "config.toml",
        top_by_size=None,
        stale_since=None,
        by_owner=False,
        duplicates=True,
        sql=None,
        as_json=True,
    )
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert len(parsed) == 2
    assert {r["name"] for r in parsed} == {"dup-a", "dup-b"}


def test_sql_passthrough(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.inventory.load_config", return_value=cfg)
    _seed(cfg.catalog.path)
    run_inventory(
        config_path=tmp_path / "config.toml",
        top_by_size=None,
        stale_since=None,
        by_owner=False,
        duplicates=False,
        sql="SELECT COUNT(*) AS n FROM items WHERE is_folder = false",
        as_json=True,
    )
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == [{"n": 4}]


def test_requires_exactly_one_mode(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.inventory.load_config", return_value=cfg)
    rc = run_inventory(
        config_path=tmp_path / "config.toml",
        top_by_size=None,
        stale_since=None,
        by_owner=False,
        duplicates=False,
        sql=None,
        as_json=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "exactly one" in err.lower()
