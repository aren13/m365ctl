"""Parser tests for `m365ctl mail folders`."""
from m365ctl.mail.cli.folders import build_parser


def test_mail_folders_parser_defaults():
    args = build_parser().parse_args([])
    assert args.tree is False
    assert args.with_counts is False
    assert args.include_hidden is False


def test_mail_folders_parser_flags():
    args = build_parser().parse_args([
        "--tree", "--with-counts", "--include-hidden", "--json",
    ])
    assert args.tree is True
    assert args.with_counts is True
    assert args.include_hidden is True
    assert args.json is True
