import pytest
from m365ctl.mail.cli.flag import build_parser


def test_flag_parser_single_item():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--status", "flagged",
        "--due", "2026-04-30T17:00:00Z",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.status == "flagged"
    assert args.due == "2026-04-30T17:00:00Z"
    assert args.confirm is True


def test_flag_parser_rejects_invalid_status():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--status", "maybe"])


def test_flag_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
