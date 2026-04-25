"""`m365ctl mail <verb>` — mail CLI dispatcher.

Phase 1 (readers):
- auth          device-code login (alias of od-auth; shared cache)
- whoami        identity + scopes + mailbox access summary
- list          list messages in a folder
- get           fetch one message
- search        server-side /search/query
- folders       list folders (Phase 2 also adds create/rename/move/delete subcommands)
- categories    list master categories (Phase 2 adds add/update/remove/sync)
- rules         list / show inbox rules
- settings      show mailbox settings
- attach        list / get attachments

Phase 3 (safe message mutations):
- move          move one or more messages (single or bulk plan)
- copy          copy one or more messages
- flag          set / clear flag
- read          mark read / unread
- focus         set inferenceClassification (focused / other)
- categorize    add / remove / set categories

Phase 4 (soft delete):
- delete        soft-delete messages (→ Deleted Items, reversible via `undo`)
"""
from __future__ import annotations

import sys

_USAGE = (
    "usage: m365ctl mail <verb> [args...]\n"
    "\n"
    "Readers:\n"
    "  auth         login | whoami\n"
    "  whoami       identity + scopes + mailbox access\n"
    "  list         list messages in a folder\n"
    "  get          fetch a single message\n"
    "  search       server-side message search\n"
    "  folders      list mail folders (+ create/rename/move/delete subcommands)\n"
    "  categories   list master categories (+ add/update/remove/sync subcommands)\n"
    "  rules        list / show inbox rules\n"
    "  settings     show mailbox settings\n"
    "  attach       list / get attachments\n"
    "  catalog      catalog refresh / catalog status (DuckDB mirror)\n"
    "\n"
    "Mutations (safe — all undoable):\n"
    "  move         move one or more messages\n"
    "  copy         copy one or more messages\n"
    "  flag         set / clear flag\n"
    "  read         mark read / unread\n"
    "  focus        set inferenceClassification\n"
    "  categorize   add / remove / set categories\n"
    "  delete       soft-delete messages (→ Deleted Items)\n"
    "  draft        create/update/delete drafts (undoable)\n"
    "  send         send draft or inline (IRREVERSIBLE)\n"
    "  reply        reply to a message (IRREVERSIBLE — inline send)\n"
    "  forward      forward a message (IRREVERSIBLE — inline send)\n"
    "  triage       triage validate <yaml> | triage run --rules <yaml> [--plan-out|--confirm]\n"
    "  ooo          ooo show | ooo on --message ... | ooo off (auto-reply / OOO)\n"
    "\nHard delete (permanent) lands in Phase 6 — `mail clean`. Use with care.\n"
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
    elif verb == "move":
        from m365ctl.mail.cli.move import main as f
    elif verb == "copy":
        from m365ctl.mail.cli.copy import main as f
    elif verb == "flag":
        from m365ctl.mail.cli.flag import main as f
    elif verb == "read":
        from m365ctl.mail.cli.read import main as f
    elif verb == "focus":
        from m365ctl.mail.cli.focus import main as f
    elif verb == "categorize":
        from m365ctl.mail.cli.categorize import main as f
    elif verb == "delete":
        from m365ctl.mail.cli.delete import main as f
    elif verb == "draft":
        from m365ctl.mail.cli.draft import main as f
    elif verb == "send":
        from m365ctl.mail.cli.send import main as f
    elif verb == "reply":
        from m365ctl.mail.cli.reply import main as f
    elif verb == "forward":
        from m365ctl.mail.cli.forward import main as f
    elif verb == "triage":
        from m365ctl.mail.cli.triage import main as f
    elif verb == "catalog":
        from m365ctl.mail.cli.catalog import main as f
    elif verb == "ooo":
        from m365ctl.mail.cli.ooo import main as f
    else:
        print(f"m365ctl mail: unknown verb {verb!r}\n\n{_USAGE}", file=sys.stderr)
        return 2
    return f(rest) or 0
