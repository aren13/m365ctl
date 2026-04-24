"""Parser tests for `m365ctl mail list`."""
from m365ctl.mail.cli.list import build_parser


def test_mail_list_parser_defaults():
    args = build_parser().parse_args([])
    assert args.folder == "Inbox"
    assert args.limit == 50
    assert args.page_size == 50
    assert not args.unread
    assert not args.read
    assert args.json is False
    assert args.mailbox == "me"


def test_mail_list_parser_filters():
    args = build_parser().parse_args([
        "--folder", "Archive/2026",
        "--from", "alice@example.com",
        "--subject", "meeting",
        "--since", "2026-04-20T00:00:00Z",
        "--until", "2026-04-24T00:00:00Z",
        "--unread",
        "--has-attachments",
        "--importance", "high",
        "--focus", "focused",
        "--category", "Followup",
        "--limit", "25",
        "--json",
    ])
    assert args.folder == "Archive/2026"
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "meeting"
    assert args.unread is True
    assert args.has_attachments is True
    assert args.importance == "high"
    assert args.focus == "focused"
    assert args.category == "Followup"
    assert args.limit == 25
    assert args.json is True


def test_mail_list_parser_rejects_invalid_importance():
    import pytest
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--importance", "critical"])


def test_mail_list_parser_rejects_invalid_focus():
    import pytest
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--focus", "random"])
