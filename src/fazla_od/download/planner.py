"""Download-plan schema + loaders.

Plan 3 owns the READ subset of the repo's plan-file format:

    [
      {"action": "download",
       "drive_id": "<id>",
       "item_id":  "<id>",
       "args": {"full_path": "/path/in/drive"}}
    ]

Plan 4 extends the ``action`` enum with move/rename/copy/delete/label and
their own ``args`` shapes; Plan 3 rejects anything other than ``download``
so we don't accidentally execute a mutation plan with a read-only tool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import duckdb


class PlanFileError(ValueError):
    """Raised when a plan file is malformed or contains unsupported actions."""


@dataclass(frozen=True)
class DownloadItem:
    drive_id: str
    item_id: str
    full_path: str  # path relative to drive root; used for local layout


def plan_from_single(
    *, drive_id: str, item_id: str, full_path: str = ""
) -> DownloadItem:
    return DownloadItem(drive_id=drive_id, item_id=item_id, full_path=full_path)


def plan_from_query(
    conn: duckdb.DuckDBPyConnection, sql: str
) -> list[DownloadItem]:
    """Run ``sql`` against the catalog; each row must yield drive_id, item_id,
    full_path columns (extra columns are ignored)."""
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    required = {"drive_id", "item_id", "full_path"}
    missing = required - set(cols)
    if missing:
        raise PlanFileError(
            f"query is missing required columns: {sorted(missing)}"
        )
    idx = {c: cols.index(c) for c in required}
    out: list[DownloadItem] = []
    for row in cur.fetchall():
        out.append(
            DownloadItem(
                drive_id=row[idx["drive_id"]],
                item_id=row[idx["item_id"]],
                full_path=row[idx["full_path"]] or "",
            )
        )
    return out


def write_plan_file(path: Path, items: Iterable[DownloadItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [
        {
            "action": "download",
            "drive_id": it.drive_id,
            "item_id": it.item_id,
            "args": {"full_path": it.full_path},
        }
        for it in items
    ]
    path.write_text(json.dumps(serialised, indent=2))


def load_plan_file(path: Path) -> list[DownloadItem]:
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PlanFileError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise PlanFileError(f"{path}: plan file must be a JSON list of entries")
    items: list[DownloadItem] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise PlanFileError(f"{path}[{i}]: entry must be a dict")
        action = row.get("action")
        if action != "download":
            raise PlanFileError(
                f"{path}[{i}]: unsupported action {action!r} for od-download "
                f"(expected 'download' — mutations are Plan 4)"
            )
        for key in ("drive_id", "item_id"):
            if key not in row:
                raise PlanFileError(f"{path}[{i}]: missing {key!r}")
        args = row.get("args") or {}
        items.append(
            DownloadItem(
                drive_id=row["drive_id"],
                item_id=row["item_id"],
                full_path=args.get("full_path", "") or "",
            )
        )
    return items
