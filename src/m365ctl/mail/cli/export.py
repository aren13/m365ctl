"""`m365ctl mail export {message, folder, mailbox, attachments}` — Phase 11 G4.

Read-only export verbs. No mutations, no audit/undo plumbing.

Subcommands:
    message <id>    --out <path.eml>           per-message EML
    folder  <path>  --out <path.mbox>          per-folder streaming MBOX
    mailbox         --out-dir <dir>            full mailbox + manifest.json
    attachments <id> --out-dir <dir>           file attachments to a directory
                     [--include-inline]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.graph import GraphClient
from m365ctl.mail.cli._common import (
    add_common_args,
    derive_mailbox_upn,
    load_and_authorize,
)
from m365ctl.mail.export.attachments import export_attachments
from m365ctl.mail.export.eml import export_message_to_eml
from m365ctl.mail.export.mailbox import export_mailbox
from m365ctl.mail.export.mbox import export_folder_to_mbox
from m365ctl.mail.folders import resolve_folder_path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail export")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)

    msg = sub.add_parser("message", help="Export one message to a .eml file.")
    msg.add_argument("message_id")
    msg.add_argument("--out", dest="out", required=True,
                     help="Destination .eml path.")

    fld = sub.add_parser("folder", help="Export one folder to a .mbox file.")
    fld.add_argument("folder_path",
                     help="Folder path or well-known name (e.g. 'Inbox').")
    fld.add_argument("--out", dest="out", required=True,
                     help="Destination .mbox path.")

    mbx = sub.add_parser("mailbox", help="Export every folder + manifest.json.")
    mbx.add_argument("--out-dir", dest="out_dir", required=True,
                     help="Destination directory for per-folder mboxes + manifest.")

    att = sub.add_parser("attachments",
                         help="Dump file attachments of a message to a directory.")
    att.add_argument("message_id")
    att.add_argument("--out-dir", dest="out_dir", required=True,
                     help="Destination directory.")
    att.add_argument("--include-inline", dest="include_inline", action="store_true",
                     help="Include inline attachments (default: skip).")
    return p


def _run_message(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    out_path = Path(args.out)
    written = export_message_to_eml(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        message_id=args.message_id,
        out_path=out_path,
    )
    print(f"wrote {written}")
    return 0


def _run_folder(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    folder_id = resolve_folder_path(
        args.folder_path, graph,
        mailbox_spec=args.mailbox, auth_mode=auth_mode,
    )
    out_path = Path(args.out)
    count = export_folder_to_mbox(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        folder_id=folder_id,
        folder_path=args.folder_path,
        out_path=out_path,
    )
    print(f"wrote {count} message(s) to {out_path}")
    return 0


def _run_mailbox(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    out_dir = Path(args.out_dir)
    mailbox_upn = derive_mailbox_upn(args.mailbox)
    manifest = export_mailbox(
        graph,
        mailbox_spec=args.mailbox,
        mailbox_upn=mailbox_upn,
        auth_mode=auth_mode,
        out_dir=out_dir,
    )
    n_done = sum(1 for fe in manifest.folders.values() if fe.status == "done")
    print(f"exported {n_done}/{len(manifest.folders)} folder(s) to {out_dir}")
    return 0


def _run_attachments(args: argparse.Namespace) -> int:
    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    out_dir = Path(args.out_dir)
    written = export_attachments(
        graph,
        mailbox_spec=args.mailbox,
        auth_mode=auth_mode,
        message_id=args.message_id,
        out_dir=out_dir,
        include_inline=args.include_inline,
    )
    print(f"wrote {len(written)} attachment(s) to {out_dir}")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "message":
        return _run_message(args)
    if args.subcommand == "folder":
        return _run_folder(args)
    if args.subcommand == "mailbox":
        return _run_mailbox(args)
    if args.subcommand == "attachments":
        return _run_attachments(args)
    return 2
