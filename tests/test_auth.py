from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.auth import (
    AppOnlyCredential,
    AuthError,
    CertInfo,
    DelegatedCredential,
    get_cert_info,
)
from fazla_od.config import Config, load_config

_SKIPIF_LIVE = pytest.mark.skipif(
    os.environ.get("FAZLA_OD_LIVE_TESTS") != "1",
    reason="live Graph test; set FAZLA_OD_LIVE_TESTS=1 to run",
)


def LIVE(fn):
    """Mark a test as live (hits real Graph) AND skip unless env var set."""
    return _SKIPIF_LIVE(pytest.mark.live(fn))


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Minimal valid config pointing at a fake cert file."""
    cert = tmp_path / "fake.key"
    cert.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    pub = tmp_path / "fake.cer"
    pub.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    toml = tmp_path / "config.toml"
    toml.write_text(
        f"""
tenant_id    = "tenant-uuid"
client_id    = "client-uuid"
cert_path    = "{cert}"
cert_public  = "{pub}"
default_auth = "app-only"

[scope]
allow_drives = ["me"]
"""
    )
    return load_config(toml)


def test_app_only_acquires_token_using_cert(
    cfg: Config, tmp_path: Path, mocker
) -> None:
    # Stub the cert thumbprint helper -- we don't want to parse a fake cert here.
    mocker.patch(
        "fazla_od.auth.get_cert_info",
        return_value=CertInfo(
            subject="CN=Test",
            thumbprint="ABCDEF",
            not_after_utc="2028-04-22T22:12:10Z",
            days_until_expiry=999,
        ),
    )
    mock_app = MagicMock()
    mock_app.acquire_token_for_client.return_value = {"access_token": "t0k3n"}
    mocker.patch("msal.ConfidentialClientApplication", return_value=mock_app)

    cred = AppOnlyCredential(cfg)
    token = cred.get_token()

    assert token == "t0k3n"
    mock_app.acquire_token_for_client.assert_called_once_with(
        scopes=["https://graph.microsoft.com/.default"]
    )


def test_app_only_raises_on_msal_error(cfg: Config, mocker) -> None:
    mocker.patch(
        "fazla_od.auth.get_cert_info",
        return_value=CertInfo("CN=x", "AB", "2028-01-01T00:00:00Z", 900),
    )
    mock_app = MagicMock()
    mock_app.acquire_token_for_client.return_value = {
        "error": "invalid_client",
        "error_description": "cert not uploaded",
    }
    mocker.patch("msal.ConfidentialClientApplication", return_value=mock_app)

    cred = AppOnlyCredential(cfg)
    with pytest.raises(AuthError, match="invalid_client"):
        cred.get_token()


@LIVE
def test_live_app_only_against_fazla_tenant() -> None:
    """Smoke test: real cert, real Entra. Requires config.toml + cert on disk."""
    cfg = load_config(Path("config.toml"))
    cred = AppOnlyCredential(cfg)
    token = cred.get_token()
    assert isinstance(token, str) and len(token) > 100


def test_delegated_login_uses_device_code_flow(cfg: Config, mocker) -> None:
    mock_app = MagicMock()
    mock_app.initiate_device_flow.return_value = {
        "user_code": "ABCD-1234",
        "device_code": "dev-code",
        "verification_uri": "https://microsoft.com/devicelogin",
        "message": "Go to https://microsoft.com/devicelogin and enter ABCD-1234",
        "expires_in": 900,
        "interval": 5,
    }
    mock_app.acquire_token_by_device_flow.return_value = {
        "access_token": "delegated-t0k3n",
        "id_token_claims": {"preferred_username": "test@fazla.com"},
    }
    mock_app.get_accounts.return_value = []
    mocker.patch("msal.PublicClientApplication", return_value=mock_app)
    mocker.patch("fazla_od.auth._load_persistent_cache", return_value=None)

    printed: list[str] = []
    cred = DelegatedCredential(cfg, prompt=lambda msg: printed.append(msg))
    token = cred.login()

    assert token == "delegated-t0k3n"
    assert any("ABCD-1234" in m for m in printed)
    mock_app.acquire_token_by_device_flow.assert_called_once()


def test_delegated_get_token_uses_cached_account(cfg: Config, mocker) -> None:
    mock_app = MagicMock()
    mock_app.get_accounts.return_value = [{"username": "cached@fazla.com"}]
    mock_app.acquire_token_silent.return_value = {"access_token": "cached-t0k3n"}
    mocker.patch("msal.PublicClientApplication", return_value=mock_app)
    mocker.patch("fazla_od.auth._load_persistent_cache", return_value=None)

    cred = DelegatedCredential(cfg)
    token = cred.get_token()

    assert token == "cached-t0k3n"
    mock_app.initiate_device_flow.assert_not_called()


def test_delegated_get_token_raises_when_not_logged_in(cfg: Config, mocker) -> None:
    mock_app = MagicMock()
    mock_app.get_accounts.return_value = []
    mocker.patch("msal.PublicClientApplication", return_value=mock_app)
    mocker.patch("fazla_od.auth._load_persistent_cache", return_value=None)

    cred = DelegatedCredential(cfg)
    with pytest.raises(AuthError, match="not logged in"):
        cred.get_token()
