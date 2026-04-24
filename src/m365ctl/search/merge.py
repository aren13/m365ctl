"""Merge Graph + catalog hits, dedupe by (drive_id, item_id), sort desc by mtime."""
from __future__ import annotations

from typing import Iterable, Iterator

from m365ctl.search.graph_search import SearchHit


def merge_hits(
    graph_hits: Iterable[SearchHit],
    catalog_hits: Iterable[SearchHit],
    *,
    limit: int | None = None,
) -> Iterator[SearchHit]:
    seen: dict[tuple[str, str], SearchHit] = {}
    # Graph first so it wins ties (fresher metadata).
    for h in graph_hits:
        seen[(h.drive_id, h.item_id)] = h
    for h in catalog_hits:
        seen.setdefault((h.drive_id, h.item_id), h)

    def sort_key(h: SearchHit) -> tuple[int, str]:
        # NULLS LAST: tag missing timestamps with 1, real ones with 0, then
        # reverse-sort the timestamp string (ISO sorts lex correctly).
        if h.modified_at is None:
            return (1, "")
        return (0, h.modified_at)

    ordered = sorted(seen.values(), key=sort_key)
    # Reverse only among the (0, ts) group; (1, '') stays at end.
    head = [h for h in ordered if h.modified_at is not None]
    tail = [h for h in ordered if h.modified_at is None]
    head.sort(key=lambda h: h.modified_at, reverse=True)  # type: ignore[arg-type]
    combined = head + tail
    if limit is not None:
        combined = combined[:limit]
    yield from combined
