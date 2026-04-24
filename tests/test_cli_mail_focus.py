from m365ctl.mail.cli.focus import build_parser


def test_focus_parser_focused():
    args = build_parser().parse_args(["--message-id", "m1", "--focused", "--confirm"])
    assert args.classification == "focused"


def test_focus_parser_other():
    args = build_parser().parse_args(["--message-id", "m1", "--other", "--confirm"])
    assert args.classification == "other"


def test_focus_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
