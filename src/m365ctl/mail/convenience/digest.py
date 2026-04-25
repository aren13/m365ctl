"""Build an unread-mail digest from catalog rows.

Pure data → text/HTML transformation. No Graph calls inside this module
(the CLI fetches catalog rows and feeds them in).

Sections:
  1. Top senders by count (desc), capped at top 10.
  2. By category (categories field, comma-joined → buckets).
  3. Recent (newest by received_at, capped at limit).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


class DigestError(ValueError):
    pass


@dataclass(frozen=True)
class DigestEntry:
    message_id: str
    subject: str
    from_address: str
    received_at: datetime
    categories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Digest:
    since: datetime
    now: datetime
    total: int
    top_senders: list[tuple[str, int]]
    by_category: dict[str, int]
    recent: list[DigestEntry]


def parse_since(s: str, *, now: datetime | None = None) -> datetime:
    """Convert ``24h`` / ``3d`` / ISO string into an absolute datetime."""
    if now is None:
        now = datetime.now(timezone.utc)
    s = s.strip()
    if not s:
        raise DigestError("--since cannot be empty")
    if s[-1] in ("h", "d") and s[:-1].isdigit():
        n = int(s[:-1])
        if s.endswith("h"):
            return now - timedelta(hours=n)
        return now - timedelta(days=n)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as e:
        raise DigestError(
            f"--since {s!r} is neither a duration ({{N}}h|{{N}}d) "
            f"nor an ISO-8601 datetime"
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def build_digest(
    rows: list[dict[str, Any]],
    *,
    since: datetime,
    now: datetime,
    limit: int = 20,
) -> Digest:
    filtered = [r for r in rows if _received_after(r, since)]
    sender_counts: Counter[str] = Counter(
        r.get("from_address") or "(unknown)" for r in filtered
    )
    by_category: Counter[str] = Counter()
    for r in filtered:
        cats = (r.get("categories") or "").split(",")
        for c in cats:
            c = c.strip()
            if c:
                by_category[c] += 1
        if not any(c.strip() for c in cats):
            by_category["(uncategorised)"] += 1
    recent_rows = sorted(
        filtered,
        key=lambda r: _to_dt(r.get("received_at")),
        reverse=True,
    )[:limit]
    recent = [
        DigestEntry(
            message_id=r["message_id"],
            subject=r.get("subject") or "",
            from_address=r.get("from_address") or "",
            received_at=_to_dt(r.get("received_at")),
            categories=[
                c.strip()
                for c in (r.get("categories") or "").split(",")
                if c.strip()
            ],
        )
        for r in recent_rows
    ]
    return Digest(
        since=since,
        now=now,
        total=len(filtered),
        top_senders=sender_counts.most_common(10),
        by_category=dict(by_category),
        recent=recent,
    )


def _received_after(row: dict[str, Any], since: datetime) -> bool:
    received = row.get("received_at")
    if received is None:
        return False
    received_dt = _to_dt(received)
    return received_dt >= since


def _to_dt(received: Any) -> datetime:
    if isinstance(received, datetime):
        if received.tzinfo is None:
            return received.replace(tzinfo=timezone.utc)
        return received
    if isinstance(received, str):
        d = datetime.fromisoformat(received.replace("Z", "+00:00"))
        if d.tzinfo is None:
            return d.replace(tzinfo=timezone.utc)
        return d
    return datetime.min.replace(tzinfo=timezone.utc)


def render_text(d: Digest) -> str:
    lines = [
        f"Mail digest — since {d.since.isoformat(timespec='minutes')} "
        f"(now {d.now.isoformat(timespec='minutes')})",
        f"Total: {d.total} unread",
        "",
        "Top senders:",
    ]
    for addr, n in d.top_senders:
        lines.append(f"  {n:>4} {addr}")
    lines += ["", "By category:"]
    for cat, n in sorted(d.by_category.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:>4} {cat}")
    lines += ["", f"Recent ({len(d.recent)}):"]
    for e in d.recent:
        ts = e.received_at.isoformat(timespec="minutes")
        lines.append(f"  {ts}  {e.from_address:<32}  {e.subject}")
    return "\n".join(lines) + "\n"


def render_html(d: Digest) -> str:
    parts = [
        f"<h2>Mail digest — since {d.since.isoformat(timespec='minutes')}</h2>",
        f"<p><strong>Total:</strong> {d.total} unread</p>",
        "<h3>Top senders</h3><ul>",
        *(f"<li>{n} — {addr}</li>" for addr, n in d.top_senders),
        "</ul>",
        "<h3>By category</h3><ul>",
        *(
            f"<li>{n} — {cat}</li>"
            for cat, n in sorted(d.by_category.items(), key=lambda kv: -kv[1])
        ),
        "</ul>",
        f"<h3>Recent ({len(d.recent)})</h3><ul>",
    ]
    for e in d.recent:
        ts = e.received_at.isoformat(timespec="minutes")
        parts.append(
            f"<li>{ts} — <strong>{e.from_address}</strong>: {e.subject}</li>"
        )
    parts.append("</ul>")
    return "\n".join(parts)
