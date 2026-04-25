"""Predicate evaluator for the triage DSL.

Operates on dicts that look like rows from
``m365ctl.mail.catalog.queries`` (the catalog message schema).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from m365ctl.mail.triage.dsl import (
    AgeP, BodyP, CategoriesP, CcP, FlaggedP, FocusP, FolderP, FromP,
    HasAttachmentsP, HeadersP, ImportanceP, Match, Predicate,
    SubjectP, ThreadP, ToP, UnreadP,
)


@dataclass
class MatchContext:
    """Pre-computed cross-row data + lazy fetcher caches.

    Built once per ruleset run by the plan emitter. ``thread`` predicates
    consult ``replied_conversations`` to avoid per-evaluation rebuilds.
    ``headers`` predicates use ``header_fetcher`` (one Graph GET per
    message) plus the in-memory ``header_cache`` to avoid duplicate
    fetches for the same message within a single triage run.
    """
    replied_conversations: frozenset[str] = field(default_factory=frozenset)
    header_fetcher: Callable[[str], list[dict[str, str]]] | None = None
    header_cache: dict[str, list[dict[str, str]]] = field(default_factory=dict)


def evaluate_match(
    match: Match,
    row: dict[str, Any],
    *,
    now: datetime,
    context: MatchContext | None = None,
) -> bool:
    ctx = context if context is not None else MatchContext()
    if match.all_of and not all(_eval(p, row, now=now, context=ctx) for p in match.all_of):
        return False
    if match.any_of and not any(_eval(p, row, now=now, context=ctx) for p in match.any_of):
        return False
    if match.none_of and any(_eval(p, row, now=now, context=ctx) for p in match.none_of):
        return False
    return True


def _eval(p: Predicate, row: dict[str, Any], *, now: datetime, context: MatchContext) -> bool:
    if isinstance(p, FromP):
        return _eval_from(p, row)
    if isinstance(p, ToP):
        return _eval_to(p, row)
    if isinstance(p, CcP):
        return _eval_cc(p, row)
    if isinstance(p, SubjectP):
        return _eval_subject(p, row)
    if isinstance(p, BodyP):
        return _eval_body(p, row)
    if isinstance(p, FolderP):
        return _eval_folder(p, row)
    if isinstance(p, AgeP):
        return _eval_age(p, row, now=now)
    if isinstance(p, UnreadP):
        return bool(row.get("is_read") is False) is p.value
    if isinstance(p, FlaggedP):
        flagged = (row.get("flag_status") or "").lower() == "flagged"
        return flagged is p.value
    if isinstance(p, HasAttachmentsP):
        return bool(row.get("has_attachments")) is p.value
    if isinstance(p, CategoriesP):
        return _eval_categories(p, row)
    if isinstance(p, FocusP):
        return (row.get("inference_class") or "") == p.equals
    if isinstance(p, ImportanceP):
        return (row.get("importance") or "") == p.equals
    if isinstance(p, ThreadP):
        return _eval_thread(p, row, context=context)
    if isinstance(p, HeadersP):
        return _eval_headers(p, row, context=context)
    raise TypeError(f"unhandled predicate type: {type(p).__name__}")


def _eval_from(p: FromP, row: dict[str, Any]) -> bool:
    addr = (row.get("from_address") or "").lower()
    if not addr:
        return False
    if p.address is not None:
        if p.address.lower() != addr:
            return False
    if p.address_in is not None:
        if addr not in {a.lower() for a in p.address_in}:
            return False
    if p.domain_in is not None:
        domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
        if domain not in {d.lower() for d in p.domain_in}:
            return False
    return True


def _eval_to(p: ToP, row: dict[str, Any]) -> bool:
    raw = (row.get("to_addresses") or "").lower()
    if not raw:
        return False
    addrs = [a.strip() for a in raw.split(",") if a.strip()]
    if p.address is not None and p.address.lower() not in addrs:
        return False
    if p.address_in is not None:
        wanted = {a.lower() for a in p.address_in}
        if not wanted.intersection(addrs):
            return False
    if p.domain_in is not None:
        domains = {a.split("@", 1)[-1] for a in addrs if "@" in a}
        wanted_d = {d.lower() for d in p.domain_in}
        if not wanted_d.intersection(domains):
            return False
    return True


def _eval_cc(p: CcP, row: dict[str, Any]) -> bool:
    raw = (row.get("cc_addresses") or "").lower()
    if not raw:
        return False
    addrs = [a.strip() for a in raw.split(",") if a.strip()]
    if p.address is not None and p.address.lower() not in addrs:
        return False
    if p.address_in is not None:
        wanted = {a.lower() for a in p.address_in}
        if not wanted.intersection(addrs):
            return False
    if p.domain_in is not None:
        domains = {a.split("@", 1)[-1] for a in addrs if "@" in a}
        wanted_d = {d.lower() for d in p.domain_in}
        if not wanted_d.intersection(domains):
            return False
    return True


def _eval_body(p: BodyP, row: dict[str, Any]) -> bool:
    s = row.get("body_preview") or ""
    if not s and any(
        v is not None for v in (
            p.equals, p.contains, p.starts_with, p.ends_with, p.regex,
        )
    ):
        return False
    if p.equals is not None and s != p.equals:
        return False
    if p.contains is not None and p.contains.lower() not in s.lower():
        return False
    if p.starts_with is not None and not s.startswith(p.starts_with):
        return False
    if p.ends_with is not None and not s.endswith(p.ends_with):
        return False
    if p.regex is not None and not re.search(p.regex, s):
        return False
    return True


def _eval_subject(p: SubjectP, row: dict[str, Any]) -> bool:
    s = row.get("subject") or ""
    if p.equals is not None and s != p.equals:
        return False
    if p.contains is not None and p.contains.lower() not in s.lower():
        return False
    if p.starts_with is not None and not s.startswith(p.starts_with):
        return False
    if p.ends_with is not None and not s.endswith(p.ends_with):
        return False
    if p.regex is not None and not re.search(p.regex, s):
        return False
    return True


def _eval_folder(p: FolderP, row: dict[str, Any]) -> bool:
    path = row.get("parent_folder_path") or ""
    if p.equals is not None:
        return path == p.equals
    if p.in_ is not None:
        return path in p.in_
    return True


def _eval_age(p: AgeP, row: dict[str, Any], *, now: datetime) -> bool:
    received = row.get("received_at")
    if received is None:
        return False
    if isinstance(received, str):
        received = datetime.fromisoformat(received.replace("Z", "+00:00"))
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    age = now - received
    if p.older_than_days is not None and age < timedelta(days=p.older_than_days):
        return False
    if p.newer_than_days is not None and age >= timedelta(days=p.newer_than_days):
        return False
    return True


def _eval_categories(p: CategoriesP, row: dict[str, Any]) -> bool:
    cats_raw = row.get("categories") or ""
    cats = [c for c in cats_raw.split(",") if c]
    if p.equals is not None and p.equals not in cats:
        return False
    if p.contains is not None and not any(p.contains in c for c in cats):
        return False
    if p.in_ is not None and not any(c in p.in_ for c in cats):
        return False
    return True


def _eval_thread(p: ThreadP, row: dict[str, Any], *, context: MatchContext) -> bool:
    cid = row.get("conversation_id") or ""
    if not cid:
        return False
    is_replied = cid in context.replied_conversations
    return is_replied == p.has_reply


def _eval_headers(
    p: HeadersP, row: dict[str, Any], *, context: MatchContext,
) -> bool:
    headers = _get_headers_for(row, context)
    if headers is None:
        return False
    needle_name = p.name.lower()
    for h in headers:
        hname = (h.get("name") or "").lower()
        if hname != needle_name:
            continue
        value = h.get("value") or ""
        if p.equals is None and p.contains is None and p.regex is None:
            return True   # existence-only match
        if p.equals is not None and value == p.equals:
            return True
        if p.contains is not None and p.contains.lower() in value.lower():
            return True
        if p.regex is not None and re.search(p.regex, value):
            return True
    return False


def _get_headers_for(
    row: dict[str, Any], context: MatchContext,
) -> list[dict[str, str]] | None:
    """Return cached headers for this message, fetching once if needed."""
    msg_id = row.get("message_id")
    if not msg_id:
        return None
    if msg_id in context.header_cache:
        return context.header_cache[msg_id]
    if context.header_fetcher is None:
        return None
    headers = context.header_fetcher(msg_id)
    context.header_cache[msg_id] = headers
    return headers
