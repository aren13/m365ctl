from __future__ import annotations

from m365ctl.onedrive.search.graph_search import SearchHit
from m365ctl.onedrive.search.merge import merge_hits


def _hit(drive, item, modified, source, name="x", is_folder=False):
    return SearchHit(
        drive_id=drive,
        item_id=item,
        name=name,
        full_path=f"/{name}",
        size=0,
        modified_at=modified,
        modified_by=None,
        is_folder=is_folder,
        source=source,
    )


def test_merge_dedupes_by_drive_item_pair() -> None:
    a_graph = _hit("d", "1", "2024-05-01T00:00:00Z", "graph", name="a")
    a_catalog = _hit("d", "1", "2024-05-01T00:00:00Z", "catalog", name="a")
    b_catalog = _hit("d", "2", "2024-04-01T00:00:00Z", "catalog", name="b")

    merged = list(merge_hits([a_graph], [a_catalog, b_catalog]))
    pairs = [(h.drive_id, h.item_id) for h in merged]
    assert pairs == [("d", "1"), ("d", "2")]
    # graph source wins on tie (it's the fresh-from-Graph copy).
    assert merged[0].source == "graph"


def test_merge_sorts_by_modified_desc_nulls_last() -> None:
    h_new = _hit("d", "1", "2024-10-01T00:00:00Z", "graph")
    h_old = _hit("d", "2", "2023-01-01T00:00:00Z", "catalog")
    h_null = _hit("d", "3", None, "catalog")
    merged = list(merge_hits([h_new], [h_old, h_null]))
    assert [h.item_id for h in merged] == ["1", "2", "3"]


def test_merge_respects_limit() -> None:
    hits = [_hit("d", str(i), f"2024-{i+1:02d}-01T00:00:00Z", "catalog")
            for i in range(10)]
    merged = list(merge_hits([], hits, limit=3))
    assert len(merged) == 3
