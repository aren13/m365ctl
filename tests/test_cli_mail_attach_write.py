import pytest
from m365ctl.mail.cli.attach import build_parser


def test_attach_add_parser():
    args = build_parser().parse_args([
        "add", "m1", "--file", "/tmp/x.pdf", "--confirm",
    ])
    assert args.subcommand == "add"
    assert args.message_id == "m1"
    assert args.file == "/tmp/x.pdf"


def test_attach_add_requires_file():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["add", "m1"])


def test_attach_remove_parser():
    args = build_parser().parse_args(["remove", "m1", "att-1", "--confirm"])
    assert args.subcommand == "remove"
    assert args.message_id == "m1"
    assert args.attachment_id == "att-1"
