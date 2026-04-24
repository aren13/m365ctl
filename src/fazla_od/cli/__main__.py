"""fazla-od command dispatcher."""
from __future__ import annotations

import sys

from fazla_od.cli import audit_sharing as audit_sharing_cli
from fazla_od.cli import auth as auth_cli
from fazla_od.cli import catalog as catalog_cli
from fazla_od.cli import copy as copy_cli
from fazla_od.cli import delete as delete_cli
from fazla_od.cli import download as download_cli
from fazla_od.cli import inventory as inventory_cli
from fazla_od.cli import move as move_cli
from fazla_od.cli import rename as rename_cli
from fazla_od.cli import search as search_cli

_SUBCOMMANDS = {
    "audit-sharing": audit_sharing_cli.main,
    "auth": auth_cli.main,
    "catalog": catalog_cli.main,
    "copy": copy_cli.main,
    "delete": delete_cli.main,
    "download": download_cli.main,
    "inventory": inventory_cli.main,
    "move": move_cli.main,
    "rename": rename_cli.main,
    "search": search_cli.main,
}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: fazla-od <subcommand> [args...]")
        print(f"  subcommands: {', '.join(_SUBCOMMANDS)}")
        return 0 if argv else 2
    sub = argv[0]
    if sub not in _SUBCOMMANDS:
        print(f"unknown subcommand: {sub}", file=sys.stderr)
        return 2
    return _SUBCOMMANDS[sub](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
