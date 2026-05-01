"""Resolve a `--mailbox` spec to the Graph URL prefix.

Mailbox specs follow the forms documented in spec §11.1:

- ``me``                         — signed-in user (delegated only)
- ``upn:user@example.com``       — specific mailbox (app-only, or delegated with delegation)
- ``shared:team@example.com``    — shared mailbox (either auth mode)
- ``*``                          — wildcard (app-only only; never resolvable by this helper)

``user_base("me", auth_mode="delegated")``         → ``/me``
``user_base("upn:alice@x", auth_mode="app-only")`` → ``/users/alice@x``
``user_base("shared:team@x", auth_mode=…)``        → ``/users/team@x``
"""
from __future__ import annotations

from typing import Literal

AuthMode = Literal["delegated", "app-only"]


class InvalidMailboxSpec(ValueError):
    """Raised when a mailbox spec can't be resolved to a Graph URL prefix."""


def parse_mailbox_spec(spec: str) -> tuple[str, str | None]:
    """Split a mailbox spec into ``(kind, address)``.

    Returns:
        ("me", None), ("*", None), ("upn", "<addr>"), or ("shared", "<addr>").

    Raises:
        InvalidMailboxSpec: on malformed input.
    """
    if spec == "me":
        return ("me", None)
    if spec == "*":
        return ("*", None)
    if spec.startswith("upn:"):
        addr = spec[len("upn:"):].strip()
        if not addr or "@" not in addr:
            raise InvalidMailboxSpec(f"upn: spec requires an email address, got {spec!r}")
        return ("upn", addr)
    if spec.startswith("shared:"):
        addr = spec[len("shared:"):].strip()
        if not addr or "@" not in addr:
            raise InvalidMailboxSpec(f"shared: spec requires an email address, got {spec!r}")
        return ("shared", addr)
    raise InvalidMailboxSpec(
        f"unrecognized mailbox spec {spec!r}; expected one of 'me', 'upn:<addr>', 'shared:<addr>', '*'"
    )


def user_base(spec: str, *, auth_mode: AuthMode) -> str:
    """Return the Graph URL prefix (``/me`` or ``/users/{upn}``) for a mailbox spec.

    Raises InvalidMailboxSpec for ``*`` (caller must enumerate) or for ``me`` under app-only.
    """
    kind, addr = parse_mailbox_spec(spec)
    if kind == "*":
        raise InvalidMailboxSpec("wildcard '*' cannot be resolved to a single URL prefix")
    if kind == "me":
        if auth_mode == "app-only":
            raise InvalidMailboxSpec("'me' is not valid under app-only auth; pass 'upn:<addr>' instead")
        return "/me"
    assert addr is not None
    return f"/users/{addr}"


def user_base_for_op(op) -> str:
    """Resolve the Graph URL prefix from an ``Operation`` carrying drive_id + auth_mode.

    Used by both ``mail.mutate`` verbs (where ``op`` is the unit of work) and CLI
    ``--from-plan`` ``fetch_before`` callbacks. Reads ``op.args.get("auth_mode",
    "delegated")``; CLI callers should ``op.args.setdefault("auth_mode", auth_mode)``
    before invoking this.
    """
    auth_mode = op.args.get("auth_mode", "delegated")
    spec = "me" if op.drive_id == "me" else f"upn:{op.drive_id}"
    return user_base(spec, auth_mode=auth_mode)
