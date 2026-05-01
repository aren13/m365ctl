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
from typing import Any, Callable, Iterable, Iterator, Protocol, TypeVar, cast, runtime_checkable
from urllib.parse import urlencode

from m365ctl.common.audit import AuditLogger
from m365ctl.common.batch import BatchFuture
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation, Plan, write_plan
from m365ctl.common.safety import _confirm_via_tty
from m365ctl.mail.endpoints import AuthMode
from m365ctl.mail.mutate._common import MailResult  # noqa: F401  (re-export convenience)
from m365ctl.mail.messages import (
    MessageListFilters,
    _messages_url,
    _derive_mailbox_upn,
)
from m365ctl.mail.models import Message


@runtime_checkable
class _OpResult(Protocol):
    """Duck-typed result shared by mail and onedrive verb result dataclasses."""
    status: str  # "ok" | "error"


_R = TypeVar("_R", bound=_OpResult)


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
    _list_messages_impl: Callable[..., Iterable[Message]] | None = None,
) -> Iterator[Message]:
    """Yield messages across ``resolved_folders`` (list of ``(folder_id, folder_path)``).

    The server-side ``$filter`` is pushed down. Iteration stops at ``limit``
    total messages across all folders.

    When ``_list_messages_impl`` is None (production), the FIRST page of every
    folder's listing is fetched in a single ``/$batch`` POST. Subsequent pages
    of each folder are walked serially via ``@odata.nextLink`` (pagination is
    inherently sequential per stream). When an injected impl is supplied —
    legacy unit-test path — we fall back to the simpler serial-per-folder
    iteration.
    """
    yielded = 0
    list_filters = filter.as_list_filters()
    if _list_messages_impl is not None:
        # Legacy serial path for tests that inject a fake list_messages.
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
        return

    if not resolved_folders:
        return

    mailbox_upn = _derive_mailbox_upn(mailbox_spec)

    # Phase 1: build per-folder URLs, then batch the first-page GETs across
    # all folders in one /$batch (auto-flushes every 20 enqueued).
    # Pagination within each folder stream stays sequential (below).
    auth_mode_lit = cast("AuthMode", auth_mode)
    folder_urls: list[tuple[str, str, str]] = []  # (fid, fpath, url)
    for folder_id, folder_path in resolved_folders:
        path, params = _messages_url(
            mailbox_spec=mailbox_spec,
            auth_mode=auth_mode_lit,
            folder_id=folder_id,
            filters=list_filters,
            limit=limit,
            page_size=page_size,
        )
        folder_urls.append((folder_id, folder_path, f"{path}?{urlencode(params)}"))

    with graph.batch() as b:
        first_pages: list[tuple[str, str, BatchFuture]] = [
            (fid, fpath, b.get(url)) for (fid, fpath, url) in folder_urls
        ]

    # Phase 2: walk each folder's pages serially, yielding messages.
    for folder_id, folder_path, fut in first_pages:
        try:
            body = fut.result()
        except GraphError:
            # If a single folder's first-page GET failed, surface the error
            # in the same way the eager path would have: the ``GraphError``
            # propagates out of the iterator. Preserves prior behavior.
            raise
        items = body.get("value", []) if isinstance(body, dict) else []
        next_link = body.get("@odata.nextLink") if isinstance(body, dict) else None
        for raw in items:
            yield Message.from_graph_json(
                raw, mailbox_upn=mailbox_upn, parent_folder_path=folder_path,
            )
            yielded += 1
            if yielded >= limit:
                return
        # Subsequent pages stay sequential (each depends on the prior link).
        while next_link:
            page = graph.get_absolute(next_link)
            page_items = page.get("value", []) if isinstance(page, dict) else []
            next_link = page.get("@odata.nextLink") if isinstance(page, dict) else None
            for raw in page_items:
                yield Message.from_graph_json(
                    raw, mailbox_upn=mailbox_upn, parent_folder_path=folder_path,
                )
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


def execute_plan_in_batches(
    *,
    graph: GraphClient,
    logger: AuditLogger,
    ops: list[Operation],
    fetch_before: Callable[[Any, Operation], BatchFuture] | None,
    parse_before: Callable[[Operation, dict | None, GraphError | None], dict],
    start_op: Callable[..., tuple[BatchFuture, dict]],
    finish_op: Callable[..., _R],
    on_result: Callable[[Operation, _R], None],
) -> int:
    """Two-phase batched plan execution.

    Phase 1: batch all `before` GETs (skipped if ``fetch_before`` is None).
    Phase 2: buffer all mutations under one BatchSession; the ``with`` exit
    flushes them. Then resolve futures via ``finish_op`` for each.

    Generic over the result type (``_R``) so the helper is shared between
    mail and onedrive verbs whose result dataclasses differ but share a
    ``.status`` attribute (see ``_OpResult``).

    Phase 1 sub-failures are routed through ``parse_before(op, None, err)``
    so the verb decides what `before` state to record. Exceptions raised by
    ``parse_before`` itself (e.g. malformed Graph response) propagate to
    the caller; the contract assumes ``parse_before`` is a small pure
    projection over the returned body.

    Returns 1 if any op errored, else 0.
    """
    # Phase 1: pre-mutation lookups.
    befores: dict[str, dict] = {}
    if fetch_before is not None:
        with graph.batch() as b:
            phase1 = [(op, fetch_before(b, op)) for op in ops]
        for op, f in phase1:
            try:
                befores[op.op_id] = parse_before(op, f.result(), None)
            except GraphError as e:
                befores[op.op_id] = parse_before(op, None, e)

    # Phase 2: buffer all mutations under a single BatchSession.
    # Order matters: start_op registers a BatchFuture per call, and the
    # with-exit flushes them in registration order. Keep this a list-comp
    # over `ops` so (op, future, after) tuples stay aligned by identity.
    with graph.batch() as b:
        pending = [
            (op, *start_op(op, b, logger, before=befores.get(op.op_id, {})))
            for op in ops
        ]

    any_error = False
    for op, future, after in pending:
        result = finish_op(op, future, after, logger)
        on_result(op, result)
        if result.status != "ok":
            any_error = True
    return 1 if any_error else 0
