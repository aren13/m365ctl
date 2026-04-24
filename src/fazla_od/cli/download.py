"""`od-download` subcommand: materialise a subset of OneDrive locally."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
from datetime import datetime
from pathlib import Path

from fazla_od.auth import AppOnlyCredential, DelegatedCredential
from fazla_od.catalog.db import open_catalog
from fazla_od.config import load_config
from fazla_od.download.fetcher import fetch_item
from fazla_od.download.planner import (
    DownloadItem,
    load_plan_file,
    plan_from_query,
    plan_from_single,
    write_plan_file,
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _sources_provided(item_id, drive_id, from_plan, query) -> int:
    count = 0
    if item_id is not None and drive_id is not None:
        count += 1
    if from_plan is not None:
        count += 1
    if query is not None:
        count += 1
    return count


def _dest_for(item: DownloadItem, root: Path) -> Path:
    rel = (item.full_path or item.item_id).lstrip("/")
    if not rel:
        rel = item.item_id
    return root / rel


def run_download(
    *,
    config_path: Path,
    item_id: str | None,
    drive_id: str | None,
    from_plan: Path | None,
    query: str | None,
    dest: Path | None,
    overwrite: bool,
    concurrency: int,
    plan_out: Path | None,
    scope: str,
) -> int:
    if _sources_provided(item_id, drive_id, from_plan, query) != 1:
        print(
            "error: provide exactly one of (--item-id + --drive-id), "
            "--from-plan, or --query",
            file=sys.stderr,
        )
        return 2

    cfg = load_config(config_path)
    if scope == "me":
        cred = DelegatedCredential(cfg)
    else:
        cred = AppOnlyCredential(cfg)

    if query is not None:
        with open_catalog(cfg.catalog.path) as conn:
            items = plan_from_query(conn, query)
    elif from_plan is not None:
        items = load_plan_file(from_plan)
    else:
        items = [plan_from_single(drive_id=drive_id, item_id=item_id,
                                  full_path=item_id)]  # single: use item_id as local name

    if not items:
        print("No items matched — nothing to do.")
        return 0

    if plan_out is not None:
        write_plan_file(plan_out, items)
        print(f"Wrote {len(items)} entries to {plan_out}")
        return 0

    dest_root = dest if dest is not None else (
        Path("workspaces") / f"download-{_timestamp()}"
    )
    dest_root.mkdir(parents=True, exist_ok=True)

    token = cred.get_token()

    successes = 0
    skipped = 0
    failures: list[tuple[DownloadItem, str]] = []

    def _one(item: DownloadItem):
        return fetch_item(
            drive_id=item.drive_id,
            item_id=item.item_id,
            dest=_dest_for(item, dest_root),
            token_provider=lambda: token,
            overwrite=overwrite,
        )

    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(_one, it): it for it in items}
        for fut in cf.as_completed(futures):
            it = futures[fut]
            try:
                res = fut.result()
                if res.skipped:
                    skipped += 1
                    print(f"  skip  {it.full_path or it.item_id}")
                else:
                    successes += 1
                    print(f"  ok    {it.full_path or it.item_id} "
                          f"({res.bytes_written:,} bytes)")
            except Exception as exc:
                failures.append((it, str(exc)))
                print(f"  FAIL  {it.full_path or it.item_id}: {exc}",
                      file=sys.stderr)

    print(f"Done. {successes} downloaded, {skipped} skipped, "
          f"{len(failures)} failed. Dest: {dest_root}")
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-download")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope", default="me",
                   help="me (delegated) or anything else (app-only). "
                        "Controls auth only; actual items come from --item-id / "
                        "--from-plan / --query.")
    p.add_argument("--item-id")
    p.add_argument("--drive-id")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--query", help="SELECT ... returning drive_id,item_id,full_path")
    p.add_argument("--dest", type=Path,
                   help="Destination dir (default: workspaces/download-<ts>/).")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--plan-out", type=Path,
                   help="Write the resolved items as a plan file and exit "
                        "without downloading.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_download(
        config_path=Path(args.config),
        item_id=args.item_id,
        drive_id=args.drive_id,
        from_plan=args.from_plan,
        query=args.query,
        dest=args.dest,
        overwrite=args.overwrite,
        concurrency=args.concurrency,
        plan_out=args.plan_out,
        scope=args.scope,
    )
