import pytest

from m365ctl.mail.cli.delete import build_parser


def test_delete_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.confirm is True


def test_delete_parser_bulk_plan_out():
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "spam",
        "--folder", "/Inbox",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "spam"
    assert args.folder == "/Inbox"
    assert args.plan_out == "/tmp/p.json"
    assert args.confirm is False


def test_delete_parser_from_plan():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_delete_parser_no_args_still_valid():
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None
    assert args.plan_out is None


def test_delete_help_mentions_hard_delete_distinction():
    """Per spec: --help explicitly distinguishes from mail-clean (hard delete, Phase 6)."""
    parser = build_parser()
    help_text = parser.format_help()
    assert "clean" in help_text.lower() or "hard" in help_text.lower()
