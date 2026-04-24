"""Shared bulk helpers for mail mutation CLIs.

Two responsibilities:
1. **Pattern expansion** — given a set of resolved folders + a filter spec,
   iterate messages across them (stopping at ``limit``). Callers pass a
   ``MessageFilter`` dataclass whose shape mirrors ``mail.messages.MessageListFilters``
   so the server-side OData filter can be pushed down.
2. **Plan I/O** — write a ``Plan`` to disk via the existing ``planfile.write_plan``
   with sensible metadata; read + iterate ops via ``planfile.load_plan``.

The third helper (``confirm_bulk_proceed``) gates large plan execution
behind an interactive ``/dev/tty`` confirm — same pattern as
``common.safety._confirm_via_tty``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

from m365ctl.common.graph import GraphClient
from m365ctl.common.planfile import Operation, Plan, write_plan
from m365ctl.common.safety import _confirm_via_tty
from m365ctl.mail.messages import MessageListFilters, list_messages as _default_list_messages
from m365ctl.mail.models import Message


@dataclass(frozen=True)
class MessageFilter:
    """Filter inputs for bulk pattern expansion.

    Mirrors ``mail.messages.MessageListFilters`` so server-side ``$filter``
    clauses are pushed down. Also provides a ``match()`` helper for local
    post-list filtering when the caller wants a second-pass check.
    """
    unread: bool | None = None
    from_address: str | None = None
    subject_contains: str | None = None
    since: str | None = None
    until: str | None = None
    has_attachments: bool | None = None
    importance: str | None = None
    focus: str | None = None
    category: str | None = None

    def as_list_filters(self) -> MessageListFilters:
        return MessageListFilters(
            unread=self.unread,
            from_address=self.from_address,
            subject_contains=self.subject_contains,
            since=self.since,
            until=self.until,
            has_attachments=self.has_attachments,
            importance=self.importance,
            focus=self.focus,
            category=self.category,
        )

    def match(self, m: Message) -> bool:
        """Local predicate for filters the Graph $filter missed."""
        if self.unread is True and m.is_read:
            return False
        if self.unread is False and not m.is_read:
            return False
        if self.from_address and m.from_addr.address.lower() != self.from_address.lower():
            return False
        if self.subject_contains and self.subject_contains.lower() not in m.subject.lower():
            return False
        if self.importance and m.importance != self.importance:
            return False
        if self.focus and m.inference_classification != self.focus:
            return False
        if self.category and self.category not in m.categories:
            return False
        if self.has_attachments is True and not m.has_attachments:
            return False
        if self.has_attachments is False and m.has_attachments:
            return False
        return True


def expand_messages_for_pattern(
    *,
    graph: GraphClient,
    mailbox_spec: str,
    auth_mode: str,
    resolved_folders: list[tuple[str, str]],
    filter: MessageFilter,
    limit: int = 50,
    page_size: int = 50,
    _list_messages_impl: Callable[..., Iterable[Message]] = _default_list_messages,
) -> Iterator[Message]:
    """Yield messages across ``resolved_folders`` (list of ``(folder_id, folder_path)``).

    The server-side ``$filter`` is pushed down via ``list_messages``. Iteration
    stops at ``limit`` total messages across all folders.

    ``_list_messages_impl`` is injected so unit tests can bypass the real
    Graph client. Production callers pass the default.
    """
    yielded = 0
    list_filters = filter.as_list_filters()
    for folder_id, folder_path in resolved_folders:
        for msg in _list_messages_impl(
            graph=graph,
            mailbox_spec=mailbox_spec,
            auth_mode=auth_mode,
            folder_id=folder_id,
            parent_folder_path=folder_path,
            filters=list_filters,
            limit=limit - yielded,
            page_size=page_size,
        ):
            yield msg
            yielded += 1
            if yielded >= limit:
                return


def emit_plan(
    path: Path,
    *,
    source_cmd: str,
    scope: str,
    operations: list[Operation],
) -> None:
    """Write a ``Plan`` to ``path`` with ``PLAN_SCHEMA_VERSION`` + ISO-8601 UTC timestamp."""
    from m365ctl.common.planfile import PLAN_SCHEMA_VERSION
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        source_cmd=source_cmd,
        scope=scope,
        operations=list(operations),
    )
    write_plan(plan, path)


def confirm_bulk_proceed(n: int, *, threshold: int = 20, verb: str) -> bool:
    """Return True if the bulk should proceed.

    - If ``n < threshold``: always True (no prompt).
    - Else: prompt via ``/dev/tty`` (not stdin) for y/N confirmation.

    Designed so Claude/agents piping stdin can't bypass this.
    """
    if n < threshold:
        return True
    prompt = (
        f"m365ctl mail {verb}: about to apply {n} operations from plan file.\n"
        f"Proceed? [y/N]: "
    )
    return _confirm_via_tty(prompt)
