"""Parser tests for `m365ctl mail search`."""
from m365ctl.mail.cli.search import build_parser, main


def test_mail_search_parser_defaults():
    args = build_parser().parse_args(["invoice"])
    assert args.query == "invoice"
    assert args.limit == 25
    assert not args.local


def test_mail_search_parser_custom_limit():
    args = build_parser().parse_args(["meeting", "--limit", "100"])
    assert args.limit == 100


def test_mail_search_local_defers_to_phase_7(capsys):
    rc = main(["--config", "/nonexistent", "query", "--local"])
    assert rc == 2
    out = capsys.readouterr()
    assert "phase 7" in (out.err + out.out).lower() or "catalog" in (out.err + out.out).lower()
