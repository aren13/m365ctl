"""MSAL-backed authentication for fazla_od.

Two flows, both using the same Azure AD app registration:

- ``AppOnlyCredential``: certificate-based client_credentials. Used for
  tenant-wide unattended operations.
- ``DelegatedCredential``: device-code flow with persistent token cache.
  Used when operations should be attributed to the signed-in user.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import msal
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from fazla_od.config import Config

GRAPH_SCOPES_APP_ONLY = ["https://graph.microsoft.com/.default"]
GRAPH_SCOPES_DELEGATED = [
    "Files.ReadWrite.All",
    "Sites.ReadWrite.All",
    "User.Read",
]

_CACHE_DIR = Path.home() / ".config" / "fazla-od"
_CACHE_FILE = _CACHE_DIR / "token_cache.bin"


class AuthError(RuntimeError):
    """Raised when token acquisition fails."""


@dataclass(frozen=True)
class CertInfo:
    subject: str
    thumbprint: str  # SHA-1, uppercase hex, no colons
    not_after_utc: str  # ISO-8601
    days_until_expiry: int


def get_cert_info(cert_public: Path) -> CertInfo:
    """Parse a PEM cert and return its identifying metadata."""
    pem = cert_public.read_bytes()
    cert = x509.load_pem_x509_certificate(pem)
    thumb = cert.fingerprint(hashes.SHA1()).hex().upper()
    not_after = cert.not_valid_after_utc
    days = (not_after - datetime.now(timezone.utc)).days
    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        thumbprint=thumb,
        not_after_utc=not_after.isoformat(),
        days_until_expiry=days,
    )


class AppOnlyCredential:
    """Certificate-based client_credentials flow for tenant-wide ops."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._info = get_cert_info(cfg.cert_public)

    @property
    def cert_info(self) -> CertInfo:
        return self._info

    def _build_app(self) -> msal.ConfidentialClientApplication:
        private_key = self._cfg.cert_path.read_text()
        client_credential = {
            "private_key": private_key,
            "thumbprint": self._info.thumbprint,
            "public_certificate": self._cfg.cert_public.read_text(),
        }
        authority = f"https://login.microsoftonline.com/{self._cfg.tenant_id}"
        return msal.ConfidentialClientApplication(
            client_id=self._cfg.client_id,
            authority=authority,
            client_credential=client_credential,
        )

    def get_token(self) -> str:
        app = self._build_app()
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPES_APP_ONLY)
        if "access_token" not in result:
            err = result.get("error", "unknown")
            desc = result.get("error_description", "")
            raise AuthError(f"app-only auth failed: {err}: {desc}")
        return result["access_token"]


def _load_persistent_cache() -> msal.SerializableTokenCache | None:
    """Load the MSAL token cache from disk, if present.

    We use a plain file at mode 600 rather than msal-extensions' Keychain
    integration because the latter has historical stability issues on
    macOS. The file sits in ~/.config/fazla-od/ alongside the cert and
    inherits the directory's 700 permissions.
    """
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_text())
    return cache


def _persist_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    _CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    _CACHE_FILE.write_text(cache.serialize())
    os.chmod(_CACHE_FILE, 0o600)


class DelegatedCredential:
    """Device-code flow with persistent token cache."""

    def __init__(
        self,
        cfg: Config,
        prompt: Callable[[str], None] = print,
    ) -> None:
        self._cfg = cfg
        self._prompt = prompt
        self._cache = _load_persistent_cache() or msal.SerializableTokenCache()
        authority = f"https://login.microsoftonline.com/{cfg.tenant_id}"
        self._app = msal.PublicClientApplication(
            client_id=cfg.client_id,
            authority=authority,
            token_cache=self._cache,
        )

    def login(self) -> str:
        flow = self._app.initiate_device_flow(scopes=GRAPH_SCOPES_DELEGATED)
        if "user_code" not in flow:
            raise AuthError(f"could not start device flow: {flow!r}")
        self._prompt(flow["message"])
        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            err = result.get("error", "unknown")
            desc = result.get("error_description", "")
            raise AuthError(f"device-code auth failed: {err}: {desc}")
        _persist_cache(self._cache)
        return result["access_token"]

    def get_token(self) -> str:
        accounts = self._app.get_accounts()
        if not accounts:
            raise AuthError("not logged in; run `od-auth login` first")
        result = self._app.acquire_token_silent(
            scopes=GRAPH_SCOPES_DELEGATED, account=accounts[0]
        )
        if not result or "access_token" not in result:
            raise AuthError(
                "cached token could not be refreshed; run `od-auth login` again"
            )
        _persist_cache(self._cache)
        return result["access_token"]

    def logout(self) -> None:
        for acc in self._app.get_accounts():
            self._app.remove_account(acc)
        _persist_cache(self._cache)
