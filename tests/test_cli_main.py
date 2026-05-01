"""Tests for the top-level `m365ctl` dispatcher (cli/__main__.py)."""
from __future__ import annotations

import importlib.metadata as importlib_metadata



def test_help_prints_domain_banner(capsys) -> None:
    from m365ctl.cli.__main__ import main
    rc = main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "m365ctl <domain> <verb>" in out
    assert "od" in out and "mail" in out and "undo" in out


def test_no_args_prints_usage_and_exits_nonzero(capsys) -> None:
    from m365ctl.cli.__main__ import main
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert "m365ctl <domain> <verb>" in out


def test_version_flag_prints_package_version(capsys) -> None:
    from m365ctl.cli.__main__ import main
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == importlib_metadata.version("m365ctl")


def test_short_version_flag_prints_package_version(capsys) -> None:
    from m365ctl.cli.__main__ import main
    rc = main(["-V"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == importlib_metadata.version("m365ctl")


def test_unknown_domain_returns_2_and_writes_to_stderr(capsys) -> None:
    from m365ctl.cli.__main__ import main
    rc = main(["bogus"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown domain" in err
    assert "bogus" in err
