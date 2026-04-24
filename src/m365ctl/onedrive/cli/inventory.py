"""`od-inventory` subcommand: query the local catalog."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from m365ctl.onedrive.catalog.db import open_catalog
from m365ctl.common.config import load_config


def _emit(rows: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        # Coerce datetime -> ISO string for JSON output.
        def _default(o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            raise TypeError(f"unjsonable: {type(o)}")

        print(json.dumps(rows, default=_default))
        return

    # TSV - empty result prints an empty line.
    if not rows:
        print("")
        return
    cols = list(rows[0].keys())
    print("\t".join(cols))
    for r in rows:
        print("\t".join("" if r[c] is None else str(r[c]) for c in cols))


def run_inventory(
    *,
    config_path: Path,
    top_by_size: int | None,
    stale_since: str | None,
    by_owner: bool,
    duplicates: bool,
    sql: str | None,
    as_json: bool,
) -> int:
    modes = sum(
        [
            top_by_size is not None,
            stale_since is not None,
            by_owner,
            duplicates,
            sql is not None,
        ]
    )
    if modes != 1:
        print(
            "error: provide exactly one of --top-by-size, --stale-since, "
            "--by-owner, --duplicates, --sql",
            file=sys.stderr,
        )
        return 2

    cfg = load_config(config_path)
    with open_catalog(cfg.catalog.path) as conn:
        if top_by_size is not None:
            rows = _top_by_size_impl(conn, top_by_size)
        elif stale_since is not None:
            rows = _stale_since_impl(conn, stale_since)
        elif by_owner:
            rows = _by_owner_impl(conn)
        elif duplicates:
            rows = _duplicates_impl(conn)
        else:
            rows = _sql_impl(conn, sql or "")

    _emit(rows, as_json=as_json)
    return 0


# Thin indirection so tests can monkey-patch single modes if they want.
def _top_by_size_impl(conn, limit: int):
    from m365ctl.onedrive.catalog.queries import top_by_size as q
    return q(conn, limit=limit)


def _stale_since_impl(conn, cutoff: str):
    from m365ctl.onedrive.catalog.queries import stale_since as q
    return q(conn, cutoff=cutoff)


def _by_owner_impl(conn):
    from m365ctl.onedrive.catalog.queries import by_owner as q
    return q(conn)


def _duplicates_impl(conn):
    from m365ctl.onedrive.catalog.queries import duplicates as q
    return q(conn)


def _sql_impl(conn, sql: str):
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-inventory")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="Emit JSON (default: TSV).")

    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--top-by-size", type=int, metavar="N",
                      help="Top N largest files.")
    mode.add_argument("--stale-since", metavar="YYYY-MM-DD",
                      help="Files not modified since this date.")
    mode.add_argument("--by-owner", action="store_true",
                      help="Storage and file-count per modified_by.")
    mode.add_argument("--duplicates", action="store_true",
                      help="Items sharing a quick_xor_hash.")
    mode.add_argument("--sql", metavar="QUERY",
                      help="Run an arbitrary SELECT against the catalog.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_inventory(
        config_path=Path(args.config),
        top_by_size=args.top_by_size,
        stale_since=args.stale_since,
        by_owner=args.by_owner,
        duplicates=args.duplicates,
        sql=args.sql,
        as_json=args.as_json,
    )
