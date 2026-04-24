"""Parser tests for `m365ctl mail get`."""
import pytest

from m365ctl.mail.cli.get import build_parser, main


def test_mail_get_parser_requires_message_id():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_mail_get_parser_accepts_flags():
    args = build_parser().parse_args([
        "AAMkAD.mm=",
        "--with-body",
        "--with-attachments",
        "--json",
    ])
    assert args.message_id == "AAMkAD.mm="
    assert args.with_body
    assert args.with_attachments
    assert args.json


def test_mail_get_eml_flag_returns_deferred_exit(capsys):
    rc = main(["--config", "/nonexistent", "abc", "--eml"])
    assert rc == 2
    captured = capsys.readouterr()
    # The deferral notice goes to stderr.
    assert "deferred" in captured.err.lower() or "phase 11" in captured.err.lower()
