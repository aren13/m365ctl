from __future__ import annotations

import mailbox
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.mail.export.mbox import MboxWriter, export_folder_to_mbox


_EML1 = (
    b"From: alice@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: hello\r\n"
    b"Date: Tue, 01 Apr 2026 10:00:00 +0000\r\n"
    b"\r\n"
    b"Body of message 1.\r\n"
)

_EML2 = (
    b"From: carol@example.com\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: greetings\r\n"
    b"Date: Wed, 02 Apr 2026 11:00:00 +0000\r\n"
    b"\r\n"
    b"Body of message 2.\r\n"
    b"From the past\r\n"   # Triggers the leading-From quote escape.
)


def test_mbox_writer_two_messages_round_trip(tmp_path: Path):
    out = tmp_path / "f.mbox"
    with MboxWriter(out) as w:
        w.append(_EML1, sender_addr="alice@example.com",
                 received_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc))
        w.append(_EML2, sender_addr="carol@example.com",
                 received_at=datetime(2026, 4, 2, 11, 0, tzinfo=timezone.utc))

    box = mailbox.mbox(str(out))
    msgs = list(box)
    assert len(msgs) == 2
    assert msgs[0]["From"] == "alice@example.com"
    assert msgs[1]["Subject"] == "greetings"
    box.close()


def test_mbox_writer_escapes_leading_from_in_body(tmp_path: Path):
    out = tmp_path / "f.mbox"
    with MboxWriter(out) as w:
        w.append(_EML2, sender_addr="carol@example.com",
                 received_at=datetime(2026, 4, 2, 11, 0, tzinfo=timezone.utc))
    raw = out.read_bytes()
    # The body line "From the past" must have been quoted to ">From the past".
    assert b">From the past" in raw


def test_export_folder_to_mbox_streams_all_messages(tmp_path: Path):
    """Walk the folder via list_messages, fetch EML each, write to mbox."""
    graph = MagicMock()
    # First call: folder messages (id-only listing).
    graph.get_paginated.return_value = iter([(
        [
            {"id": "m1", "from": {"emailAddress": {"address": "a@example.com"}},
             "receivedDateTime": "2026-04-01T10:00:00Z", "subject": "s1"},
            {"id": "m2", "from": {"emailAddress": {"address": "b@example.com"}},
             "receivedDateTime": "2026-04-02T11:00:00Z", "subject": "s2"},
        ],
        None,
    )])
    # Subsequent EML fetches.
    graph.get_bytes.side_effect = [_EML1, _EML2]

    out = tmp_path / "Inbox.mbox"
    count = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="fld-inbox", folder_path="Inbox", out_path=out,
    )
    assert count == 2
    box = mailbox.mbox(str(out))
    assert len(list(box)) == 2
    box.close()


def test_export_folder_to_mbox_handles_empty_folder(tmp_path: Path):
    graph = MagicMock()
    graph.get_paginated.return_value = iter([([], None)])
    out = tmp_path / "f.mbox"
    count = export_folder_to_mbox(
        graph, mailbox_spec="me", auth_mode="delegated",
        folder_id="fld", folder_path="X", out_path=out,
    )
    assert count == 0
    assert out.exists()
    assert out.stat().st_size == 0
