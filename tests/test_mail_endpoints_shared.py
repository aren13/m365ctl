"""Integration coverage for ``shared:<addr>`` mailbox specs end-to-end.

Verifies that readers + mutators in mail/* route shared: specs to
``/users/<addr>/...`` paths, and that ``assert_mailbox_allowed`` requires
exact (kind, address) match (i.e. ``shared:`` does not auto-promote to
``upn:`` and vice versa).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from m365ctl.common.config import (
    CatalogConfig,
    Config,
    LoggingConfig,
    MailConfig,
    ScopeConfig,
)
from m365ctl.common.safety import ScopeViolation, assert_mailbox_allowed
from m365ctl.mail.folders import list_folders, resolve_folder_path
from m365ctl.mail.messages import list_messages
from m365ctl.mail.settings import get_settings, update_mailbox_settings


# ---- shared:<addr> URL routing -------------------------------------------

def test_list_messages_routes_shared_to_users_addr():
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([], None)])
    list(
        list_messages(
            graph,
            mailbox_spec="shared:team@example.com",
            auth_mode="app-only",
            folder_id="inbox",
            parent_folder_path="Inbox",
        )
    )
    first_call_path = graph.get_paginated.call_args_list[0].args[0]
    assert first_call_path == "/users/team@example.com/mailFolders/inbox/messages"


def test_list_folders_routes_shared_to_users_addr():
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([], None)])
    list(
        list_folders(
            graph,
            mailbox_spec="shared:team@example.com",
            auth_mode="app-only",
        )
    )
    first_call_path = graph.get_paginated.call_args_list[0].args[0]
    assert first_call_path == "/users/team@example.com/mailFolders"


def test_get_settings_routes_shared_to_users_addr():
    graph = MagicMock()
    graph.get.return_value = {}
    get_settings(
        graph,
        mailbox_spec="shared:team@example.com",
        auth_mode="app-only",
    )
    assert graph.get.call_args.args[0] == "/users/team@example.com/mailboxSettings"


def test_update_mailbox_settings_routes_shared_to_users_addr():
    graph = MagicMock()
    graph.patch.return_value = {}
    update_mailbox_settings(
        graph,
        mailbox_spec="shared:team@example.com",
        auth_mode="app-only",
        body={"timeZone": "UTC"},
    )
    assert graph.patch.call_args.args[0] == "/users/team@example.com/mailboxSettings"


def test_resolve_folder_path_routes_shared_to_users_addr():
    graph = MagicMock()
    graph.get.return_value = {"id": "AAMk-folder-id"}
    resolve_folder_path(
        "inbox",
        graph,
        mailbox_spec="shared:team@example.com",
        auth_mode="app-only",
    )
    assert graph.get.call_args.args[0] == "/users/team@example.com/mailFolders/inbox"


# ---- assert_mailbox_allowed: shared: vs upn: are distinct ----------------

def _cfg(allow_mailboxes: list[str]) -> Config:
    return Config(
        tenant_id="00000000-0000-0000-0000-000000000000",
        client_id="11111111-1111-1111-1111-111111111111",
        cert_path=Path("/tmp/x.key"),
        cert_public=Path("/tmp/x.cer"),
        default_auth="app-only",
        scope=ScopeConfig(
            allow_drives=["me"],
            allow_mailboxes=allow_mailboxes,
        ),
        catalog=CatalogConfig(path=Path("cache/catalog.duckdb"), refresh_on_start=False),
        mail=MailConfig(catalog_path=Path("cache/mail.duckdb")),
        logging=LoggingConfig(ops_dir=Path("logs/ops")),
    )


def test_shared_entry_permits_matching_shared_spec():
    cfg = _cfg(allow_mailboxes=["shared:team@example.com"])
    # No raise.
    assert_mailbox_allowed(
        "shared:team@example.com", cfg, auth_mode="app-only", unsafe_scope=False,
    )
    # Different shared address rejected.
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed(
            "shared:other@example.com", cfg, auth_mode="app-only", unsafe_scope=False,
        )


def test_upn_entry_does_not_auto_permit_shared_for_same_address():
    cfg = _cfg(allow_mailboxes=["upn:team@example.com"])
    with pytest.raises(ScopeViolation):
        assert_mailbox_allowed(
            "shared:team@example.com", cfg, auth_mode="app-only", unsafe_scope=False,
        )
