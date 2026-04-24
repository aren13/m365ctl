"""Adapter: Graph /search/query -> SearchHit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class SearchHit:
    drive_id: str
    item_id: str
    name: str
    full_path: str | None
    size: int | None
    modified_at: str | None  # ISO string; comparable lexicographically
    modified_by: str | None
    is_folder: bool
    source: str  # "graph" | "catalog"


class _GraphLike(Protocol):
    def post(self, path: str, *, json: dict) -> dict: ...


_PATH_PREFIX = "/drive/root:"


def _strip_prefix(p: str | None) -> str | None:
    if not p:
        return None
    if p.startswith(_PATH_PREFIX):
        p = p[len(_PATH_PREFIX):]
    return p or "/"


def _full_path(parent: str | None, name: str) -> str | None:
    if parent is None:
        return None
    if parent in ("/", ""):
        return f"/{name}" if name else "/"
    return f"{parent}/{name}" if name else parent


def graph_search(
    graph: _GraphLike, query: str, *, limit: int = 50
) -> Iterator[SearchHit]:
    body = {
        "requests": [
            {
                "entityTypes": ["driveItem"],
                "query": {"queryString": query},
                "from": 0,
                "size": min(max(limit, 1), 500),
            }
        ]
    }
    resp = graph.post("/search/query", json=body)
    for container in _iter_hit_containers(resp):
        for hit in container.get("hits") or []:
            res = hit.get("resource") or {}
            parent = res.get("parentReference") or {}
            drive_id = parent.get("driveId")
            item_id = res.get("id")
            if not drive_id or not item_id:
                continue  # Can't dedupe without a (drive_id, item_id) pair.
            parent_path = _strip_prefix(parent.get("path"))
            name = res.get("name", "")
            is_folder = "folder" in res
            yield SearchHit(
                drive_id=drive_id,
                item_id=item_id,
                name=name,
                full_path=_full_path(parent_path, name),
                size=None if is_folder else res.get("size"),
                modified_at=res.get("lastModifiedDateTime"),
                modified_by=((res.get("lastModifiedBy") or {}).get("user") or {}).get(
                    "email"
                ),
                is_folder=is_folder,
                source="graph",
            )


def _iter_hit_containers(resp: dict):
    for entry in resp.get("value") or []:
        for c in entry.get("hitsContainers") or []:
            yield c
