from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.cli.auth import run_whoami


def test_whoami_prints_both_flows(tmp_path: Path, mocker, capsys) -> None:
    cfg = MagicMock()
    cfg.tenant_id = "tenant-uuid"
    cfg.client_id = "client-uuid"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    mocker.patch("m365ctl.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.return_value = "deleg"
    mocker.patch("m365ctl.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=FazlaODToolkit"
    app_only.cert_info.thumbprint = "ABCDEF"
    app_only.cert_info.days_until_expiry = 728
    app_only.cert_info.not_after_utc = "2028-04-22T22:12:10+00:00"
    app_only.get_token.return_value = "app"
    mocker.patch("m365ctl.cli.auth.AppOnlyCredential", return_value=app_only)

    graph = MagicMock()
    graph.get.side_effect = [
        {"displayName": "Arda Eren", "userPrincipalName": "arda@fazla.com"},
        {"displayName": "FazlaODToolkit"},
    ]
    mocker.patch("m365ctl.cli.auth.GraphClient", return_value=graph)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "Arda Eren" in out
    assert "arda@fazla.com" in out
    assert "FazlaODToolkit" in out
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
    mocker.patch("m365ctl.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.side_effect = AuthError("not logged in; run `od-auth login` first")
    mocker.patch("m365ctl.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=x"
    app_only.cert_info.thumbprint = "AB"
    app_only.cert_info.days_until_expiry = 700
    app_only.cert_info.not_after_utc = "2028-01-01T00:00:00+00:00"
    app_only.get_token.return_value = "app"
    mocker.patch("m365ctl.cli.auth.AppOnlyCredential", return_value=app_only)

    graph = MagicMock()
    graph.get.return_value = {"displayName": "FazlaODToolkit"}
    mocker.patch("m365ctl.cli.auth.GraphClient", return_value=graph)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "not logged in" in out.lower()
    assert "FazlaODToolkit" in out
