from m365ctl.mail.cli.send import build_parser


def test_send_parser_draft_id():
    args = build_parser().parse_args(["d1", "--confirm"])
    assert args.draft_id == "d1"
    assert not args.new


def test_send_parser_new_mode():
    args = build_parser().parse_args([
        "--new", "--subject", "hi", "--body", "body",
        "--to", "a@example.com", "--confirm",
    ])
    assert args.new is True
    assert args.subject == "hi"
    assert args.to == ["a@example.com"]


def test_send_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"
