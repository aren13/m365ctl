"""Local-file signature read/write.

Phase 9 ships local-only signature management: the signature lives at
``[mail].signature_path`` in config.toml. File extension determines the
content type — ``.html`` is HTML, anything else (`.txt`, no extension)
is plain text.

Sync-to-Outlook (Graph beta endpoint ``/me/userConfiguration`` for
roaming signatures) is documented but not implemented — the API is
flagged unstable. Manual sync from this file remains the user's
responsibility for now.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class SignatureNotConfigured(ValueError):
    """Raised when [mail].signature_path is unset."""


class SignatureReadError(IOError):
    """Raised when the signature file exists but can't be read."""


@dataclass(frozen=True)
class Signature:
    content_type: str   # "text" | "html"
    content: str


def _content_type_for(path: Path) -> str:
    return "html" if path.suffix.lower() in {".html", ".htm"} else "text"


def get_signature(path: Path | None) -> Signature:
    if path is None:
        raise SignatureNotConfigured(
            "[mail].signature_path is not set in config.toml"
        )
    if not path.exists():
        return Signature(content_type=_content_type_for(path), content="")
    try:
        return Signature(
            content_type=_content_type_for(path),
            content=path.read_text(encoding="utf-8"),
        )
    except OSError as e:
        raise SignatureReadError(f"cannot read {path}: {e}") from e


def set_signature(path: Path | None, *, content: str) -> None:
    if path is None:
        raise SignatureNotConfigured(
            "[mail].signature_path is not set in config.toml"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
