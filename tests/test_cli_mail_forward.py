from m365ctl.mail.cli.forward import build_parser


def test_forward_parser_basic():
    args = build_parser().parse_args(["m1", "--confirm"])
    assert args.message_id == "m1"
    assert not args.inline


def test_forward_parser_inline_with_to():
    args = build_parser().parse_args([
        "m1", "--inline", "--body", "fyi", "--to", "c@example.com", "--confirm",
    ])
    assert args.inline is True
    assert args.to == ["c@example.com"]
