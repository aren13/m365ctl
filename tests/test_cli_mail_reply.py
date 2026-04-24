import pytest
from m365ctl.mail.cli.reply import build_parser


def test_reply_parser_basic():
    args = build_parser().parse_args(["m1", "--confirm"])
    assert args.message_id == "m1"
    assert not args.all
    assert not args.inline


def test_reply_parser_reply_all():
    args = build_parser().parse_args(["m1", "--all", "--confirm"])
    assert args.all is True


def test_reply_parser_inline():
    args = build_parser().parse_args(["m1", "--inline", "--body", "ok", "--confirm"])
    assert args.inline is True
    assert args.body == "ok"


def test_reply_parser_requires_message_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
