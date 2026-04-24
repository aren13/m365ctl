"""Smoke tests for the top-level m365ctl CLI dispatcher."""
from __future__ import annotations

import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "m365ctl", *args],
        capture_output=True, text=True, check=False,
    )


def test_top_level_help_lists_both_domains():
    r = _run(["--help"])
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "od" in out
    # `mail` is listed even though Phase 0 ships no mail verbs.
    assert "mail" in out
    assert "undo" in out


def test_od_search_passthrough_reaches_onedrive_cli():
    # --help on the od sub-dispatcher should not error.
    r = _run(["od", "--help"])
    assert r.returncode == 0
    assert "search" in (r.stdout + r.stderr)


def test_unknown_domain_exits_nonzero():
    r = _run(["teams", "foo"])
    assert r.returncode != 0


def test_mail_domain_no_verb_prints_usage():
    # With no verb: mail dispatcher prints its own usage and exits non-zero.
    r = _run(["mail"])
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "verb" in out or "usage" in out


def test_mail_domain_routes_to_mail_cli():
    r = _run(["mail", "--help"])
    assert r.returncode == 0
    out = r.stdout + r.stderr
    assert "list" in out
    assert "get" in out
    assert "search" in out


def test_mail_list_help_reachable():
    r = _run(["mail", "list", "--help"])
    # Stub still returns 2 pre-Task-13; --help flag is the argparse path and should exit 0
    # once the real verb lands. For Group 6, the stub replies non-zero with a
    # "not yet implemented" notice. Accept either here until Group 7 lands.
    assert r.returncode in (0, 2)
