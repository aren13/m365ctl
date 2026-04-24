import pytest
from m365ctl.mail.cli.draft import build_parser


def test_draft_create_parser():
    args = build_parser().parse_args([
        "create", "--subject", "hi", "--body", "body", "--to", "a@example.com", "--confirm",
    ])
    assert args.subcommand == "create"
    assert args.subject == "hi"
    assert args.body == "body"
    assert args.to == ["a@example.com"]


def test_draft_update_requires_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["update"])


def test_draft_update_partial():
    args = build_parser().parse_args(["update", "d1", "--subject", "new"])
    assert args.subcommand == "update"
    assert args.draft_id == "d1"
    assert args.subject == "new"
    assert args.body is None


def test_draft_delete_requires_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["delete"])
    args = build_parser().parse_args(["delete", "d1", "--confirm"])
    assert args.subcommand == "delete"
    assert args.draft_id == "d1"
