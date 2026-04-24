"""m365ctl undo - thin delegate; full Dispatcher lands in Group 6."""
from __future__ import annotations

from m365ctl.onedrive.cli.undo import main as _onedrive_undo_main


def main(argv: list[str] | None = None) -> int:
    # Pre-Group 6 shim: the existing OneDrive undo already handles all
    # od.* audit entries (and legacy bare actions). Group 6 replaces this
    # with a domain-agnostic Dispatcher shared with mail.
    return _onedrive_undo_main(argv) or 0
