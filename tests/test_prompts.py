from __future__ import annotations

import io

import pytest

from m365ctl.prompts import confirm_or_abort, TTYUnavailable


def test_confirm_returns_true_on_y(monkeypatch) -> None:
    fake_tty = io.StringIO("y\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("m365ctl.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is True


def test_confirm_returns_true_on_yes_case_insensitive(monkeypatch) -> None:
    fake_tty = io.StringIO("YES\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("m365ctl.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is True


def test_confirm_returns_false_on_n(monkeypatch) -> None:
    fake_tty = io.StringIO("n\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("m365ctl.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is False


def test_confirm_returns_false_on_blank(monkeypatch) -> None:
    # Default is N.
    fake_tty = io.StringIO("\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("m365ctl.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is False


def test_yes_flag_shortcuts_prompt(monkeypatch) -> None:
    called = {"n": 0}

    def should_not_open():
        called["n"] += 1
        raise AssertionError("should not open tty")

    monkeypatch.setattr("m365ctl.prompts._open_tty", should_not_open)
    assert confirm_or_abort("Proceed?", assume_yes=True) is True
    assert called["n"] == 0


def test_raises_when_tty_unavailable(monkeypatch) -> None:
    def no_tty():
        raise OSError("no tty")

    monkeypatch.setattr("m365ctl.prompts._open_tty", no_tty)
    with pytest.raises(TTYUnavailable):
        confirm_or_abort("Proceed?")
