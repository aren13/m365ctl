"""m365ctl undo - cross-domain audit-log replay."""
from __future__ import annotations

from m365ctl.onedrive.cli.undo import main as _onedrive_undo_main


def main(argv: list[str] | None = None) -> int:
    # Group 6: all registered inverses are `od.*`. Phase 1 wires `mail.*` by
    # adding `register_mail_inverses(<same dispatcher singleton>)`. The
    # existing onedrive undo CLI uses the Dispatcher-backed lookup +
    # legacy-action normalization, so it is already the cross-domain entry
    # point — mail support is additive, not a rewrite.
    return _onedrive_undo_main(argv) or 0
