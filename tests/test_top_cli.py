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


def test_mail_domain_exists_but_has_no_verbs_yet():
    # Phase 0: mail tree is scaffold only. `mail` should print a
    # "not yet implemented" notice and exit non-zero, not ImportError.
    r = _run(["mail"])
    assert r.returncode != 0
    out = (r.stdout + r.stderr).lower()
    assert "not yet" in out or "phase 1" in out
