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

from fazla_od.config import Config


class _HasScopeFields(Protocol):
    drive_id: str
    item_id: str
    full_path: str


class ScopeViolation(RuntimeError):
    """Raised when an item is outside allow_drives or matches deny_paths.

    Kill chain: caught by the CLI's top-level handler, which exits with
    code 2 and prints ``str(err)``. Not caught anywhere deeper.
    """


def _confirm_via_tty(prompt: str) -> bool:
    """Read y/N from /dev/tty directly. Returns False if no TTY.

    Opens /dev/tty read-write and prompts there; an agent piping into
    the command's stdin cannot intercept this.
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
    # Plan 4 keeps allow_drives a plain string set. Plan 3 introduces the
    # "me" and "site:<slug>" synonyms into the config; here we treat the
    # raw string value equality only. Scope resolution before us already
    # collapsed friendly names to drive ids where applicable.
    return item.drive_id in cfg.scope.allow_drives


def _deny_match(item: _HasScopeFields, cfg: Config) -> str | None:
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
