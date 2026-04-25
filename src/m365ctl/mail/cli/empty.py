"""`m365ctl mail empty <folder>` — IRREVERSIBLE folder empty.

This is NOT `mail-delete` — these operations are IRREVERSIBLE.

Hard-delete every message in a folder, with full per-message EML capture
under ``[logging].purged_dir``. Multiple guards apply, in order:

1. ``--confirm`` is mandatory; without it exit 2.
2. Folder must resolve via ``resolve_folder_path``.
3. If ``totalItemCount == 0`` → exit 0 with stderr ``"(folder is empty)"``.
4. If the folder is one of the well-known common folders (Inbox,
   Sent Items, Drafts, Archive, Outbox), require ``--unsafe-common-folder``;
   otherwise exit 1.
5. If ``totalItemCount >= 1000``, require the operator to type the exact
   phrase ``"YES DELETE <count>"`` on /dev/tty.
6. Otherwise, require the operator to type ``"YES"`` on /dev/tty.

Without an available /dev/tty, the CLI exits 1 with stderr
``"requires TTY confirm; this is irreversible"``.
"""
from __future__ import annotations

import argparse
import sys

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args, load_and_authorize
from m365ctl.mail.folders import FolderNotFound, get_folder, resolve_folder_path
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.clean import execute_empty_folder


_DESCRIPTION = (
    "This is NOT 'mail delete' — these operations are IRREVERSIBLE. "
    "Hard-delete every message in a folder (with per-message EML "
    "capture). Common folders require --unsafe-common-folder. Folders "
    "with >=1000 items require an extra TTY phrase including the count."
)


_COMMON_FOLDERS = {"Inbox", "Sent Items", "Drafts", "Archive", "Outbox"}


def _tty_prompt(message: str) -> str:
    """Open /dev/tty, print ``message``, read one line, return it stripped.

    Raises ``IOError`` when /dev/tty cannot be opened.
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
        prog="m365ctl mail empty",
        description=_DESCRIPTION,
    )
    add_common_args(p)
    p.add_argument(
        "folder_path",
        help="Folder path or well-known name (e.g. 'Archive/2024', 'inbox').",
    )
    p.add_argument(
        "--confirm", action="store_true",
        help="Required to proceed. A TTY-typed phrase is also required.",
    )
    p.add_argument(
        "--unsafe-common-folder", action="store_true",
        help="Required when targeting a common folder "
             "(Inbox, Sent Items, Drafts, Archive, Outbox).",
    )
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if not args.confirm:
        print(
            "mail empty: --confirm is required (this is IRREVERSIBLE).",
            file=sys.stderr,
        )
        return 2

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope, folder_path=args.folder_path,
    )
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    # Resolve + fetch metadata for pre-flight gates.
    try:
        folder_id = resolve_folder_path(
            args.folder_path, graph,
            mailbox_spec=args.mailbox, auth_mode=auth_mode,
        )
    except FolderNotFound as e:
        print(f"mail empty: {e}", file=sys.stderr)
        return 2

    folder = get_folder(
        graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        folder_id=folder_id, path=args.folder_path,
    )
    total = folder.total_items

    # Gate 1: empty folder fast-exit.
    if total == 0:
        print("(folder is empty)", file=sys.stderr)
        return 0

    # Gate 2: common-folder warning.
    if folder.display_name in _COMMON_FOLDERS and not args.unsafe_common_folder:
        print(
            f"mail empty: {folder.display_name!r} is a common folder; "
            f"refusing without --unsafe-common-folder. This will permanently "
            f"delete {total} message(s).",
            file=sys.stderr,
        )
        return 1

    # Gate 3 / 4: TTY phrase.
    if total >= 1000:
        expected = f"YES DELETE {total}"
        prompt = (
            f"This will permanently delete {total} messages from "
            f"{args.folder_path!r}. Type {expected!r} to confirm: "
        )
    else:
        expected = "YES"
        prompt = (
            f"Type 'YES' to permanently delete {total} message(s) from "
            f"{args.folder_path!r} (this is IRREVERSIBLE): "
        )

    try:
        answer = _tty_prompt(prompt)
    except IOError:
        print(
            "requires TTY confirm; this is irreversible",
            file=sys.stderr,
        )
        return 1

    if answer != expected:
        print(
            f"aborted: expected {expected!r}, got something else.",
            file=sys.stderr,
        )
        return 1

    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    op = Operation(
        op_id=new_op_id(),
        action="mail.empty.folder",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id=folder_id,
        args={
            "mailbox_spec": args.mailbox,
            "auth_mode": auth_mode,
            "folder_id": folder_id,
            "folder_path": args.folder_path,
        },
    )
    result = execute_empty_folder(
        op, graph, logger, purged_dir=cfg.logging.purged_dir,
    )
    if result.status != "ok":
        print(f"[{op.op_id}] error: {result.error}", file=sys.stderr)
        return 1
    purged = result.after.get("purged_count", 0)
    print(
        f"[{op.op_id}] ok — emptied {args.folder_path!r} "
        f"({purged} messages)"
    )
    return 0


__all__ = ["main", "build_parser"]
