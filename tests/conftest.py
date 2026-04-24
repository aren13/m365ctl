"""Pytest configuration shared across m365ctl tests."""
from __future__ import annotations

import os
import sys


def live_tests_enabled() -> bool:
    """Return True if live-Graph tests are enabled via env var.

    Accepts M365CTL_LIVE_TESTS (preferred) or FAZLA_OD_LIVE_TESTS
    (deprecated; emits a one-time warning).
    """
    new = os.environ.get("M365CTL_LIVE_TESTS")
    legacy = os.environ.get("FAZLA_OD_LIVE_TESTS")
    if new:
        return new == "1"
    if legacy:
        print(
            "m365ctl: FAZLA_OD_LIVE_TESTS is deprecated; set M365CTL_LIVE_TESTS=1 instead.",
            file=sys.stderr,
        )
        return legacy == "1"
    return False
