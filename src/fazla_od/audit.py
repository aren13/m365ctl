"""Append-only JSONL audit log for fazla_od mutations.

Spec §7 rule 5: every mutating command writes an entry BEFORE calling
Graph (phase='start') and a paired entry AFTER (phase='end'). The 'start'
record guarantees a trail even if the process crashes mid-mutation.

File layout: ``<ops_dir>/YYYY-MM-DD.jsonl``, one JSON object per line,
UTC-dated by when the entry is written.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class AuditLogger:
    """Bundles the on-disk ops directory.

    Pass one to every mutating function. The helpers below accept it as
    first positional argument so tests can use a tmp_path-rooted logger
    without monkey-patching any global state.
    """
    ops_dir: Path


def _today_path(logger: AuditLogger) -> Path:
    logger.ops_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return logger.ops_dir / f"{day}.jsonl"


def _append(logger: AuditLogger, record: dict[str, Any]) -> None:
    path = _today_path(logger)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")))
        f.write("\n")


def log_mutation_start(
    logger: AuditLogger,
    *,
    op_id: str,
    cmd: str,
    args: dict[str, Any],
    drive_id: str,
    item_id: str,
    before: dict[str, Any] | None,
) -> None:
    """Persist the 'I am about to do X' record BEFORE the Graph call."""
    _append(
        logger,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op_id": op_id,
            "phase": "start",
            "cmd": cmd,
            "args": args,
            "drive_id": drive_id,
            "item_id": item_id,
            "before": before,
        },
    )


def log_mutation_end(
    logger: AuditLogger,
    *,
    op_id: str,
    after: dict[str, Any] | None,
    result: str,
    error: str | None = None,
) -> None:
    """Persist the 'I finished / failed' record AFTER the Graph call."""
    _append(
        logger,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "op_id": op_id,
            "phase": "end",
            "after": after,
            "result": result,
            "error": error,
        },
    )


def iter_audit_entries(logger: AuditLogger) -> Iterator[dict[str, Any]]:
    """Yield every record from every YYYY-MM-DD.jsonl under ops_dir."""
    if not logger.ops_dir.exists():
        return
    for path in sorted(logger.ops_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                yield json.loads(line)


def find_op_by_id(
    logger: AuditLogger, op_id: str
) -> tuple[dict | None, dict | None]:
    """Return (start_record, end_record) for ``op_id``, either ``None`` if absent."""
    start: dict | None = None
    end: dict | None = None
    for rec in iter_audit_entries(logger):
        if rec.get("op_id") != op_id:
            continue
        if rec.get("phase") == "start":
            start = rec
        elif rec.get("phase") == "end":
            end = rec
    return start, end
