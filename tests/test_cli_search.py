from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


from m365ctl.onedrive.catalog.crawl import DriveSpec
from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.onedrive.cli.search import run_search
from m365ctl.onedrive.search.graph_search import SearchHit


def _cfg(tmp_path: Path):
    cfg = MagicMock()
    cfg.catalog.path = tmp_path / "c.duckdb"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    return cfg


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, full_path, is_folder,
                               is_deleted, size, modified_at, modified_by)
            VALUES
              ('d', 'c1', 'local-invoice.pdf', '/L/local-invoice.pdf', false, false,
               100, TIMESTAMP '2024-03-01 00:00:00', 'a@example.com')
            """
        )


def test_search_merges_graph_and_catalog_json(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.search.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.search.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "app"))
    mocker.patch("m365ctl.onedrive.cli.search.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "deleg"))
    mocker.patch("m365ctl.onedrive.cli.search.GraphClient", return_value=MagicMock())

    mocker.patch(
        "m365ctl.onedrive.cli.search.graph_search",
        return_value=iter(
            [
                SearchHit("d", "g1", "Graph-invoice.pdf",
                          "/G/Graph-invoice.pdf", 200,
                          "2024-08-01T00:00:00Z", None, False, "graph"),
            ]
        ),
    )
    _seed(cfg.catalog.path)

    rc = run_search(
        config_path=tmp_path / "config.toml",
        query="invoice",
        scope="me",
        type_="file",
        modified_since=None,
        owner=None,
        limit=50,
        as_json=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    names = [r["name"] for r in parsed]
    # Graph hit (newer) first, local second.
    assert names[:2] == ["Graph-invoice.pdf", "local-invoice.pdf"]


def test_search_scope_tenant_uses_app_only_and_filters(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.search.load_config", return_value=cfg)
    delegated = MagicMock()
    app_only = MagicMock()
    app_only.get_token.return_value = "app"
    mocker.patch("m365ctl.onedrive.cli.search.DelegatedCredential", return_value=delegated)
    mocker.patch("m365ctl.onedrive.cli.search.AppOnlyCredential", return_value=app_only)
    mocker.patch("m365ctl.onedrive.cli.search.GraphClient", return_value=MagicMock())
    mocker.patch("m365ctl.onedrive.cli.search.resolve_scope",
                 return_value=[DriveSpec("dx", "dn", "o", "business",
                                         "/drives/dx/root/delta")])
    # Graph returns one hit on drive 'dx' and one on drive 'other'; only dx survives.
    mocker.patch(
        "m365ctl.onedrive.cli.search.graph_search",
        return_value=iter([
            SearchHit("dx", "in-scope", "A", "/A", 1, "2024-01-01T00:00:00Z",
                      None, False, "graph"),
            SearchHit("other", "out", "B", "/B", 1, "2024-02-01T00:00:00Z",
                      None, False, "graph"),
        ]),
    )
    rc = run_search(
        config_path=tmp_path / "config.toml",
        query="x",
        scope="tenant",
        type_="file",
        modified_since=None,
        owner=None,
        limit=50,
        as_json=True,
    )
    assert rc == 0
    delegated.get_token.assert_not_called()
    app_only.get_token.assert_called()


def test_search_tsv_output_has_header(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.search.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.search.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "app"))
    mocker.patch("m365ctl.onedrive.cli.search.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "deleg"))
    mocker.patch("m365ctl.onedrive.cli.search.GraphClient", return_value=MagicMock())
    mocker.patch("m365ctl.onedrive.cli.search.graph_search", return_value=iter([]))
    _seed(cfg.catalog.path)

    run_search(
        config_path=tmp_path / "config.toml",
        query="invoice",
        scope="me",
        type_="file",
        modified_since=None,
        owner=None,
        limit=10,
        as_json=False,
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("drive_id\titem_id\t")
    assert "local-invoice.pdf" in out[1]


def test_search_limit_truncates(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.search.load_config", return_value=cfg)
    mocker.patch("m365ctl.onedrive.cli.search.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "app"))
    mocker.patch("m365ctl.onedrive.cli.search.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "deleg"))
    mocker.patch("m365ctl.onedrive.cli.search.GraphClient", return_value=MagicMock())
    mocker.patch(
        "m365ctl.onedrive.cli.search.graph_search",
        return_value=iter([
            SearchHit("d", f"g{i}", f"n{i}", f"/n{i}", 1,
                      f"2024-{i+1:02d}-01T00:00:00Z", None, False, "graph")
            for i in range(5)
        ]),
    )
    _seed(cfg.catalog.path)
    run_search(
        config_path=tmp_path / "config.toml",
        query="n",
        scope="me",
        type_="file",
        modified_since=None,
        owner=None,
        limit=2,
        as_json=True,
    )
    parsed = json.loads(capsys.readouterr().out)
    assert len(parsed) == 2
