from __future__ import annotations

from unittest.mock import MagicMock

from m365ctl.search.graph_search import SearchHit, graph_search


def _resource(drive_id: str, item_id: str, name: str,
              modified: str = "2024-05-01T00:00:00Z",
              size: int = 100,
              is_folder: bool = False) -> dict:
    r = {
        "id": item_id,
        "name": name,
        "size": size,
        "lastModifiedDateTime": modified,
        "parentReference": {"driveId": drive_id, "path": "/drive/root:/Docs"},
    }
    if is_folder:
        r["folder"] = {"childCount": 0}
    else:
        r["file"] = {"mimeType": "text/plain"}
    return r


def test_graph_search_posts_and_normalizes_hits() -> None:
    graph = MagicMock()
    graph.post.return_value = {
        "value": [
            {
                "hitsContainers": [
                    {
                        "hits": [
                            {"hitId": "h1",
                             "resource": _resource("dA", "iA", "invoice.pdf")},
                            {"hitId": "h2",
                             "resource": _resource("dB", "iB", "Invoices",
                                                   is_folder=True)},
                        ],
                        "total": 2,
                    }
                ]
            }
        ]
    }

    hits = list(graph_search(graph, "invoice", limit=25))

    assert len(hits) == 2
    assert hits[0] == SearchHit(
        drive_id="dA",
        item_id="iA",
        name="invoice.pdf",
        full_path="/Docs/invoice.pdf",
        size=100,
        modified_at="2024-05-01T00:00:00Z",
        modified_by=None,
        is_folder=False,
        source="graph",
    )
    assert hits[1].is_folder is True

    # Verify the request body is Graph's /search/query shape.
    payload = graph.post.call_args.kwargs["json"]
    assert payload["requests"][0]["entityTypes"] == ["driveItem"]
    assert payload["requests"][0]["query"]["queryString"] == "invoice"
    assert payload["requests"][0]["size"] == 25


def test_graph_search_handles_empty_response() -> None:
    graph = MagicMock()
    graph.post.return_value = {"value": [{"hitsContainers": [{"hits": [], "total": 0}]}]}
    assert list(graph_search(graph, "nope")) == []


def test_graph_search_skips_hits_missing_drive_id() -> None:
    graph = MagicMock()
    # Resource without parentReference.driveId → unusable; skip.
    graph.post.return_value = {
        "value": [{"hitsContainers": [{
            "hits": [{"hitId": "h", "resource": {"id": "x", "name": "x"}}],
            "total": 1,
        }]}]
    }
    assert list(graph_search(graph, "x")) == []
