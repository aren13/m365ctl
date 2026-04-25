"""`m365ctl mail signature {show, set}` — local-file signature management.

Phase 9 G3.3. The signature lives at ``[mail].signature_path`` in
``config.toml``. ``show`` reads from that path; ``set`` writes via
``execute_set_signature`` (audited + undoable).

Sync-to-Outlook (Graph beta /me/userConfiguration) is out-of-scope —
manual sync remains the user's responsibility.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.config import load_config
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.cli._common import add_common_args
from m365ctl.mail.mutate._common import derive_mailbox_upn
from m365ctl.mail.mutate.settings import execute_set_signature
from m365ctl.mail.signature import get_signature


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail signature")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)

    sub.add_parser("show", help="Print the current signature.")

    set_p = sub.add_parser("set", help="Write a new signature.")
    src = set_p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-file", dest="from_file",
                     help="Read signature content from a file.")
    src.add_argument("--content", help="Inline signature content.")
    set_p.add_argument("--confirm", action="store_true")
    return p


def _signature_path(args: argparse.Namespace) -> Path | None:
    cfg = load_config(Path(args.config))
    return cfg.mail.signature_path


def _run_show(args: argparse.Namespace) -> int:
    path = _signature_path(args)
    if path is None:
        print(
            "signature_path not configured in config.toml under [mail]",
            file=sys.stderr,
        )
        return 2
    sig = get_signature(path)
    print(f"path:         {path}")
    print(f"content_type: {sig.content_type}")
    print("content:")
    print(sig.content)
    return 0


def _run_set(args: argparse.Namespace) -> int:
    path = _signature_path(args)
    if path is None:
        print(
            "signature_path not configured in config.toml under [mail]",
            file=sys.stderr,
        )
        return 2

    if args.from_file:
        try:
            content = Path(args.from_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"mail signature set: cannot read {args.from_file}: {e}",
                  file=sys.stderr)
            return 2
    else:
        content = args.content

    if not args.confirm:
        size = len(content)
        print(
            f"(dry-run) would write {size} bytes to {path}; "
            f"re-run with --confirm to apply.",
            file=sys.stderr,
        )
        return 0

    cfg = load_config(Path(args.config))
    # Capture prior content for audit/undo.
    try:
        prior = get_signature(path).content
    except Exception:
        prior = ""

    op = Operation(
        op_id=new_op_id(),
        action="mail.settings.signature",
        drive_id=derive_mailbox_upn(args.mailbox),
        item_id="",
        args={
            "mailbox_spec": args.mailbox,
            "signature_path": str(path),
            "content": content,
        },
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_set_signature(
        op,
        logger=logger,
        before={"content": prior, "signature_path": str(path)},
    )
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — signature written to {path}")
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.subcommand == "show":
        return _run_show(args)
    if args.subcommand == "set":
        return _run_set(args)
    return 2
