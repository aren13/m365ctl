"""Parser tests for `m365ctl mail search`."""
from m365ctl.mail.cli.search import build_parser


def test_mail_search_parser_defaults():
    args = build_parser().parse_args(["invoice"])
    assert args.query == "invoice"
    assert args.limit == 25
    assert not args.local


def test_mail_search_parser_custom_limit():
    args = build_parser().parse_args(["meeting", "--limit", "100"])
    assert args.limit == 100


def test_mail_search_parser_local_flag():
    args = build_parser().parse_args(["query", "--local"])
    assert args.local is True
