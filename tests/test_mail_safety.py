"""Tests for m365ctl.common.safety — mailbox + folder gates."""
from __future__ import annotations

from pathlib import Path

import pytest

from m365ctl.common.config import (
    CatalogConfig,
    Config,
    LoggingConfig,
    MailConfig,
    ScopeConfig,
)
from m365ctl.common.safety import (
    HARDCODED_DENY_FOLDERS,
    ScopeViolation,
    assert_mailbox_allowed,
    is_folder_denied,
)


def _cfg(allow_mailboxes: list[str], deny_folders: list[str] | None = None) -> Config:
    return Config(
        tenant_id="00000000-0000-0000-0000-000000000000",
        client_id="11111111-1111-1111-1111-111111111111",
        cert_path=Path("/tmp/x.key"),
        cert_public=Path("/tmp/x.cer"),
        default_auth="delegated",
        scope=ScopeConfig(
            allow_drives=["me"],
            allow_mailboxes=allow_mailboxes,
            deny_folders=deny_folders or [],
        ),
        catalog=CatalogConfig(path=Path("cache/catalog.duckdb"), refresh_on_start=False),
        mail=MailConfig(catalog_path=Path("cache/mail.duckdb")),
        logging=LoggingConfig(ops_dir=Path("logs/ops")),
    )


# ---- assert_mailbox_allowed ------------------------------------------------

def test_me_allowed_when_me_in_list():
    cfg = _cfg(allow_mailboxes=["me"])
    assert_mailbox_allowed("me", cfg, auth_mode="delegated", unsafe_scope=False)


def test_me_rejected_when_not_in_list():
    cfg = _cfg(allow_mailboxes=["upn:boss@example.com"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed("me", cfg, auth_mode="delegated", unsafe_scope=False)


def test_upn_matches_upn_in_list():
    cfg = _cfg(allow_mailboxes=["upn:alice@example.com"])
    assert_mailbox_allowed("upn:alice@example.com", cfg, auth_mode="app-only", unsafe_scope=False)


def test_upn_case_insensitive():
    cfg = _cfg(allow_mailboxes=["upn:alice@example.com"])
    assert_mailbox_allowed("upn:ALICE@example.com", cfg, auth_mode="app-only", unsafe_scope=False)


def test_shared_matches_shared_in_list():
    cfg = _cfg(allow_mailboxes=["shared:ops@example.com"])
    assert_mailbox_allowed("shared:ops@example.com", cfg, auth_mode="delegated", unsafe_scope=False)


def test_shared_does_not_match_upn_entry():
    cfg = _cfg(allow_mailboxes=["upn:ops@example.com"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed("shared:ops@example.com", cfg, auth_mode="delegated", unsafe_scope=False)


def test_wildcard_star_requires_app_only():
    cfg = _cfg(allow_mailboxes=["*"])
    with pytest.raises(ScopeViolation) as ei:
        assert_mailbox_allowed("upn:random@example.com", cfg, auth_mode="delegated", unsafe_scope=False)
    assert "app-only" in str(ei.value).lower()


def test_wildcard_star_allows_app_only():
    cfg = _cfg(allow_mailboxes=["*"])
    assert_mailbox_allowed("upn:random@example.com", cfg, auth_mode="app-only", unsafe_scope=False)


def test_unsafe_scope_still_rejects_without_tty():
    cfg = _cfg(allow_mailboxes=["me"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed("upn:other@example.com", cfg, auth_mode="app-only", unsafe_scope=True)


def test_assume_yes_bypasses_mailbox_tty():
    from unittest.mock import patch
    cfg = _cfg(allow_mailboxes=["me"])
    with patch("m365ctl.common.safety._confirm_via_tty") as m:
        assert_mailbox_allowed(
            "upn:other@example.com", cfg, auth_mode="app-only",
            unsafe_scope=True, assume_yes=True,
        )
        m.assert_not_called()


def test_assume_yes_does_not_bypass_mailbox_unsafe_scope_flag():
    cfg = _cfg(allow_mailboxes=["me"])
    with pytest.raises(ScopeViolation, match="not in scope.allow_mailboxes"):
        assert_mailbox_allowed(
            "upn:other@example.com", cfg, auth_mode="app-only",
            unsafe_scope=False, assume_yes=True,
        )


# ---- is_folder_denied ------------------------------------------------------

@pytest.mark.parametrize("path", [
    "Recoverable Items",
    "Recoverable Items/Deletions",
    "Purges",
    "Purges/a/b/c",
    "Audits",
    "Calendar",
    "Calendar/Work",
    "Contacts",
    "Tasks",
    "Notes",
])
def test_is_folder_denied_hardcoded_hits(path):
    cfg = _cfg(allow_mailboxes=["me"])
    assert is_folder_denied(path, cfg), f"{path!r} should be denied"


@pytest.mark.parametrize("path", [
    "Inbox",
    "Inbox/Triage",
    "Sent Items",
    "Drafts",
    "Archive/2026",
    "",
])
def test_is_folder_denied_allows_normal_paths(path):
    cfg = _cfg(allow_mailboxes=["me"])
    assert not is_folder_denied(path, cfg), f"{path!r} should be allowed"


def test_is_folder_denied_user_config_pattern():
    cfg = _cfg(allow_mailboxes=["me"], deny_folders=["Archive/Legal/*"])
    assert is_folder_denied("Archive/Legal/2026", cfg)
    assert not is_folder_denied("Archive/2026", cfg)


def test_hardcoded_deny_folders_list_is_frozen():
    assert isinstance(HARDCODED_DENY_FOLDERS, frozenset)


def test_mail_list_fails_fast_when_mailbox_not_in_allow_list(tmp_path):
    """Scope enforcement: listing a mailbox outside allow_mailboxes raises before Graph."""
    import pytest
    from m365ctl.common.safety import ScopeViolation
    from m365ctl.mail.cli.list import main

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "/tmp/nonexistent.key"
cert_public  = "/tmp/nonexistent.cer"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "cache/catalog.duckdb"

[mail]
catalog_path = "cache/mail.duckdb"

[logging]
ops_dir = "logs/ops"
""".lstrip())

    # --mailbox upn:other@example.com is NOT in allow_mailboxes=["me"].
    # assert_mailbox_allowed raises ScopeViolation (no TTY → no /dev/tty confirm path available).
    with pytest.raises(ScopeViolation):
        main(["--config", str(cfg_path), "--mailbox", "upn:other@example.com"])
