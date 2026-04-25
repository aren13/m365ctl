from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.mail.export.attachments import export_attachments


def _file_attachment(att_id: str, name: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    return {
        "id": att_id,
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        "size": len(content),
        "isInline": False,
        "contentBytes": base64.b64encode(content).decode("ascii"),
    }


def test_exports_one_attachment(tmp_path: Path):
    graph = MagicMock()
    graph.get.return_value = {"value": [_file_attachment("a-1", "doc.pdf", b"PDFBYTES")]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert len(written) == 1
    assert written[0].name == "doc.pdf"
    assert (tmp_path / "doc.pdf").read_bytes() == b"PDFBYTES"


def test_exports_multiple_with_collision_suffixes(tmp_path: Path):
    graph = MagicMock()
    graph.get.return_value = {"value": [
        _file_attachment("a-1", "doc.pdf", b"AAA"),
        _file_attachment("a-2", "doc.pdf", b"BBB"),  # same name
        _file_attachment("a-3", "doc.pdf", b"CCC"),  # same name
    ]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    names = sorted(p.name for p in written)
    assert names == ["doc (1).pdf", "doc (2).pdf", "doc.pdf"]


def test_skips_inline_by_default(tmp_path: Path):
    graph = MagicMock()
    inline_att = _file_attachment("a-1", "logo.png", b"PNG")
    inline_att["isInline"] = True
    file_att = _file_attachment("a-2", "doc.pdf", b"PDF")
    graph.get.return_value = {"value": [inline_att, file_att]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert [p.name for p in written] == ["doc.pdf"]


def test_includes_inline_when_flagged(tmp_path: Path):
    graph = MagicMock()
    inline = _file_attachment("a-1", "logo.png", b"PNG")
    inline["isInline"] = True
    graph.get.return_value = {"value": [inline]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
        include_inline=True,
    )
    assert [p.name for p in written] == ["logo.png"]


def test_skips_non_file_attachments(tmp_path: Path):
    graph = MagicMock()
    item_att = {
        "id": "a-1",
        "@odata.type": "#microsoft.graph.itemAttachment",
        "name": "calendar.ics",
        "contentType": "application/octet-stream",
        "size": 0,
        "isInline": False,
    }
    graph.get.return_value = {"value": [item_att, _file_attachment("a-2", "doc.pdf", b"X")]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert [p.name for p in written] == ["doc.pdf"]


def test_sanitises_path_separators_in_name(tmp_path: Path):
    graph = MagicMock()
    att = _file_attachment("a-1", "evil/../../../etc/passwd", b"X")
    graph.get.return_value = {"value": [att]}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    # Name reduced to a safe basename inside out_dir.
    assert len(written) == 1
    assert written[0].parent == tmp_path
    assert "/" not in written[0].name
    assert ".." not in written[0].name


def test_returns_empty_list_when_no_attachments(tmp_path: Path):
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    written = export_attachments(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_dir=tmp_path,
    )
    assert written == []
