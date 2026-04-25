"""`m365ctl mail clean` — IRREVERSIBLE hard-delete + EML capture.

This is NOT `mail-delete` — these operations are IRREVERSIBLE.

Two forms:

    mail clean <message-id>     # hard-delete one message
    mail clean recycle-bin      # empty Deleted Items

Both require BOTH ``--confirm`` AND a TTY confirmation. Without
``--confirm``, exit 2. With ``--confirm`` but no TTY available, exit 1
with stderr ``"requires TTY confirm; this is irreversible"``. With
``--confirm`` AND TTY, prompt for the literal string ``YES`` and proceed
only on exact match. Anything else aborts.

Every wire-delete is preceded by a full EML capture under
``[logging].purged_dir``; the capture is the only recovery path outside
Graph and these ops are NOT undoable.
"""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.clean import (
    execute_empty_recycle_bin,
    execute_hard_delete,
)


_DESCRIPTION = (
    "This is NOT 'mail delete' — these operations are IRREVERSIBLE. "
    "Hard-delete a single message (with EML capture) or empty the "
    "Deleted Items recycle bin. Both require --confirm AND a TTY 'YES'."
)


def _tty_prompt(message: str) -> str:
    """Open /dev/tty, print ``message``, read one line, return it stripped.

    Raises ``IOError`` when /dev/tty cannot be opened — the CLI converts
    that into exit-1 with the standard irreversible-confirm error.
    """
    try:
        reader = open("/dev/tty", "r")
        writer = open("/dev/tty", "w")
    except OSError as exc:
        raise IOError("cannot open /dev/tty") from exc
    try:
        writer.write(message)
        writer.flush()
        return (reader.readline() or "").strip()
    finally:
        reader.close()
        writer.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="m365ctl mail clean",
        description=_DESCRIPTION,
    )
    add_common_args(p)
    p.add_argument(
        "target",
        help="Either a Graph message id, or the literal 'recycle-bin' "
             "to empty Deleted Items.",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Required to proceed. A TTY 'YES' is also required.",
    )
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if not args.confirm:
        print(
            "mail clean: --confirm is required (this is IRREVERSIBLE).",
            file=sys.stderr,
        )
        return 2

    target = args.target
    is_recycle = target == "recycle-bin"

    if is_recycle:
        prompt = (
            "Type 'YES' to permanently empty the recycle bin "
            "(this is IRREVERSIBLE): "
        )
    else:
        prompt = (
            f"Type 'YES' to permanently delete {target} "
            f"(this is IRREVERSIBLE): "
        )

    try:
        answer = _tty_prompt(prompt)
    except IOError:
        print(
            "requires TTY confirm; this is irreversible",
            file=sys.stderr,
        )
        return 1

    if answer != "YES":
        print("aborted: did not type 'YES'.", file=sys.stderr)
        return 1

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    drive_id = derive_mailbox_upn(args.mailbox)

    if is_recycle:
        op = Operation(
            op_id=new_op_id(),
            action="mail.empty.recycle-bin",
            drive_id=drive_id,
            item_id="deleteditems",
            args={
                "mailbox_spec": args.mailbox,
                "auth_mode": auth_mode,
            },
        )
        result = execute_empty_recycle_bin(
            op, graph, logger, purged_dir=cfg.logging.purged_dir,
        )
    else:
        op = Operation(
            op_id=new_op_id(),
            action="mail.delete.hard",
            drive_id=drive_id,
            item_id=target,
            args={
                "mailbox_spec": args.mailbox,
                "auth_mode": auth_mode,
                "message_id": target,
            },
        )
        result = execute_hard_delete(
            op, graph, logger, purged_dir=cfg.logging.purged_dir,
        )

    if result.status != "ok":
        print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
        return 1
    if is_recycle:
        purged = result.after.get("purged_count", 0)
        print(f"[{op.op_id}] ok — emptied recycle bin ({purged} messages)")
    else:
        print(f"[{op.op_id}] ok — hard-deleted {target}")
    return 0


__all__ = ["main", "build_parser"]
