"""Convenience wrapper around catalog ``size_per_folder`` query.

Tiny helper used by the CLI: opens the catalog, runs the canned query, returns
the rows. Truncation by ``--top`` happens in the CLI layer so the wrapper
stays a pure read.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.catalog.queries import size_per_folder


def build_size_report(
    catalog_path: Path,
    *,
    mailbox_upn: str,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """Return per-folder size + count rows ordered by total_size desc.

    ``top`` truncates to the first N rows (post-sort). If the catalog file is
    missing, returns ``[]``.
    """
    if not catalog_path.exists():
        return []
    with open_catalog(catalog_path) as conn:
        rows = size_per_folder(conn, mailbox_upn=mailbox_upn)
    if top is not None and top > 0:
        rows = rows[:top]
    return rows


__all__ = ["build_size_report"]
