from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.catalog.crawl import CrawlResult, DriveSpec
from fazla_od.cli.catalog import run_refresh, run_status


def _stub_config(tmp_path: Path):
    cfg = MagicMock()
    cfg.tenant_id = "t"
    cfg.client_id = "c"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    cfg.catalog.path = tmp_path / "catalog.duckdb"
    return cfg


def test_run_refresh_me_uses_delegated_and_crawls(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.return_value = "deleg-token"
    mocker.patch("fazla_od.cli.catalog.DelegatedCredential", return_value=delegated)
    app_only = MagicMock()
    mocker.patch("fazla_od.cli.catalog.AppOnlyCredential", return_value=app_only)

    mocker.patch(
        "fazla_od.cli.catalog.resolve_scope",
        return_value=[
            DriveSpec(
                drive_id="d1",
                display_name="OneDrive",
                owner="arda@fazla.com",
                drive_type="business",
                graph_path="/me/drive/root/delta",
            )
        ],
    )
    mocker.patch(
        "fazla_od.cli.catalog.crawl_drive",
        return_value=CrawlResult(
            drive_id="d1", items_seen=42, delta_link="https://x/delta?t=1"
        ),
    )

    rc = run_refresh(
        config_path=tmp_path / "config.toml",
        scope="me",
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert "d1" in out
    assert "42" in out
    delegated.get_token.assert_called_once()
    app_only.get_token.assert_not_called()


def test_run_refresh_drive_uses_app_only(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)

    delegated = MagicMock()
    mocker.patch("fazla_od.cli.catalog.DelegatedCredential", return_value=delegated)
    app_only = MagicMock()
    app_only.get_token.return_value = "app-token"
    mocker.patch("fazla_od.cli.catalog.AppOnlyCredential", return_value=app_only)

    mocker.patch(
        "fazla_od.cli.catalog.resolve_scope",
        return_value=[
            DriveSpec(
                drive_id="dx",
                display_name="Finance",
                owner="owner@fazla.com",
                drive_type="documentLibrary",
                graph_path="/drives/dx/root/delta",
            )
        ],
    )
    mocker.patch(
        "fazla_od.cli.catalog.crawl_drive",
        return_value=CrawlResult(drive_id="dx", items_seen=7, delta_link="d"),
    )

    rc = run_refresh(
        config_path=tmp_path / "config.toml",
        scope="drive:dx",
    )
    assert rc == 0
    delegated.get_token.assert_not_called()
    app_only.get_token.assert_called_once()


def test_run_status_prints_summary(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)

    # Seed a catalog
    from fazla_od.catalog.db import open_catalog

    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO drives (drive_id, display_name, owner, drive_type, "
            "delta_link, last_refreshed_at) VALUES "
            "('d1','OneDrive','arda@fazla.com','business','dlink', CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, is_folder, is_deleted, size) "
            "VALUES ('d1','i1','a.txt', false, false, 100), "
            "       ('d1','i2','b.txt', false, false, 200), "
            "       ('d1','f',  'fld',   true,  false, null)"
        )

    rc = run_status(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "d1" in out
    # Should show: 1 drive, 3 items (2 files), 300 bytes
    assert "3" in out
    assert "300" in out or "300 B" in out
