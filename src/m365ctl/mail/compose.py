"""Pure helpers for mail compose flows — no Graph calls, no side effects.

Used by the mail compose executors (``mutate/draft.py``, ``mutate/send.py``,
``mutate/reply.py``, ``mutate/forward.py``) and the CLI layer. All functions
return plain dicts / lists suitable for direct feed into Graph request JSON.
"""
from __future__ import annotations

import re
from typing import Any


class BodyFormatError(ValueError):
    """Raised when compose payload inputs are malformed."""


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_EMAIL_RE = re.compile(r"^\s*(?P<name>.*?)\s*<(?P<addr>[^>]+)>\s*$")


def parse_recipients(addrs: list[str]) -> list[dict[str, Any]]:
    """Turn a list of ``["alice@ex.com", "Bob <bob@ex.com>"]`` into Graph shape.

    Returns ``[{"emailAddress": {"address": ..., "name"?: ...}}, ...]``.
    Whitespace stripped. Raises ``ValueError`` on anything that isn't a
    recognizable email address.
    """
    out: list[dict[str, Any]] = []
    for raw in addrs:
        if not raw or not raw.strip():
            continue
        s = raw.strip()
        m = _NAME_EMAIL_RE.match(s)
        if m:
            name = m.group("name")
            addr = m.group("addr").strip()
            if not _EMAIL_RE.match(addr):
                raise ValueError(f"invalid email address: {addr!r}")
            entry: dict[str, Any] = {"emailAddress": {"address": addr}}
            if name:
                entry["emailAddress"]["name"] = name
            out.append(entry)
            continue
        if _EMAIL_RE.match(s):
            out.append({"emailAddress": {"address": s}})
            continue
        raise ValueError(f"cannot parse recipient {raw!r}; expected 'addr' or 'Name <addr>'")
    return out


def build_message_payload(
    *,
    subject: str,
    body: str,
    to: list[str],
    body_type: str = "text",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    importance: str | None = None,
    require_subject: bool = False,
) -> dict[str, Any]:
    """Assemble a Graph ``message`` JSON body.

    Only includes ``cc``/``bcc``/``importance`` keys when non-None / non-empty.

    If ``require_subject`` is True and ``subject`` is empty, raise
    ``BodyFormatError`` — callers use this for ``send --new`` which refuses
    to send a blank-subject message.
    """
    if require_subject and not subject:
        raise BodyFormatError("subject cannot be empty")
    if body_type not in ("text", "html"):
        raise BodyFormatError(f"body_type must be 'text' or 'html'; got {body_type!r}")
    payload: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": body_type, "content": body},
        "toRecipients": parse_recipients(to),
    }
    if cc:
        payload["ccRecipients"] = parse_recipients(cc)
    if bcc:
        payload["bccRecipients"] = parse_recipients(bcc)
    if importance:
        payload["importance"] = importance
    return payload


def count_external_recipients(
    recipients: list[dict[str, Any]],
    *,
    internal_domain: str | None,
) -> int:
    """Return the count of recipients whose address domain is NOT ``internal_domain``.

    Case-insensitive match on domain. If ``internal_domain`` is None, all
    recipients count as external (the cautious default).
    """
    if internal_domain is None:
        return len(recipients)
    needle = "@" + internal_domain.lower()
    count = 0
    for r in recipients:
        addr = (r.get("emailAddress", {}).get("address") or "").lower()
        if not addr.endswith(needle):
            count += 1
    return count
