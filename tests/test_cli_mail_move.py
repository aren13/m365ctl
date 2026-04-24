
from m365ctl.mail.cli.move import build_parser


def test_move_parser_single_mode():
    args = build_parser().parse_args([
        "--message-id", "m1",
        "--to-folder", "/Archive",
        "--confirm",
    ])
    assert args.message_id == "m1"
    assert args.to_folder == "/Archive"
    assert args.confirm is True


def test_move_parser_bulk_plan_out():
    args = build_parser().parse_args([
        "--from", "alice@example.com",
        "--subject", "old",
        "--folder", "/Inbox",
        "--to-folder", "/Archive/Old",
        "--plan-out", "/tmp/p.json",
    ])
    assert args.from_address == "alice@example.com"
    assert args.subject_contains == "old"
    assert args.folder == "/Inbox"
    assert args.to_folder == "/Archive/Old"
    assert args.plan_out == "/tmp/p.json"
    assert args.confirm is False


def test_move_parser_from_plan_requires_confirm():
    args = build_parser().parse_args([
        "--from-plan", "/tmp/p.json",
        "--confirm",
    ])
    assert args.from_plan == "/tmp/p.json"
    assert args.confirm is True


def test_move_parser_no_args_still_valid():
    args = build_parser().parse_args([])
    assert args.message_id is None
    assert args.from_plan is None
