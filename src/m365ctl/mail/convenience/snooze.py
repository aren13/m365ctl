"""Snooze convenience: ``Deferred/<YYYY-MM-DD>`` + ``Snooze/<YYYY-MM-DD>`` category.

Snooze composes existing mutators only — every state change goes through
``mail.move`` and ``mail.categorize``. There is no new audit-action namespace.

CLI surface (lives in :mod:`m365ctl.mail.cli.snooze`)::

    mail snooze <message-id> --until <date-or-relative> --confirm
    mail snooze --process [--confirm]

This module exposes the pure parsing + plan-building helpers.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from m365ctl.common.planfile import Operation, new_op_id


class SnoozeError(ValueError):
    """Raised by :func:`parse_until` for malformed input."""


_REL_RE = re.compile(r"^(?P<n>\d+)(?P<unit>[hd])$")
_DEFERRED_RE = re.compile(r"^Deferred/(?P<date>\d{4}-\d{2}-\d{2})$")


def parse_until(s: str, *, now: datetime | None = None) -> date:
    """Parse ``YYYY-MM-DD`` or ``<N>d`` / ``<N>h`` into a target date.

    Relative inputs are resolved against ``now`` (UTC by default). The
    returned value is always a calendar :class:`datetime.date`; sub-day
    relative deltas (``24h``) collapse to the resulting day.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    s = (s or "").strip()
    if not s:
        raise SnoozeError("--until cannot be empty")
    m = _REL_RE.match(s)
    if m:
        n = int(m.group("n"))
        unit = m.group("unit")
        delta = timedelta(hours=n) if unit == "h" else timedelta(days=n)
        return (now + delta).date()
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise SnoozeError(
            f"--until {s!r} is neither YYYY-MM-DD nor <N>d / <N>h"
        ) from e


def build_snooze_ops(
    message_id: str,
    due_date: date,
    mailbox_upn: str,
) -> list[Operation]:
    """Return [move-to-Deferred/<date>, categorize-add-Snooze/<date>]."""
    iso = due_date.isoformat()
    move_op = Operation(
        op_id=new_op_id(),
        action="mail.move",
        drive_id=mailbox_upn,
        item_id=message_id,
        args={"to_folder": f"Deferred/{iso}"},
        dry_run_result=f"would move -> Deferred/{iso}",
    )
    cat_op = Operation(
        op_id=new_op_id(),
        action="mail.categorize",
        drive_id=mailbox_upn,
        item_id=message_id,
        args={"add": [f"Snooze/{iso}"]},
        dry_run_result=f"would add category Snooze/{iso}",
    )
    return [move_op, cat_op]


def find_due_snoozed(
    folder_paths: list[str],
    *,
    today: date,
) -> list[tuple[str, date]]:
    """Filter ``folder_paths`` to ``Deferred/<YYYY-MM-DD>`` with date ≤ today.

    Returns ``[(folder_path, due_date), ...]`` ordered by due-date ascending.
    Folders that don't match the convention or that lie in the future are
    skipped silently.
    """
    out: list[tuple[str, date]] = []
    for path in folder_paths:
        m = _DEFERRED_RE.match(path)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group("date"))
        except ValueError:
            continue
        if d <= today:
            out.append((path, d))
    out.sort(key=lambda pd: pd[1])
    return out


def build_unsnooze_ops(
    message_id: str,
    *,
    due_date: date,
    mailbox_upn: str,
    current_categories: list[str] | None = None,
) -> list[Operation]:
    """Return ops to move a message back to Inbox + drop ``Snooze/<date>``.

    When ``current_categories`` is given, the categorize op carries the final
    list (``categories``) with the matching ``Snooze/<date>`` removed.
    Otherwise it carries a ``remove`` arg the CLI resolves at dispatch time.
    """
    iso = due_date.isoformat()
    snooze_cat = f"Snooze/{iso}"
    move_op = Operation(
        op_id=new_op_id(),
        action="mail.move",
        drive_id=mailbox_upn,
        item_id=message_id,
        args={"to_folder": "Inbox"},
        dry_run_result="would move -> Inbox",
    )
    if current_categories is not None:
        final = [c for c in current_categories if c != snooze_cat]
        cat_args = {"categories": final}
    else:
        cat_args = {"remove": [snooze_cat]}
    cat_op = Operation(
        op_id=new_op_id(),
        action="mail.categorize",
        drive_id=mailbox_upn,
        item_id=message_id,
        args=cat_args,
        dry_run_result=f"would remove category {snooze_cat}",
    )
    return [move_op, cat_op]


__all__ = [
    "SnoozeError",
    "parse_until",
    "build_snooze_ops",
    "find_due_snoozed",
    "build_unsnooze_ops",
]
