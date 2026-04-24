import pytest
from m365ctl.mail.cli.rules import build_parser


def test_mail_rules_list_parser():
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"
    assert args.disabled is False


def test_mail_rules_list_disabled_flag():
    args = build_parser().parse_args(["list", "--disabled"])
    assert args.disabled is True


def test_mail_rules_show_requires_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["show"])
    args = build_parser().parse_args(["show", "rule-id"])
    assert args.subcommand == "show"
    assert args.rule_id == "rule-id"


def test_mail_rules_parser_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
