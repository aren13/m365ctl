from m365ctl.mail.cli.read import build_parser


def test_read_parser_yes():
    args = build_parser().parse_args(["--message-id", "m1", "--yes", "--confirm"])
    assert args.set_read is True


def test_read_parser_no():
    args = build_parser().parse_args(["--message-id", "m1", "--no", "--confirm"])
    assert args.set_read is False


def test_read_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
