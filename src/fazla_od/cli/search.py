"""`od-search` subcommand: Graph + catalog fused search."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from fazla_od.auth import AppOnlyCredential, DelegatedCredential
from fazla_od.catalog.crawl import resolve_scope
from fazla_od.catalog.db import open_catalog
from fazla_od.config import load_config
from fazla_od.graph import GraphClient
from fazla_od.search.catalog_search import catalog_search
from fazla_od.search.graph_search import SearchHit, graph_search
from fazla_od.search.merge import merge_hits


def _drive_ids_for_scope(scope: str, graph) -> list[str] | None:
    """Return drive_ids to filter results to, or None = no filter."""
    if scope == "me":
        return None  # delegated auth already limits Graph results to user
    if scope == "tenant":
        return None  # no filter
    # drive:<id>, site:<id>
    specs = resolve_scope(scope, graph)
    return [s.drive_id for s in specs]


def run_search(
    *,
    config_path: Path,
    query: str,
    scope: str,
    type_: str,
    modified_since: str | None,
    owner: str | None,
    limit: int,
    as_json: bool,
) -> int:
    cfg = load_config(config_path)

    # Auth: 'me' -> delegated; everything else -> app-only.
    if scope == "me":
        cred = DelegatedCredential(cfg)
    else:
        cred = AppOnlyCredential(cfg)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    drive_filter = _drive_ids_for_scope(scope, graph)

    graph_results: Iterable[SearchHit] = graph_search(graph, query, limit=limit)
    if drive_filter is not None:
        graph_results = (h for h in graph_results if h.drive_id in drive_filter)
    if type_ == "file":
        graph_results = (h for h in graph_results if not h.is_folder)
    elif type_ == "folder":
        graph_results = (h for h in graph_results if h.is_folder)
    if modified_since:
        graph_results = (
            h for h in graph_results
            if (h.modified_at or "") >= modified_since
        )
    if owner:
        graph_results = (h for h in graph_results if h.modified_by == owner)

    with open_catalog(cfg.catalog.path) as conn:
        catalog_results = list(
            catalog_search(
                conn,
                query,
                type_=type_,  # type: ignore[arg-type]
                modified_since=modified_since,
                owner=owner,
                drive_ids=drive_filter,
            )
        )
        merged = list(
            merge_hits(list(graph_results), catalog_results, limit=limit)
        )

    _emit(merged, as_json=as_json)
    return 0


def _emit(hits: list[SearchHit], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([h.__dict__ for h in hits]))
        return
    cols = ["drive_id", "item_id", "name", "full_path", "size",
            "modified_at", "modified_by", "is_folder", "source"]
    print("\t".join(cols))
    for h in hits:
        row = [
            h.drive_id, h.item_id, h.name, h.full_path or "",
            "" if h.size is None else str(h.size),
            h.modified_at or "", h.modified_by or "",
            str(h.is_folder), h.source,
        ]
        print("\t".join(row))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-search")
    p.add_argument("--config", default="config.toml")
    p.add_argument("query", help="Free-text query (matched name + full_path).")
    p.add_argument(
        "--scope",
        default="me",
        help="me | drive:<id> | site:<slug-or-id> | tenant (default: me)",
    )
    p.add_argument(
        "--type",
        dest="type_",
        default="file",
        choices=["file", "folder", "all"],
    )
    p.add_argument("--modified-since", metavar="YYYY-MM-DD")
    p.add_argument("--owner", metavar="EMAIL")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", dest="as_json", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_search(
        config_path=Path(args.config),
        query=args.query,
        scope=args.scope,
        type_=args.type_,
        modified_since=args.modified_since,
        owner=args.owner,
        limit=args.limit,
        as_json=args.as_json,
    )
