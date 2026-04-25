"""Predicate evaluator for the triage DSL.

Operates on dicts that look like rows from
``m365ctl.mail.catalog.queries`` (the catalog message schema).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from m365ctl.mail.triage.dsl import (
    AgeP, CategoriesP, FlaggedP, FocusP, FolderP, FromP,
    HasAttachmentsP, ImportanceP, Match, Predicate,
    SubjectP, UnreadP,
)


def evaluate_match(match: Match, row: dict[str, Any], *, now: datetime) -> bool:
    if match.all_of and not all(_eval(p, row, now=now) for p in match.all_of):
        return False
    if match.any_of and not any(_eval(p, row, now=now) for p in match.any_of):
        return False
    if match.none_of and any(_eval(p, row, now=now) for p in match.none_of):
        return False
    return True


def _eval(p: Predicate, row: dict[str, Any], *, now: datetime) -> bool:
    if isinstance(p, FromP):
        return _eval_from(p, row)
    if isinstance(p, SubjectP):
        return _eval_subject(p, row)
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
