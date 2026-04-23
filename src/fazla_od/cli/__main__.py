"""fazla-od command dispatcher."""
from __future__ import annotations

import sys

from fazla_od.cli import auth as auth_cli
from fazla_od.cli import catalog as catalog_cli

_SUBCOMMANDS = {
    "auth": auth_cli.main,
    "catalog": catalog_cli.main,
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
