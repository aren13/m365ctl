"""Tests for m365ctl.mail.cli._common helpers."""
from __future__ import annotations

from m365ctl.mail.cli._common import derive_mailbox_upn


def test_derive_me():
    assert derive_mailbox_upn("me") == "me"


def test_derive_upn():
    assert derive_mailbox_upn("upn:alice@example.com") == "alice@example.com"


def test_derive_shared():
    assert derive_mailbox_upn("shared:team@example.com") == "team@example.com"


def test_derive_passthrough():
    assert derive_mailbox_upn("alice@example.com") == "alice@example.com"
