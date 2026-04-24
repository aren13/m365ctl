from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.onedrive.cli.download import run_download
from m365ctl.onedrive.download.fetcher import FetchResult


def _cfg(tmp_path: Path):
    cfg = MagicMock()
    cfg.catalog.path = tmp_path / "c.duckdb"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    return cfg


def test_download_single_item(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.download.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("m365ctl.onedrive.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))
    captured = []

    def fake_fetch(*, drive_id, item_id, dest, token_provider, overwrite, **_):
        captured.append((drive_id, item_id, dest, overwrite))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"X" * 10)
        return FetchResult(drive_id, item_id, dest, 10, False)

    mocker.patch("m365ctl.onedrive.cli.download.fetch_item", side_effect=fake_fetch)

    dest = tmp_path / "out"
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id="i1", drive_id="d1",
        from_plan=None, query=None,
        dest=dest, overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 0
    assert captured[0][0] == "d1"
    assert captured[0][1] == "i1"


def test_download_from_plan(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.download.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("m365ctl.onedrive.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([
        {"action": "download", "drive_id": "d", "item_id": "i1",
         "args": {"full_path": "/A/a.pdf"}},
        {"action": "download", "drive_id": "d", "item_id": "i2",
         "args": {"full_path": "/A/b.pdf"}},
    ]))

    calls: list[tuple[str, str, Path]] = []

    def fake_fetch(*, drive_id, item_id, dest, **_):
        calls.append((drive_id, item_id, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
        return FetchResult(drive_id, item_id, dest, 0, False)

    mocker.patch("m365ctl.onedrive.cli.download.fetch_item", side_effect=fake_fetch)

    dest = tmp_path / "out"
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id=None, drive_id=None,
        from_plan=plan, query=None,
        dest=dest, overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 0
    # Relative path preservation.
    dests = sorted(c[2] for c in calls)
    assert dests[0].name == "a.pdf"
    assert dests[1].name == "b.pdf"


def test_download_query_emits_plan_out(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.download.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("m365ctl.onedrive.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))

    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, is_folder, "
            "is_deleted) VALUES ('d','i','a.pdf','/A/a.pdf',false,false)"
        )

    mocker.patch(
        "m365ctl.onedrive.cli.download.fetch_item",
        side_effect=AssertionError("fetch_item should not be called in plan-out mode"),
    )
    plan_out = tmp_path / "plan.json"
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id=None, drive_id=None,
        from_plan=None,
        query="SELECT drive_id, item_id, full_path FROM items WHERE name = 'a.pdf'",
        dest=tmp_path / "out", overwrite=False, concurrency=2,
        plan_out=plan_out, scope="me",
    )
    assert rc == 0
    assert plan_out.exists()
    rows = json.loads(plan_out.read_text())
    assert rows[0]["action"] == "download"
    assert rows[0]["item_id"] == "i"


def test_download_requires_exactly_one_source(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.download.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.download.AppOnlyCredential", return_value=MagicMock())
    mocker.patch("m365ctl.onedrive.cli.download.DelegatedCredential", return_value=MagicMock())
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id=None, drive_id=None,
        from_plan=None, query=None,
        dest=tmp_path / "out", overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 2


def test_download_dest_defaults_to_timestamped_workspace(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.download.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("m365ctl.onedrive.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))

    captured: list[Path] = []

    def fake_fetch(*, drive_id, item_id, dest, **_):
        captured.append(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
        return FetchResult(drive_id, item_id, dest, 0, False)

    mocker.patch("m365ctl.onedrive.cli.download.fetch_item", side_effect=fake_fetch)
    monkey_now = "20260424-101530"
    mocker.patch("m365ctl.onedrive.cli.download._timestamp", return_value=monkey_now)

    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id="i1", drive_id="d1",
        from_plan=None, query=None,
        dest=None, overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 0
    # Default dest is workspaces/download-<ts>/ relative to cwd; we only care
    # that the timestamped dir name is used.
    assert f"download-{monkey_now}" in str(captured[0])
