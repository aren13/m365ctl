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
