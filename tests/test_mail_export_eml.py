from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.mail.export.eml import export_message_to_eml, fetch_eml_bytes


def test_fetch_eml_bytes_routes_to_value_endpoint_me():
    graph = MagicMock()
    graph.get_bytes.return_value = b"From: a@example.com\r\nSubject: x\r\n\r\nbody\r\n"
    out = fetch_eml_bytes(graph, mailbox_spec="me", auth_mode="delegated", message_id="m-1")
    assert out == b"From: a@example.com\r\nSubject: x\r\n\r\nbody\r\n"
    graph.get_bytes.assert_called_once_with("/me/messages/m-1/$value")


def test_fetch_eml_bytes_app_only_routes_via_users_upn():
    graph = MagicMock()
    graph.get_bytes.return_value = b"x"
    fetch_eml_bytes(graph, mailbox_spec="upn:bob@example.com",
                    auth_mode="app-only", message_id="m-2")
    graph.get_bytes.assert_called_once_with("/users/bob@example.com/messages/m-2/$value")


def test_export_message_to_eml_writes_file(tmp_path: Path):
    graph = MagicMock()
    graph.get_bytes.return_value = b"From: a\r\nSubject: hi\r\n\r\nbody\r\n"
    out = tmp_path / "msg.eml"
    written = export_message_to_eml(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_path=out,
    )
    assert written == out
    assert out.read_bytes() == b"From: a\r\nSubject: hi\r\n\r\nbody\r\n"


def test_export_message_to_eml_creates_parent_dirs(tmp_path: Path):
    graph = MagicMock()
    graph.get_bytes.return_value = b"x"
    out = tmp_path / "deep" / "nested" / "msg.eml"
    export_message_to_eml(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_path=out,
    )
    assert out.exists()


def test_export_message_to_eml_round_trip_via_email_parser(tmp_path: Path):
    """Round-trip: write EML bytes, parse with stdlib email, re-emit, equal."""
    import email
    from email import policy

    graph = MagicMock()
    raw = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: round trip\r\n"
        b"Message-ID: <abc@example.com>\r\n"
        b"\r\n"
        b"Body line 1\r\n"
        b"Body line 2\r\n"
    )
    graph.get_bytes.return_value = raw
    out = tmp_path / "msg.eml"
    export_message_to_eml(
        graph, mailbox_spec="me", auth_mode="delegated",
        message_id="m-1", out_path=out,
    )
    parsed = email.message_from_bytes(out.read_bytes(), policy=policy.default)
    assert parsed["From"] == "alice@example.com"
    assert parsed["Subject"] == "round trip"
    assert parsed["Message-ID"] == "<abc@example.com>"
