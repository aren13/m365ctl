import pytest
from m365ctl.mail.cli.settings import build_parser


def test_mail_settings_show_parser():
    args = build_parser().parse_args(["show"])
    assert args.subcommand == "show"


def test_mail_settings_ooo_parser():
    args = build_parser().parse_args(["ooo"])
    assert args.subcommand == "ooo"


def test_mail_settings_requires_subcommand():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
