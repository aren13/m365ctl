"""`m365ctl mail <verb>` — reader dispatcher.

Verbs that land in Phase 1:
- auth          device-code login (alias of od-auth; shared cache)
- whoami        identity + scopes + mailbox access summary
- list          list messages in a folder
- get           fetch one message
- search        server-side /search/query
- folders       list folders (tree / flat / with-counts)
- categories    list master categories
- rules         list / show inbox rules
- settings      show mailbox settings
- attach        list / get attachments

Mutation verbs (move, delete, flag, compose, ...) land in Phase 2+.
"""
from __future__ import annotations

import sys

_USAGE = (
    "usage: m365ctl mail <verb> [args...]\n"
    "\n"
    "Read-only verbs (Phase 1):\n"
    "  auth         login | whoami\n"
    "  whoami       identity + scopes + mailbox access\n"
    "  list         list messages in a folder\n"
    "  get          fetch a single message\n"
    "  search       server-side message search\n"
    "  folders      list mail folders\n"
    "  categories   list master categories\n"
    "  rules        list / show inbox rules\n"
    "  settings     show mailbox settings\n"
    "  attach       list / get attachments\n"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(_USAGE)
        return 0 if args else 2
    verb = args[0]
    rest = args[1:]
    if verb == "auth":
        from m365ctl.mail.cli.auth import main as f
    elif verb == "whoami":
        from m365ctl.mail.cli.whoami import main as f
    elif verb == "list":
        from m365ctl.mail.cli.list import main as f
    elif verb == "get":
        from m365ctl.mail.cli.get import main as f
    elif verb == "search":
        from m365ctl.mail.cli.search import main as f
    elif verb == "folders":
        from m365ctl.mail.cli.folders import main as f
    elif verb == "categories":
        from m365ctl.mail.cli.categories import main as f
    elif verb == "rules":
        from m365ctl.mail.cli.rules import main as f
    elif verb == "settings":
        from m365ctl.mail.cli.settings import main as f
    elif verb == "attach":
        from m365ctl.mail.cli.attach import main as f
    else:
        print(f"m365ctl mail: unknown verb {verb!r}\n\n{_USAGE}", file=sys.stderr)
        return 2
    return f(rest) or 0
