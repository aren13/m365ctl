"""Scope allow/deny enforcement + /dev/tty escape hatch.

Spec §7 rules 3 and 4 live here. Every mutating command calls
``assert_scope_allowed(item, cfg, unsafe_scope=...)`` for each target
before touching Graph. Bulk selections call ``filter_by_scope`` to drop
deny-path items *before* plan emission — deny-path items never appear in
``--plan-out`` output or in dry-run TSV.

The ``--unsafe-scope`` escape hatch is the only way to mutate an item
whose drive_id is not in ``allow_drives``. It additionally requires a
``y/N`` confirmation read from ``/dev/tty`` (not stdin). This closes the
loophole where Claude (or any agent) pipes 'y\\n' into the command's
stdin: ``/dev/tty`` bypasses the redirected stdin and talks to the
controlling terminal directly. If no TTY is attached the confirm returns
False and the op is rejected.
"""
from __future__ import annotations

import fnmatch
from typing import Iterable, Iterator, Protocol

from m365ctl.common.config import Config


class _HasScopeFields(Protocol):
    # Properties (not class-level annotations) so frozen dataclasses with
    # the same field names can structurally match this Protocol — mypy
    # treats class-level `field: T` as writable, which conflicts with
    # frozen=True attrs.
    @property
    def drive_id(self) -> str: ...
    @property
    def item_id(self) -> str: ...
    @property
    def full_path(self) -> str: ...
    @property
    def name(self) -> str: ...
    @property
    def parent_path(self) -> str: ...


class ScopeViolation(RuntimeError):
    """Raised when an item is outside allow_drives or matches deny_paths.

    Kill chain: caught by the CLI's top-level handler, which exits with
    code 2 and prints ``str(err)``. Not caught anywhere deeper.
    """


def _confirm_via_tty(prompt: str) -> bool:
    """Read y/N from /dev/tty directly. Returns False if no TTY.

    Opens /dev/tty read-write and prompts there; an agent piping into
    the command's stdin cannot intercept this. Returning ``False`` is the
    only rejection signal — callers do not distinguish between "no TTY"
    and "user said no"; both are treated as "don't proceed".
    """
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(prompt)
            tty.flush()
            answer = tty.readline().strip().lower()
            return answer in {"y", "yes"}
    except OSError:
        return False


def _drive_allowed(item: _HasScopeFields, cfg: Config) -> bool:
    # ``allow_drives`` is a plain string set; the friendly synonyms ("me",
    # "site:<slug>") are collapsed to drive ids upstream of this check, so
    # equality on the raw string is sufficient here.
    return item.drive_id in cfg.scope.allow_drives


def _deny_match(item: _HasScopeFields, cfg: Config) -> str | None:
    """Return the first matching deny pattern, or None.

    Uses fnmatch glob semantics. Patterns ending in ``/**`` additionally
    match the bare parent directory itself — so ``/HR/**`` blocks both
    ``/HR`` and ``/HR/anything/deeper`` under a single rule. This closes
    the fnmatch gap where ``fnmatch("/HR", "/HR/**")`` returns False.
    """
    for pattern in cfg.scope.deny_paths:
        if fnmatch.fnmatch(item.full_path, pattern):
            return pattern
        # A pattern like "/HR/**" should also block the directory "/HR" itself.
        if pattern.endswith("/**"):
            prefix = pattern[:-3]  # strip trailing "/**"
            if item.full_path == prefix:
                return pattern
    return None


def assert_scope_allowed(
    item: _HasScopeFields,
    cfg: Config,
    *,
    unsafe_scope: bool,
) -> None:
    """Raise ``ScopeViolation`` unless the item is allowed.

    - Deny paths ALWAYS block, even with ``--unsafe-scope``.
    - Drive-not-in-allow-list blocks unless ``unsafe_scope=True`` AND the
      /dev/tty prompt confirms.
    """
    denied = _deny_match(item, cfg)
    if denied is not None:
        raise ScopeViolation(
            f"deny-path match: {item.full_path!r} matches {denied!r} "
            f"(deny_paths are absolute — no override)"
        )

    if _drive_allowed(item, cfg):
        return

    # Note: `cfg.scope.unsafe_requires_flag` is intentionally NOT consulted here.
    # The CLI layer decides whether `--unsafe-scope` may be passed based on that
    # config field. By the time we get here, `unsafe_scope` is the authoritative
    # caller intent.
    if not unsafe_scope:
        raise ScopeViolation(
            f"drive {item.drive_id!r} not in scope.allow_drives; "
            f"pass --unsafe-scope to override (requires TTY confirm)"
        )

    prompt = (
        f"UNSAFE SCOPE: drive {item.drive_id!r} is outside allow_drives.\n"
        f"  item full_path: {item.full_path!r}\n"
        f"Proceed anyway? [y/N]: "
    )
    if not _confirm_via_tty(prompt):
        raise ScopeViolation(
            f"user declined /dev/tty confirm for unsafe-scope item "
            f"drive={item.drive_id!r} path={item.full_path!r}"
        )


def filter_by_scope(
    items: Iterable[_HasScopeFields],
    cfg: Config,
    *,
    unsafe_scope: bool,
) -> Iterator[_HasScopeFields]:
    """Drop items that would raise ``ScopeViolation``.

    Used during bulk selection (before plan emission) so deny-path items
    never appear in the plan. An allow-list miss with ``unsafe_scope=True``
    still prompts once per item; pass the whole selection through
    ``assert_scope_allowed`` at execute time anyway — this filter is a
    fast pre-pass, not the authoritative gate.
    """
    for item in items:
        if _deny_match(item, cfg) is not None:
            continue
        if _drive_allowed(item, cfg):
            yield item
            continue
        if not unsafe_scope:
            continue
        # Prompt once per item. Order matches the source iterable.
        try:
            assert_scope_allowed(item, cfg, unsafe_scope=True)
        except ScopeViolation:
            continue
        yield item


# ---- Mailbox + folder gates (Phase 1) --------------------------------------

# Hard-coded folder patterns that are ALWAYS denied (spec §11.2). These are
# non-negotiable compliance/out-of-scope buckets; user config cannot override.
HARDCODED_DENY_FOLDERS: frozenset[str] = frozenset({
    "Recoverable Items",
    "Recoverable Items/*",
    "Purges",
    "Purges/*",
    "Audits",
    "Audits/*",
    "Calendar",
    "Calendar/*",
    "Contacts",
    "Contacts/*",
    "Tasks",
    "Tasks/*",
    "Notes",
    "Notes/*",
})


def _mailbox_spec_matches(actual_spec: str, allowed_entry: str) -> bool:
    """Return True iff ``actual_spec`` satisfies ``allowed_entry``.

    Entries are compared case-insensitively on the address portion (email
    addresses are case-insensitive per RFC 5321). Exact (kind, address)
    tuple match.
    """
    def _split(s: str) -> tuple[str, str]:
        for prefix, kind in (("upn:", "upn"), ("shared:", "shared")):
            if s.startswith(prefix):
                return (kind, s[len(prefix):].strip().lower())
        # Bare keywords: "me", "*".
        return (s.strip().lower(), "")

    a_kind, a_addr = _split(actual_spec)
    e_kind, e_addr = _split(allowed_entry)
    return a_kind == e_kind and a_addr == e_addr


def assert_mailbox_allowed(
    mailbox_spec: str,
    cfg: Config,
    *,
    auth_mode: str,
    unsafe_scope: bool,
) -> None:
    """Raise ``ScopeViolation`` unless ``mailbox_spec`` is in ``allow_mailboxes``.

    Matching semantics (spec §11.1):
    - ``"me"`` matches ``"me"``.
    - ``"upn:alice@example.com"`` matches ``"upn:alice@example.com"``  (case-insensitive address).
    - ``"shared:team@example.com"`` matches ``"shared:team@example.com"`` — NOT ``"upn:team@example.com"``.
    - ``"*"`` in ``allow_mailboxes`` matches any spec, but ONLY under app-only auth.
    - ``--unsafe-scope`` falls through to ``/dev/tty`` confirm (same behavior as drive scope).
    """
    allow = cfg.scope.allow_mailboxes

    # Wildcard fast path: only app-only is allowed to use "*".
    if "*" in allow:
        if auth_mode != "app-only":
            raise ScopeViolation(
                "mailbox scope '*' is app-only only; use a specific allow_mailboxes entry for delegated flows"
            )
        return

    for entry in allow:
        if _mailbox_spec_matches(mailbox_spec, entry):
            return

    if not unsafe_scope:
        raise ScopeViolation(
            f"mailbox {mailbox_spec!r} not in scope.allow_mailboxes; "
            f"pass --unsafe-scope to override (requires TTY confirm)"
        )

    prompt = (
        f"UNSAFE SCOPE: mailbox {mailbox_spec!r} is outside allow_mailboxes.\n"
        f"Proceed anyway? [y/N]: "
    )
    if not _confirm_via_tty(prompt):
        raise ScopeViolation(
            f"user declined /dev/tty confirm for unsafe-scope mailbox {mailbox_spec!r}"
        )


def is_folder_denied(folder_path: str, cfg: Config) -> bool:
    """Return True if ``folder_path`` matches the hard-coded deny list OR a
    user-configured pattern in ``scope.deny_folders``.

    Match semantics: ``fnmatch`` glob. Paths are matched BOTH against the
    pattern and (for patterns ending in ``/*``) against the bare parent.
    """
    for pat in HARDCODED_DENY_FOLDERS | frozenset(cfg.scope.deny_folders):
        if fnmatch.fnmatch(folder_path, pat):
            return True
        if pat.endswith("/*") and folder_path == pat[:-2]:
            return True
    return False
