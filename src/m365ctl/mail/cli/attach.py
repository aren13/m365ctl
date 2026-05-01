"""`m365ctl mail attach list|get|add|remove` — attachment ops."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.attachments import get_attachment_content, list_attachments
from m365ctl.mail.cli._common import add_common_args, emit_json_lines, load_and_authorize
from m365ctl.mail.mutate._common import assert_mail_target_allowed, derive_mailbox_upn
from m365ctl.mail.mutate.attach import (
    execute_add_attachment_large,
    execute_add_attachment_small,
    execute_remove_attachment,
    pick_upload_strategy,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="m365ctl mail attach")
    add_common_args(p)
    sub = p.add_subparsers(dest="subcommand", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("message_id")
    get_p = sub.add_parser("get")
    get_p.add_argument("message_id")
    get_p.add_argument("attachment_id")
    get_p.add_argument("--out", help="Path to write the attachment to. Default: stdout.")

    a = sub.add_parser("add", help="Add an attachment to a message.")
    a.add_argument("message_id")
    a.add_argument("--file", required=True, help="Path to the file to attach.")
    a.add_argument("--content-type", help="MIME type (default: sniff from filename).")
    a.add_argument("--confirm", action="store_true")

    rm = sub.add_parser("remove", help="Remove an attachment from a message.")
    rm.add_argument("message_id")
    rm.add_argument("attachment_id")
    rm.add_argument("--confirm", action="store_true")

    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.subcommand == "add":
        return _run_add_attachment(args)
    if args.subcommand == "remove":
        return _run_remove_attachment(args)

    _cfg, auth_mode, cred = load_and_authorize(args)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    if args.subcommand == "list":
        out = list_attachments(
            graph,
            mailbox_spec=args.mailbox,
            auth_mode=auth_mode,
            message_id=args.message_id,
        )
        if args.json:
            emit_json_lines(out)
        else:
            for a in out:
                print(
                    f"{a.kind:<9}  {a.size:>10}  {a.content_type:<40}  "
                    f"{a.name}  (id: {a.id})"
                )
        return 0

    if args.subcommand == "get":
        data = get_attachment_content(
            graph,
            mailbox_spec=args.mailbox,
            auth_mode=auth_mode,
            message_id=args.message_id,
            attachment_id=args.attachment_id,
        )
        if args.out:
            Path(args.out).write_bytes(data)
            print(f"Wrote {len(data)} bytes to {args.out}", file=sys.stderr)
        else:
            sys.stdout.buffer.write(data)
        return 0

    return 2


def _run_add_attachment(args) -> int:
    import base64
    import mimetypes
    from pathlib import Path as _Path

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )

    file_path = _Path(args.file)
    if not file_path.exists():
        print(f"mail attach add: file not found: {args.file}", file=sys.stderr)
        return 2
    size = file_path.stat().st_size
    strategy = pick_upload_strategy(size=size)

    if not args.confirm:
        print(f"(dry-run) would attach {args.file} ({size} bytes) to {args.message_id}",
              file=sys.stderr)
        return 0

    content_type = args.content_type or mimetypes.guess_type(args.file)[0] or "application/octet-stream"
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)

    if strategy == "large":
        op = Operation(
            op_id=new_op_id(), action="mail.attach.add.large",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={
                "name": file_path.name,
                "content_type": content_type,
                "size": size,
                "file_path": str(file_path.resolve()),
                "auth_mode": auth_mode,
            },
        )
        result = execute_add_attachment_large(op, graph, logger, before={})
    else:
        raw = file_path.read_bytes()
        op = Operation(
            op_id=new_op_id(), action="mail.attach.add",
            drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
            args={
                "name": file_path.name,
                "content_type": content_type,
                "content_bytes_b64": base64.b64encode(raw).decode("ascii"),
                "auth_mode": auth_mode,
            },
        )
        result = execute_add_attachment_small(op, graph, logger, before={})

    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    att_id = (result.after or {}).get("id", "")
    print(f"[{op.op_id}] ok — added attachment {att_id}")
    return 0


def _run_remove_attachment(args) -> int:
    import base64

    cfg, auth_mode, cred = load_and_authorize(args)
    assert_mail_target_allowed(
        cfg, mailbox_spec=args.mailbox, auth_mode=auth_mode,
        unsafe_scope=args.unsafe_scope,
        assume_yes=getattr(args, "assume_yes", False),
    )
    if not args.confirm:
        print(f"(dry-run) would remove attachment {args.attachment_id} from {args.message_id}",
              file=sys.stderr)
        return 0

    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)
    try:
        atts = list_attachments(graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
                                message_id=args.message_id)
        match = next((a for a in atts if a.id == args.attachment_id), None)
        if match is None:
            print(f"mail attach remove: attachment {args.attachment_id} not found on {args.message_id}",
                  file=sys.stderr)
            return 2
        content = get_attachment_content(
            graph, mailbox_spec=args.mailbox, auth_mode=auth_mode,
            message_id=args.message_id, attachment_id=args.attachment_id,
        )
        before = {
            "id": match.id,
            "name": match.name,
            "content_type": match.content_type,
            "size": match.size,
            "content_bytes_b64": base64.b64encode(content).decode("ascii"),
        }
    except Exception:
        before = {}

    op = Operation(
        op_id=new_op_id(), action="mail.attach.remove",
        drive_id=derive_mailbox_upn(args.mailbox), item_id=args.message_id,
        args={"attachment_id": args.attachment_id, "auth_mode": auth_mode},
    )
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    result = execute_remove_attachment(op, graph, logger, before=before)
    if result.status != "ok":
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    print(f"[{op.op_id}] ok — removed attachment {args.attachment_id}")
    return 0
