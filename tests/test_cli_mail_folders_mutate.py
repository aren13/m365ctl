"""Parser + scope-gate tests for `m365ctl mail folders {create,rename,move,delete}`."""
from __future__ import annotations

import pytest

from m365ctl.mail.cli.folders import build_parser


def test_folders_list_still_works_with_no_subcommand():
    args = build_parser().parse_args([])
    assert args.subcommand is None
    assert args.tree is False


def test_folders_list_still_works_with_tree_flag():
    args = build_parser().parse_args(["--tree"])
    assert args.subcommand is None
    assert args.tree is True


def test_folders_create_subparser():
    args = build_parser().parse_args(["create", "/Inbox", "Triage", "--confirm"])
    assert args.subcommand == "create"
    assert args.parent_path == "/Inbox"
    assert args.name == "Triage"
    assert args.confirm is True


def test_folders_create_requires_both_positional():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["create", "/Inbox"])


def test_folders_rename_subparser():
    args = build_parser().parse_args(["rename", "/Inbox/Triage", "Triaged", "--confirm"])
    assert args.subcommand == "rename"
    assert args.path == "/Inbox/Triage"
    assert args.new_name == "Triaged"


def test_folders_move_subparser():
    args = build_parser().parse_args(["move", "/Inbox/Triage", "/Archive", "--confirm"])
    assert args.subcommand == "move"
    assert args.path == "/Inbox/Triage"
    assert args.new_parent_path == "/Archive"


def test_folders_delete_subparser():
    args = build_parser().parse_args(["delete", "/Archive/Old", "--confirm"])
    assert args.subcommand == "delete"
    assert args.path == "/Archive/Old"
    assert args.confirm is True


def test_folders_mutations_without_confirm_default_dry_run():
    args = build_parser().parse_args(["create", "/Inbox", "X"])
    assert args.confirm is False


def test_folders_deny_folder_blocked_before_graph(tmp_path):
    """Attempting to create under Calendar/ (hardcoded deny) fails fast."""
    from m365ctl.common.safety import ScopeViolation
    from m365ctl.mail.cli.folders import main

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

    with pytest.raises(ScopeViolation):
        main(["--config", str(cfg_path), "create", "/Calendar", "Evil", "--confirm"])
