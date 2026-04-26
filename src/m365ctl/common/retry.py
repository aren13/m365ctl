"""Simple retry wrapper for transient HTTP errors.

Deliberately minimal: a single ``with_retry`` callable that takes a
zero-arg function and back-off parameters. Graph throttles 429/503 are
the common case; the helper honours a ``Retry-After`` hint if the caller
supplies one, else exponential back-off.
"""
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryExhausted(RuntimeError):
    """Raised after max_attempts transient failures."""


def _default_is_transient(_: Exception) -> bool:
    return False


def _default_retry_after(_: Exception) -> float | None:
    return None


def with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    is_transient: Callable[[Exception], bool] = _default_is_transient,
    retry_after_of: Callable[[Exception], float | None] = _default_retry_after,
) -> T:
    """Invoke ``fn`` with retry on transient failures.

    Non-transient exceptions propagate immediately (no wrapping).

    Exhaustion contract (intentionally asymmetric):

    * If ``max_attempts <= 1``, no retry is attempted, and any raised
      exception propagates directly - preserving its type and any attached
      metadata (e.g. ``GraphError.retry_after_seconds``). Callers that set
      ``max_attempts=1`` typically want to observe the native error rather
      than a ``RetryExhausted`` wrapper.
    * If ``max_attempts >= 2`` and every attempt fails transiently, the
      final exception is wrapped in :class:`RetryExhausted` (chained via
      ``raise ... from last_exc``), signalling that retry machinery gave up.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if not is_transient(exc):
                raise
            if attempt == max_attempts:
                break
            hint = retry_after_of(exc)
            delay = hint if hint is not None else min(
                base_delay * (2 ** (attempt - 1)), max_delay
            )
            sleep(delay)
    # With max_attempts == 1 there was no retry attempted; re-raise the
    # original exception directly so callers see the underlying error type
    # (and any attached metadata, e.g. ``retry_after_seconds``).
    if max_attempts <= 1 and last_exc is not None:
        raise last_exc
    raise RetryExhausted(f"giving up after {max_attempts} attempts") from last_exc
