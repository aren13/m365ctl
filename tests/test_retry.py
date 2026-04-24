from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365ctl.common.retry import RetryExhausted, with_retry


def test_retry_returns_result_on_first_success() -> None:
    fn = MagicMock(return_value="ok")
    assert with_retry(fn, max_attempts=3, sleep=lambda s: None) == "ok"
    assert fn.call_count == 1


def test_retry_retries_on_transient_status() -> None:
    class Transient(Exception):
        def __init__(self, status: int, retry_after: float | None = None) -> None:
            self.status = status
            self.retry_after = retry_after

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise Transient(429, retry_after=0.01)
        return "eventually"

    result = with_retry(
        flaky,
        max_attempts=5,
        sleep=lambda s: None,
        is_transient=lambda e: isinstance(e, Transient),
        retry_after_of=lambda e: e.retry_after,
    )
    assert result == "eventually"
    assert attempts["n"] == 3


def test_retry_gives_up_after_max_attempts() -> None:
    class Fail(Exception):
        pass

    def always_fails():
        raise Fail("nope")

    with pytest.raises(RetryExhausted):
        with_retry(
            always_fails,
            max_attempts=3,
            sleep=lambda s: None,
            is_transient=lambda e: True,
        )


def test_retry_does_not_retry_non_transient() -> None:
    class Permanent(Exception):
        pass

    def fails():
        raise Permanent("stop")

    with pytest.raises(Permanent):
        with_retry(
            fails,
            max_attempts=5,
            sleep=lambda s: None,
            is_transient=lambda e: False,
        )
