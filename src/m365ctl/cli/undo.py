"""`m365ctl undo <op-id>` — cross-domain audit-log replay.

Routes mail ops (``cmd`` starts with ``"mail-"``) to the mail handler and
everything else to the existing OneDrive undo path. The OneDrive path is
backward-compatible with legacy bare-action audit records.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from m365ctl.common.audit import AuditLogger, find_op_by_id
from m365ctl.common.config import load_config


def main(argv: list[str] | None = None) -> int:
    # Parse just enough to peek at the audit log.
    parser = argparse.ArgumentParser(prog="m365ctl undo")
    parser.add_argument("op_id")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--unsafe-scope", action="store_true")
    args = parser.parse_args(argv)

    # Peek the audit log to decide routing.
    cfg = load_config(Path(args.config))
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    start, _end = find_op_by_id(logger, args.op_id)
    cmd = (start or {}).get("cmd", "")

    if cmd.startswith("mail-"):
        from m365ctl.mail.cli.undo import run_undo_mail
        return run_undo_mail(
            config_path=Path(args.config),
            op_id=args.op_id,
            confirm=args.confirm,
        )

    # Default: OneDrive path (also handles legacy bare-action audit records
    # produced before Phase 0's `od.*` namespacing).
    from m365ctl.onedrive.cli.undo import run_undo
    return run_undo(
        config_path=Path(args.config),
        op_id=args.op_id,
        confirm=args.confirm,
        unsafe_scope=args.unsafe_scope,
    )
