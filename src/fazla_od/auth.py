"""MSAL-backed authentication for fazla_od.

Two flows, both using the same Azure AD app registration:

- ``AppOnlyCredential``: certificate-based client_credentials. Used for
  tenant-wide unattended operations.
- ``DelegatedCredential``: device-code flow with persistent token cache.
  Used when operations should be attributed to the signed-in user.

Delegated credential is added in a later step; this file is split by
concern so the mock-based unit tests can land independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import msal
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from fazla_od.config import Config

GRAPH_SCOPES_APP_ONLY = ["https://graph.microsoft.com/.default"]


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


# DelegatedCredential is added in Task 7.
class DelegatedCredential:  # placeholder -- implemented in Task 7
    def __init__(self, cfg: Config) -> None:
        raise NotImplementedError("DelegatedCredential lands in Task 7")
