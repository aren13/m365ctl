"""Safety prompt that forces a human's `y/N` decision via ``/dev/tty``.

Critical: we read from ``/dev/tty`` directly, not stdin. An agentic process
(e.g. Claude piping input to the subprocess) cannot answer this prompt —
that is the safety property.
"""
from __future__ import annotations

from typing import IO, Tuple


class TTYUnavailable(RuntimeError):
    """Raised when /dev/tty cannot be opened (e.g. no controlling terminal)."""


def _open_tty() -> Tuple[IO[str], IO[str]]:
    """Return (reader, writer) backed by /dev/tty. Separate so tests can patch."""
    try:
        reader = open("/dev/tty", "r")
        writer = open("/dev/tty", "w")
    except OSError as exc:
        raise TTYUnavailable("cannot open /dev/tty") from exc
    return reader, writer


def confirm_or_abort(message: str, *, assume_yes: bool = False) -> bool:
    """Prompt the user; return True iff they typed yes.

    ``assume_yes`` (wired from ``--yes`` on the CLI) skips the prompt entirely.
    """
    if assume_yes:
        return True
    try:
        reader, writer = _open_tty()
    except OSError as exc:
        raise TTYUnavailable("cannot open /dev/tty") from exc
    try:
        writer.write(f"{message} [y/N]: ")
        writer.flush()
        answer = (reader.readline() or "").strip().lower()
    finally:
        reader.close()
        writer.close()
    return answer in {"y", "yes"}
