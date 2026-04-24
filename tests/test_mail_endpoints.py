"""Unit tests for m365ctl.mail.endpoints."""
from __future__ import annotations

import pytest

from m365ctl.mail.endpoints import (
    AuthMode,
    InvalidMailboxSpec,
    user_base,
    parse_mailbox_spec,
)


def test_user_base_me_delegated():
    assert user_base("me", auth_mode="delegated") == "/me"


def test_user_base_upn_app_only():
    assert user_base("upn:alice@example.com", auth_mode="app-only") == "/users/alice@example.com"


def test_user_base_shared_delegated():
    assert user_base("shared:team@example.com", auth_mode="delegated") == "/users/team@example.com"


def test_user_base_rejects_star_wildcard():
    with pytest.raises(InvalidMailboxSpec):
        user_base("*", auth_mode="delegated")


def test_user_base_rejects_me_under_app_only():
    with pytest.raises(InvalidMailboxSpec):
        user_base("me", auth_mode="app-only")


def test_user_base_upn_delegated_allowed():
    assert user_base("upn:bob@example.com", auth_mode="delegated") == "/users/bob@example.com"


@pytest.mark.parametrize("spec,expected", [
    ("me", ("me", None)),
    ("upn:alice@example.com", ("upn", "alice@example.com")),
    ("shared:ops@example.com", ("shared", "ops@example.com")),
    ("*", ("*", None)),
])
def test_parse_mailbox_spec_shapes(spec, expected):
    assert parse_mailbox_spec(spec) == expected


def test_parse_mailbox_spec_rejects_garbage():
    with pytest.raises(InvalidMailboxSpec):
        parse_mailbox_spec("random-text-no-colon")


def test_parse_mailbox_spec_rejects_upn_without_address():
    with pytest.raises(InvalidMailboxSpec):
        parse_mailbox_spec("upn:")


def test_parse_mailbox_spec_rejects_shared_without_address():
    with pytest.raises(InvalidMailboxSpec):
        parse_mailbox_spec("shared:notanemail")
