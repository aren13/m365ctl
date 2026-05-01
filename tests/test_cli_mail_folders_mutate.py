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


def test_folders_move_to_root_uses_msgfolderroot(tmp_path, monkeypatch):
    """`move <child> <root-sentinel>` sends destinationId=msgfolderroot.

    Per #33, "", "/", "root", "msgfolderroot" must all be accepted as the
    destination and pass through to Graph as the well-known root id without
    a path-resolution step.
    """
    import argparse
    from unittest.mock import MagicMock

    from m365ctl.mail.cli.folders import _run_move

    # Stub the dependencies that hit Graph or the FS.
    cfg = MagicMock()
    cfg.logging.ops_dir = tmp_path / "ops"

    def fake_load_and_authorize(_args):
        cred = MagicMock()
        cred.get_token.return_value = "tok"
        return cfg, "delegated", cred

    monkeypatch.setattr(
        "m365ctl.mail.cli.folders.load_and_authorize", fake_load_and_authorize,
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.folders.assert_mail_target_allowed",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.folders._preauth_deny_check", lambda *_a, **_kw: None,
    )

    # resolve_folder_path is called for the SOURCE (and the parent_path lookup)
    # — but should NOT be called for the destination when it's a sentinel.
    resolved_paths: list[str] = []

    def fake_resolve(path, *_a, **_kw):
        resolved_paths.append(path)
        return f"folder-id-of-{path}"

    monkeypatch.setattr(
        "m365ctl.mail.cli.folders.resolve_folder_path", fake_resolve,
    )

    # Capture the Operation passed to execute_move_folder.
    captured: dict = {}

    def fake_execute(op, _graph, _logger, *, before):
        captured["op"] = op
        captured["before"] = before
        from m365ctl.mail.mutate._common import MailResult
        return MailResult(op_id=op.op_id, status="ok")

    monkeypatch.setattr(
        "m365ctl.mail.cli.folders.execute_move_folder", fake_execute,
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.folders._build_audit_logger", lambda _cfg: MagicMock(),
    )
    monkeypatch.setattr(
        "m365ctl.mail.cli.folders.GraphClient", lambda **_kw: MagicMock(),
    )

    for sentinel in ("", "/", "root", "msgfolderroot"):
        captured.clear()
        resolved_paths.clear()
        args = argparse.Namespace(
            mailbox="me", path="Inbox/Newsletter", new_parent_path=sentinel,
            unsafe_scope=False, confirm=True, config=str(tmp_path / "x.toml"),
        )
        rc = _run_move(args)
        assert rc == 0, f"move to {sentinel!r} should succeed"
        assert captured["op"].args["destination_id"] == "msgfolderroot", (
            f"sentinel {sentinel!r} should resolve to msgfolderroot, "
            f"got {captured['op'].args['destination_id']!r}"
        )
        # The destination sentinel must NOT have triggered resolve_folder_path —
        # only the source path (and possibly its parent for `before`) should.
        assert sentinel not in resolved_paths, (
            f"resolve_folder_path was called for sentinel {sentinel!r} "
            f"(expected to bypass)"
        )
