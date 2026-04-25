"""Phase 4.x: undo of mail-delete-soft must recover from Graph's id rotation.

When `mail delete --message-id <id>` moves a message to Deleted Items, Graph
assigns the message a NEW id at the destination. The audit log records the
OLD id; a naive `undo` then 404s when looking up the message. The fix:
record `internetMessageId` in the audit `before` block, and on undo, if the
literal id 404s, locate the rotated id in Deleted Items via
`?$filter=internetMessageId eq '...'`.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.common.graph import GraphError
from m365ctl.mail.cli.undo import run_undo_mail


def _stub_cfg(tmp_path: Path):
    from m365ctl.common.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="delegated",
        scope=ScopeConfig(allow_drives=["*"], allow_users=["*"],
                          deny_paths=[], unsafe_requires_flag=True),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def _seed_soft_delete_op(
    logger: AuditLogger,
    *,
    op_id: str = "D1",
    item_id: str = "old-id-rotated-away",
    internet_message_id: str = "<abc@example.com>",
) -> None:
    log_mutation_start(
        logger, op_id=op_id, cmd="mail-delete-soft",
        args={}, drive_id="me", item_id=item_id,
        before={
            "parent_folder_id": "inbox",
            "parent_folder_path": "/Inbox",
            "internet_message_id": internet_message_id,
        },
    )
    log_mutation_end(
        logger, op_id=op_id,
        after={"parent_folder_id": "deleteditems-id", "deleted_from": "inbox"},
        result="ok",
    )


def test_undo_recovers_via_internet_message_id_when_old_id_404s(tmp_path, mocker):
    """First lookup 404s (id rotated). Helper finds rotated id in Deleted Items.
    Move uses the resolved id."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.mail.cli.undo.load_config", return_value=cfg)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    _seed_soft_delete_op(logger)

    fake_cred = MagicMock()
    fake_cred.get_token.return_value = "tok"
    mocker.patch("m365ctl.mail.cli.undo._build_credential", return_value=fake_cred)
    mocker.patch("m365ctl.mail.cli.undo.GraphClient", return_value=MagicMock())

    # First get_message 404s; we then call find_by_internet_message_id which
    # returns the rotated id; second get_message succeeds.
    rotated_id = "rotated-id-99"

    def _get_message_side_effect(graph, *, mailbox_spec, auth_mode, message_id, **kwargs):
        if message_id == "old-id-rotated-away":
            raise GraphError("ErrorItemNotFound: The specified object was not found in the store.")
        msg = MagicMock()
        msg.parent_folder_id = "deleteditems-id"
        msg.parent_folder_path = "/Deleted Items"
        return msg

    mocker.patch("m365ctl.mail.cli.undo.get_message",
                 side_effect=_get_message_side_effect)
    find_helper = mocker.patch(
        "m365ctl.mail.cli.undo.find_by_internet_message_id",
        return_value=rotated_id,
    )

    fake_result = MagicMock(status="ok", op_id="rev-uid", error=None)
    ex_move = mocker.patch("m365ctl.mail.mutate.move.execute_move",
                           return_value=fake_result)

    rc = run_undo_mail(config_path=tmp_path / "config.toml",
                       op_id="D1", confirm=True)
    assert rc == 0
    find_helper.assert_called_once()
    # The find_helper must search Deleted Items by the recorded internetMessageId.
    kwargs = find_helper.call_args.kwargs
    assert kwargs["folder_id"] == "deleteditems"
    assert kwargs["internet_message_id"] == "<abc@example.com>"
    # Move was called against the resolved (rotated) id.
    ex_move.assert_called_once()
    op_arg = ex_move.call_args.args[0]
    assert op_arg.item_id == rotated_id


def test_undo_uses_recorded_id_directly_when_lookup_succeeds(tmp_path, mocker):
    """If the original id still resolves (rare — eg. Graph didn't rotate),
    we don't need the recovery helper at all."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.mail.cli.undo.load_config", return_value=cfg)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    _seed_soft_delete_op(logger, item_id="still-valid")

    fake_cred = MagicMock()
    fake_cred.get_token.return_value = "tok"
    mocker.patch("m365ctl.mail.cli.undo._build_credential", return_value=fake_cred)
    mocker.patch("m365ctl.mail.cli.undo.GraphClient", return_value=MagicMock())

    msg = MagicMock()
    msg.parent_folder_id = "deleteditems-id"
    msg.parent_folder_path = "/Deleted Items"
    mocker.patch("m365ctl.mail.cli.undo.get_message", return_value=msg)
    find_helper = mocker.patch("m365ctl.mail.cli.undo.find_by_internet_message_id")

    fake_result = MagicMock(status="ok", op_id="rev-uid", error=None)
    ex_move = mocker.patch("m365ctl.mail.mutate.move.execute_move",
                           return_value=fake_result)

    rc = run_undo_mail(config_path=tmp_path / "config.toml",
                       op_id="D1", confirm=True)
    assert rc == 0
    find_helper.assert_not_called()
    op_arg = ex_move.call_args.args[0]
    assert op_arg.item_id == "still-valid"


def test_undo_surfaces_clear_error_when_neither_path_works(tmp_path, mocker, capsys):
    """If literal id 404s AND Deleted Items search returns no hit, fail with a
    clear message — the message may have been hard-deleted or moved manually."""
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.mail.cli.undo.load_config", return_value=cfg)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    _seed_soft_delete_op(logger)

    fake_cred = MagicMock()
    fake_cred.get_token.return_value = "tok"
    mocker.patch("m365ctl.mail.cli.undo._build_credential", return_value=fake_cred)
    mocker.patch("m365ctl.mail.cli.undo.GraphClient", return_value=MagicMock())

    mocker.patch(
        "m365ctl.mail.cli.undo.get_message",
        side_effect=GraphError("ErrorItemNotFound: ..."),
    )
    mocker.patch("m365ctl.mail.cli.undo.find_by_internet_message_id",
                 return_value=None)
    ex_move = mocker.patch("m365ctl.mail.mutate.move.execute_move")

    rc = run_undo_mail(config_path=tmp_path / "config.toml",
                       op_id="D1", confirm=True)
    assert rc != 0
    ex_move.assert_not_called()
    err = capsys.readouterr().err.lower()
    assert "deleted items" in err or "not found" in err
    assert "manually" in err or "hard-deleted" in err or "hard deleted" in err


def test_undo_falls_back_to_anywhere_search_when_not_in_deleted_items(
    tmp_path, mocker, capsys,
):
    """When DeletedItems lookup misses, search the whole mailbox.

    Reverse-of-soft-delete records ``destination_id`` as the original-folder id.
    If the user has manually moved the message out of Deleted Items between
    delete and undo, ``find_by_internet_message_id`` (folder-scoped) returns
    None; ``find_message_anywhere`` then locates it. The move proceeds with
    the rotated id.
    """
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.mail.cli.undo.load_config", return_value=cfg)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    _seed_soft_delete_op(logger)

    fake_cred = MagicMock()
    fake_cred.get_token.return_value = "tok"
    mocker.patch("m365ctl.mail.cli.undo._build_credential", return_value=fake_cred)
    mocker.patch("m365ctl.mail.cli.undo.GraphClient", return_value=MagicMock())

    # First get_message 404s; the broader search resolves the rotated id and
    # reports a different parent folder (not the restore target).
    rotated_id = "rotated-id-7"

    def _get_message_side_effect(graph, *, mailbox_spec, auth_mode, message_id, **kwargs):
        if message_id == "old-id-rotated-away":
            raise GraphError("ErrorItemNotFound: ...")
        msg = MagicMock()
        msg.parent_folder_id = "fld-archive"
        msg.parent_folder_path = "/Archive"
        return msg

    mocker.patch("m365ctl.mail.cli.undo.get_message",
                 side_effect=_get_message_side_effect)
    mocker.patch("m365ctl.mail.cli.undo.find_by_internet_message_id",
                 return_value=None)
    anywhere = mocker.patch(
        "m365ctl.mail.cli.undo.find_message_anywhere",
        return_value=(rotated_id, "fld-archive"),
    )

    fake_result = MagicMock(status="ok", op_id="rev-uid", error=None)
    ex_move = mocker.patch("m365ctl.mail.mutate.move.execute_move",
                           return_value=fake_result)

    rc = run_undo_mail(config_path=tmp_path / "config.toml",
                       op_id="D1", confirm=True)
    assert rc == 0
    anywhere.assert_called_once()
    kwargs = anywhere.call_args.kwargs
    assert kwargs["internet_message_id"] == "<abc@example.com>"
    # execute_move was called with the rotated id.
    ex_move.assert_called_once()
    op_arg = ex_move.call_args.args[0]
    assert op_arg.item_id == rotated_id
    err = capsys.readouterr().err
    # The stderr notice names the discovered folder.
    assert "fld-archive" in err
    assert "manually moved" in err.lower() or "restoring from there" in err.lower()


def test_undo_no_op_when_message_already_in_target_folder(
    tmp_path, mocker, capsys,
):
    """User manually moved the message back to the original folder.

    ``find_message_anywhere`` reports the current parent folder equals the
    reverse op's ``destination_id`` (the restore target). The undo emits a
    "nothing to do" stderr notice, exits 0, and does NOT call execute_move.
    """
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.mail.cli.undo.load_config", return_value=cfg)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    _seed_soft_delete_op(logger)

    fake_cred = MagicMock()
    fake_cred.get_token.return_value = "tok"
    mocker.patch("m365ctl.mail.cli.undo._build_credential", return_value=fake_cred)
    mocker.patch("m365ctl.mail.cli.undo.GraphClient", return_value=MagicMock())

    mocker.patch(
        "m365ctl.mail.cli.undo.get_message",
        side_effect=GraphError("ErrorItemNotFound: ..."),
    )
    mocker.patch("m365ctl.mail.cli.undo.find_by_internet_message_id",
                 return_value=None)
    # Rotated id found, parent equals "inbox" (the seeded restore target).
    mocker.patch(
        "m365ctl.mail.cli.undo.find_message_anywhere",
        return_value=("rotated-id-back-home", "inbox"),
    )
    ex_move = mocker.patch("m365ctl.mail.mutate.move.execute_move")

    rc = run_undo_mail(config_path=tmp_path / "config.toml",
                       op_id="D1", confirm=True)
    assert rc == 0
    ex_move.assert_not_called()
    err = capsys.readouterr().err.lower()
    assert "already in original folder" in err
    assert "nothing to do" in err
