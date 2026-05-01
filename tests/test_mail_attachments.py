from unittest.mock import MagicMock

from m365ctl.mail.attachments import get_attachment_content, list_attachments
from m365ctl.mail.models import Attachment


def test_list_attachments():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "a1", "@odata.type": "#microsoft.graph.fileAttachment", "name": "x.pdf", "contentType": "application/pdf", "size": 100, "isInline": False},
            {"id": "a2", "@odata.type": "#microsoft.graph.itemAttachment", "name": "y.ics", "contentType": "application/octet-stream", "size": 200, "isInline": False},
        ]
    }
    out = list_attachments(graph, mailbox_spec="me", auth_mode="delegated", message_id="m1")
    assert [a.kind for a in out] == ["file", "item"]
    assert all(isinstance(a, Attachment) for a in out)
    assert graph.get.call_args.args[0] == "/me/messages/m1/attachments"


def test_list_attachments_app_only_routing():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    list_attachments(graph, mailbox_spec="upn:bob@example.com", auth_mode="app-only", message_id="m1")
    assert graph.get.call_args.args[0] == "/users/bob@example.com/messages/m1/attachments"


def test_list_attachments_for_messages_batches_in_chunks_of_20():
    """Per-message attachment listings fan out via /$batch (chunks of 20)."""
    import json as _json

    import httpx

    from m365ctl.common.graph import GraphClient
    from m365ctl.mail.attachments import list_attachments_for_messages

    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/$batch"):
            body = _json.loads(request.read())
            posts.append({"path": path, "body": body})
            responses = []
            for r in body["requests"]:
                # url like "/me/messages/<mid>/attachments"
                mid = r["url"].split("/messages/")[1].split("/")[0]
                responses.append({
                    "id": r["id"],
                    "status": 200,
                    "headers": {},
                    "body": {
                        "value": [{
                            "id": f"att-{mid}",
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": f"{mid}.pdf",
                            "contentType": "application/pdf",
                            "size": 100,
                            "isInline": False,
                        }],
                    },
                })
            return httpx.Response(200, json={"responses": responses})
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    graph = GraphClient(
        token_provider=lambda: "tok",
        transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )

    # 25 message ids => two /$batch POSTs (20 + 5).
    mids = [f"m{i}" for i in range(25)]
    out = list_attachments_for_messages(
        graph, mailbox_spec="me", auth_mode="delegated", message_ids=mids,
    )
    assert len(posts) == 2
    assert len(posts[0]["body"]["requests"]) == 20
    assert len(posts[1]["body"]["requests"]) == 5
    # Each message resolves to one attachment.
    assert sorted(out) == sorted(mids)
    assert all(len(v) == 1 for v in out.values())
    assert out["m3"][0].id == "att-m3"


def test_list_attachments_for_messages_empty_input():
    from unittest.mock import MagicMock as _MM

    from m365ctl.mail.attachments import list_attachments_for_messages

    out = list_attachments_for_messages(
        _MM(), mailbox_spec="me", auth_mode="delegated", message_ids=[],
    )
    assert out == {}


def test_get_attachment_content_returns_bytes():
    graph = MagicMock()
    graph.get_bytes.return_value = b"hello-bytes"
    data = get_attachment_content(
        graph,
        mailbox_spec="me",
        auth_mode="delegated",
        message_id="m1",
        attachment_id="a1",
    )
    assert data == b"hello-bytes"
    assert graph.get_bytes.call_args.args[0] == "/me/messages/m1/attachments/a1/$value"
