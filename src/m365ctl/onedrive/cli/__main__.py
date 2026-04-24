"""fazla-od command dispatcher."""
from __future__ import annotations

import sys

from m365ctl.onedrive.cli import audit_sharing as audit_sharing_cli
from m365ctl.onedrive.cli import auth as auth_cli
from m365ctl.onedrive.cli import catalog as catalog_cli
from m365ctl.onedrive.cli import clean as clean_cli
from m365ctl.onedrive.cli import copy as copy_cli
from m365ctl.onedrive.cli import delete as delete_cli
from m365ctl.onedrive.cli import download as download_cli
from m365ctl.onedrive.cli import inventory as inventory_cli
from m365ctl.onedrive.cli import label as label_cli
from m365ctl.onedrive.cli import move as move_cli
from m365ctl.onedrive.cli import rename as rename_cli
from m365ctl.onedrive.cli import search as search_cli
from m365ctl.onedrive.cli import undo as undo_cli

_SUBCOMMANDS = {
    "audit-sharing": audit_sharing_cli.main,
    "auth": auth_cli.main,
    "catalog": catalog_cli.main,
    "clean": clean_cli.main,
    "copy": copy_cli.main,
    "delete": delete_cli.main,
    "download": download_cli.main,
    "inventory": inventory_cli.main,
    "label": label_cli.main,
    "move": move_cli.main,
    "rename": rename_cli.main,
    "search": search_cli.main,
    "undo": undo_cli.main,
}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: m365ctl od <subcommand> [args...]")
        print(f"  subcommands: {', '.join(_SUBCOMMANDS)}")
        return 0 if argv else 2
    sub = argv[0]
    if sub not in _SUBCOMMANDS:
        print(f"unknown subcommand: {sub}", file=sys.stderr)
        return 2
    return _SUBCOMMANDS[sub](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
