"""m365ctl <domain> <verb> — cross-domain CLI entry point."""
from __future__ import annotations

import sys

_USAGE = (
    "usage: m365ctl <domain> <verb> [args...]\n"
    "       m365ctl undo <op-id> [--confirm]\n"
    "\n"
    "Domains:\n"
    "  od     OneDrive + SharePoint (catalog, search, move, copy, delete, ...)\n"
    "  mail   Microsoft 365 Mail - reserved for Phase 1+; no verbs yet\n"
    "  undo   Cross-domain audit-log replay (od.* and mail.*)\n"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else 1
    domain = args[0]
    rest = args[1:]
    if domain == "od":
        from m365ctl.onedrive.cli.__main__ import main as od_main
        return od_main(rest) or 0
    if domain == "mail":
        print(
            "m365ctl: mail domain is not yet implemented - scaffold only. "
            "See M365CTL-SPEC.md Phase 1 for delivery target.",
            file=sys.stderr,
        )
        return 2
    if domain == "undo":
        from m365ctl.cli.undo import main as undo_main
        return undo_main(rest) or 0
    print(f"m365ctl: unknown domain {domain!r}\n\n{_USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
