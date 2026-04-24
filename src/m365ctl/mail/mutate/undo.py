"""Build reverse-ops for mail mutations.

Mirror of ``onedrive.mutate.undo`` but scoped to the Phase 2 mail verbs:

    mail-folder-create      -> mail.folder.delete (on the new folder id)
    mail-folder-rename      -> mail.folder.rename back to before.display_name
    mail-folder-move        -> mail.folder.move back to before.parent_id
    mail-folder-delete      -> Irreversible (Phase 2 — folder restore is Phase 4+)
    mail-categories-add     -> mail.categories.remove on after.id
    mail-categories-update  -> mail.categories.update back to before
    mail-categories-remove  -> mail.categories.add from before.{display_name, color}
                               (NOTE: message->category links cannot be re-linked)
"""
from __future__ import annotations

from m365ctl.common.audit import AuditLogger, find_op_by_id
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.common.undo import Dispatcher
from m365ctl.onedrive.mutate.undo import Irreversible


def build_reverse_mail_operation(logger: AuditLogger, op_id: str) -> Operation:
    start, end = find_op_by_id(logger, op_id)
    if start is None or end is None:
        raise Irreversible(f"op {op_id!r} not found in audit log")
    if end.get("result") != "ok":
        raise Irreversible(
            f"op {op_id!r} did not succeed originally (result={end.get('result')!r})"
        )

    cmd = start.get("cmd", "")
    before = start.get("before", {}) or {}
    after = end.get("after", {}) or {}
    drive_id = start["drive_id"]

    if cmd == "mail-folder-create":
        new_id = after.get("id")
        if not new_id:
            raise Irreversible(
                f"mail-folder-create op {op_id!r} has no recorded id in after; "
                f"cannot locate the folder to delete."
            )
        return Operation(
            op_id=new_op_id(), action="mail.folder.delete",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) delete created folder "
                           f"{after.get('path', new_id)!r}",
        )

    if cmd == "mail-folder-rename":
        prior = before.get("display_name")
        if not prior:
            raise Irreversible(
                f"rename op {op_id!r} has no before.display_name; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.folder.rename",
            drive_id=drive_id, item_id=start["item_id"],
            args={"new_name": prior},
            dry_run_result=f"(undo of {op_id}) rename back to {prior!r}",
        )

    if cmd == "mail-folder-move":
        prior_parent = before.get("parent_id")
        if not prior_parent:
            raise Irreversible(
                f"move op {op_id!r} has no before.parent_id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.folder.move",
            drive_id=drive_id, item_id=start["item_id"],
            args={"destination_id": prior_parent,
                  "destination_path": before.get("path", "")},
            dry_run_result=f"(undo of {op_id}) move back to "
                           f"{before.get('path', prior_parent)!r}",
        )

    if cmd == "mail-folder-delete":
        raise Irreversible(
            f"op {op_id!r} deleted a mail folder — restoring folders from "
            f"Deleted Items requires manual intervention in Phase 2. "
            f"Folder restore lands Phase 4+."
        )

    if cmd == "mail-categories-add":
        new_id = after.get("id")
        if not new_id:
            raise Irreversible(
                f"categories-add op {op_id!r} has no after.id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.categories.remove",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) remove category "
                           f"{after.get('display_name', new_id)!r}",
        )

    if cmd == "mail-categories-update":
        args: dict = {}
        if "display_name" in before:
            args["name"] = before["display_name"]
        if "color" in before:
            args["color"] = before["color"]
        if not args:
            raise Irreversible(
                f"categories-update op {op_id!r} has empty before; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.categories.update",
            drive_id=drive_id, item_id=start["item_id"],
            args=args,
            dry_run_result=f"(undo of {op_id}) update category back to "
                           f"{before.get('display_name', '?')!r}",
        )

    if cmd == "mail-categories-remove":
        name = before.get("display_name")
        if not name:
            raise Irreversible(
                f"categories-remove op {op_id!r} has no before.display_name; "
                f"cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.categories.add",
            drive_id=drive_id, item_id="",
            args={"name": name, "color": before.get("color", "preset0")},
            dry_run_result=f"(undo of {op_id}) re-add category {name!r} "
                           f"(message links cannot be restored)",
        )

    raise Irreversible(f"no reverse-op known for mail cmd {cmd!r}")


# ---- Dispatcher registration -----------------------------------------------

def _inverse_mail_folder_create(before: dict, after: dict) -> dict:
    return {"action": "mail.folder.delete", "args": {}}


def _inverse_mail_folder_rename(before: dict, after: dict) -> dict:
    return {"action": "mail.folder.rename",
            "args": {"new_name": before.get("display_name", "")}}


def _inverse_mail_folder_move(before: dict, after: dict) -> dict:
    return {"action": "mail.folder.move",
            "args": {"destination_id": before.get("parent_id", "")}}


def _inverse_mail_categories_add(before: dict, after: dict) -> dict:
    return {"action": "mail.categories.remove", "args": {}}


def _inverse_mail_categories_update(before: dict, after: dict) -> dict:
    args: dict = {}
    if "display_name" in before:
        args["name"] = before["display_name"]
    if "color" in before:
        args["color"] = before["color"]
    return {"action": "mail.categories.update", "args": args}


def _inverse_mail_categories_remove(before: dict, after: dict) -> dict:
    return {"action": "mail.categories.add",
            "args": {"name": before.get("display_name", ""),
                     "color": before.get("color", "preset0")}}


def register_mail_inverses(dispatcher: Dispatcher) -> None:
    """Register every Phase-2 mail inverse on ``dispatcher``."""
    dispatcher.register("mail.folder.create", _inverse_mail_folder_create)
    dispatcher.register("mail.folder.rename", _inverse_mail_folder_rename)
    dispatcher.register("mail.folder.move", _inverse_mail_folder_move)
    dispatcher.register("mail.categories.add", _inverse_mail_categories_add)
    dispatcher.register("mail.categories.update", _inverse_mail_categories_update)
    dispatcher.register("mail.categories.remove", _inverse_mail_categories_remove)
    dispatcher.register_irreversible(
        "mail.folder.delete",
        "Folder restore from Deleted Items requires manual intervention until Phase 4+.",
    )
