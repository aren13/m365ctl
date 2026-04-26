from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from m365ctl.onedrive.cli.auth import run_whoami


def test_whoami_prints_both_flows(tmp_path: Path, mocker, capsys) -> None:
    cfg = MagicMock()
    cfg.tenant_id = "tenant-uuid"
    cfg.client_id = "client-uuid"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    cfg.catalog.path = tmp_path / "catalog.duckdb"  # doesn't exist
    mocker.patch("m365ctl.onedrive.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.return_value = "deleg"
    mocker.patch("m365ctl.onedrive.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=m365ctl-test"
    app_only.cert_info.thumbprint = "ABCDEF"
    app_only.cert_info.days_until_expiry = 728
    app_only.cert_info.not_after_utc = "2028-04-22T22:12:10+00:00"
    app_only.get_token.return_value = "app"
    mocker.patch("m365ctl.onedrive.cli.auth.AppOnlyCredential", return_value=app_only)

    graph = MagicMock()
    graph.get.side_effect = [
        {"displayName": "Arda Eren", "userPrincipalName": "arda@example.com"},
        {"displayName": "m365ctl-test"},
    ]
    mocker.patch("m365ctl.onedrive.cli.auth.GraphClient", return_value=graph)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "Arda Eren" in out
    assert "arda@example.com" in out
    assert "m365ctl-test" in out
    assert "ABCDEF" in out
    assert "728" in out
    assert "tenant-uuid" in out


def test_whoami_reports_not_logged_in(tmp_path: Path, mocker, capsys) -> None:
    from m365ctl.common.auth import AuthError

    cfg = MagicMock()
    cfg.tenant_id = "t"
    cfg.client_id = "c"
    cfg.cert_public = tmp_path / "c"
    cfg.cert_path = tmp_path / "k"
    cfg.catalog.path = tmp_path / "catalog.duckdb"  # doesn't exist
    mocker.patch("m365ctl.onedrive.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.side_effect = AuthError("not logged in; run `od-auth login` first")
    mocker.patch("m365ctl.onedrive.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=x"
    app_only.cert_info.thumbprint = "AB"
    app_only.cert_info.days_until_expiry = 700
    app_only.cert_info.not_after_utc = "2028-01-01T00:00:00+00:00"
    app_only.get_token.return_value = "app"
    mocker.patch("m365ctl.onedrive.cli.auth.AppOnlyCredential", return_value=app_only)

    graph = MagicMock()
    graph.get.return_value = {"displayName": "m365ctl-test"}
    mocker.patch("m365ctl.onedrive.cli.auth.GraphClient", return_value=graph)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "not logged in" in out.lower()
    assert "m365ctl-test" in out
    # When the catalog file is missing, whoami should suggest building it
    # rather than print stale internal-planning text.
    assert "not built yet" in out
    assert "od-catalog-refresh" in out


def test_whoami_reports_existing_catalog_size(tmp_path: Path, mocker, capsys) -> None:
    from m365ctl.common.auth import AuthError

    catalog_file = tmp_path / "catalog.duckdb"
    catalog_file.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

    cfg = MagicMock()
    cfg.tenant_id = "t"
    cfg.client_id = "c"
    cfg.cert_public = tmp_path / "c"
    cfg.cert_path = tmp_path / "k"
    cfg.catalog.path = catalog_file
    mocker.patch("m365ctl.onedrive.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.side_effect = AuthError("not logged in")
    mocker.patch("m365ctl.onedrive.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=x"
    app_only.cert_info.thumbprint = "AB"
    app_only.cert_info.days_until_expiry = 700
    app_only.cert_info.not_after_utc = "2028-01-01T00:00:00+00:00"
    app_only.get_token.side_effect = AuthError("no app-only token")
    mocker.patch("m365ctl.onedrive.cli.auth.AppOnlyCredential", return_value=app_only)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert str(catalog_file) in out
    assert "2.0 MB" in out
    assert "not built yet" not in out
