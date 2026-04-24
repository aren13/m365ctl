import pytest
from m365ctl.mail.cli.attach import build_parser


def test_attach_list_requires_message_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["list"])
    args = build_parser().parse_args(["list", "msg-id"])
    assert args.subcommand == "list"
    assert args.message_id == "msg-id"


def test_attach_get_requires_both_ids():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["get", "msg-id"])
    args = build_parser().parse_args(["get", "msg-id", "att-id", "--out", "/tmp/x"])
    assert args.subcommand == "get"
    assert args.message_id == "msg-id"
    assert args.attachment_id == "att-id"
    assert args.out == "/tmp/x"


def test_attach_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
