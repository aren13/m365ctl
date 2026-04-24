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

    if cmd == "mail-move":
        prior_parent = before.get("parent_folder_id")
        if not prior_parent:
            raise Irreversible(
                f"mail-move op {op_id!r} has no before.parent_folder_id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.move",
            drive_id=drive_id, item_id=start["item_id"],
            args={"destination_id": prior_parent,
                  "destination_path": before.get("parent_folder_path", "")},
            dry_run_result=f"(undo of {op_id}) move back to "
                           f"{before.get('parent_folder_path', prior_parent)!r}",
        )

    if cmd == "mail-copy":
        new_id = after.get("new_message_id")
        if not new_id:
            raise Irreversible(
                f"mail-copy op {op_id!r} has no after.new_message_id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.delete.soft",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) soft-delete the copy {new_id!r}",
        )

    if cmd == "mail-flag":
        return Operation(
            op_id=new_op_id(), action="mail.flag",
            drive_id=drive_id, item_id=start["item_id"],
            args={"status": before.get("status", "notFlagged"),
                  "start_at": before.get("start_at"),
                  "due_at": before.get("due_at")},
            dry_run_result=f"(undo of {op_id}) restore flag "
                           f"{before.get('status', 'notFlagged')!r}",
        )

    if cmd == "mail-read":
        return Operation(
            op_id=new_op_id(), action="mail.read",
            drive_id=drive_id, item_id=start["item_id"],
            args={"is_read": bool(before.get("is_read", False))},
            dry_run_result=f"(undo of {op_id}) set is_read back to "
                           f"{before.get('is_read', False)}",
        )

    if cmd == "mail-focus":
        return Operation(
            op_id=new_op_id(), action="mail.focus",
            drive_id=drive_id, item_id=start["item_id"],
            args={"inference_classification":
                  before.get("inference_classification", "focused")},
            dry_run_result=f"(undo of {op_id}) restore focus "
                           f"{before.get('inference_classification', '?')!r}",
        )

    if cmd == "mail-categorize":
        return Operation(
            op_id=new_op_id(), action="mail.categorize",
            drive_id=drive_id, item_id=start["item_id"],
            args={"categories": list(before.get("categories", []))},
            dry_run_result=f"(undo of {op_id}) restore categories "
                           f"{before.get('categories', [])}",
        )

    if cmd == "mail-delete-soft":
        prior_parent = before.get("parent_folder_id")
        if not prior_parent:
            raise Irreversible(
                f"mail-delete-soft op {op_id!r} has no before.parent_folder_id; "
                f"cannot determine where to restore to. "
                f"(If the message was already in Deleted Items when deleted, "
                f"the original folder is unrecoverable.)"
            )
        return Operation(
            op_id=new_op_id(), action="mail.move",
            drive_id=drive_id, item_id=start["item_id"],
            args={"destination_id": prior_parent,
                  "destination_path": before.get("parent_folder_path", "")},
            dry_run_result=f"(undo of {op_id}) restore {start['item_id']!r} "
                           f"to {before.get('parent_folder_path', prior_parent)!r}",
        )

    if cmd == "mail-draft-create":
        new_id = after.get("id")
        if not new_id:
            raise Irreversible(
                f"mail-draft-create op {op_id!r} has no after.id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.draft.delete",
            drive_id=drive_id, item_id=new_id, args={},
            dry_run_result=f"(undo of {op_id}) delete draft {new_id!r}",
        )

    if cmd == "mail-draft-update":
        if not before:
            raise Irreversible(
                f"mail-draft-update op {op_id!r} has empty before; cannot undo"
            )
        restore_args: dict = {}
        if "subject" in before:
            restore_args["subject"] = before["subject"]
        if "body" in before and isinstance(before["body"], dict):
            restore_args["body"] = before["body"].get("content", "")
            restore_args["body_type"] = before["body"].get("contentType", "text")
        if "toRecipients" in before:
            restore_args["to"] = [r.get("emailAddress", {}).get("address", "")
                                  for r in before["toRecipients"]]
        if "ccRecipients" in before:
            restore_args["cc"] = [r.get("emailAddress", {}).get("address", "")
                                  for r in before["ccRecipients"]]
        return Operation(
            op_id=new_op_id(), action="mail.draft.update",
            drive_id=drive_id, item_id=start["item_id"],
            args=restore_args,
            dry_run_result=f"(undo of {op_id}) restore draft {start['item_id']!r}",
        )

    if cmd == "mail-draft-delete":
        if not before or "subject" not in before:
            raise Irreversible(
                f"mail-draft-delete op {op_id!r} has no before.subject; "
                f"cannot reconstruct the deleted draft"
            )
        body_block = before.get("body", {}) or {}
        create_args: dict = {
            "subject": before.get("subject", ""),
            "body": body_block.get("content", ""),
            "body_type": body_block.get("contentType", "text"),
            "to": [r.get("emailAddress", {}).get("address", "")
                   for r in before.get("toRecipients", []) or []],
        }
        if before.get("ccRecipients"):
            create_args["cc"] = [r.get("emailAddress", {}).get("address", "")
                                 for r in before["ccRecipients"]]
        if before.get("bccRecipients"):
            create_args["bcc"] = [r.get("emailAddress", {}).get("address", "")
                                  for r in before["bccRecipients"]]
        return Operation(
            op_id=new_op_id(), action="mail.draft.create",
            drive_id=drive_id, item_id="", args=create_args,
            dry_run_result=f"(undo of {op_id}) recreate draft "
                           f"{before.get('subject', '?')!r}",
        )

    if cmd == "mail-attach-add":
        new_att = after.get("id")
        if not new_att:
            raise Irreversible(
                f"mail-attach-add op {op_id!r} has no after.id; cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="mail.attach.remove",
            drive_id=drive_id, item_id=start["item_id"],
            args={"attachment_id": new_att},
            dry_run_result=f"(undo of {op_id}) remove attachment {new_att!r}",
        )

    if cmd == "mail-attach-remove":
        if not before.get("content_bytes_b64"):
            raise Irreversible(
                f"mail-attach-remove op {op_id!r} has no before.content_bytes_b64; "
                f"cannot recreate the attachment"
            )
        return Operation(
            op_id=new_op_id(), action="mail.attach.add",
            drive_id=drive_id, item_id=start["item_id"],
            args={
                "name": before.get("name", ""),
                "content_type": before.get("content_type", "application/octet-stream"),
                "content_bytes_b64": before["content_bytes_b64"],
            },
            dry_run_result=f"(undo of {op_id}) re-add attachment "
                           f"{before.get('name', '?')!r}",
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
    dispatcher.register("mail.move", lambda b, a: {
        "action": "mail.move",
        "args": {"destination_id": b.get("parent_folder_id", "")},
    })
    dispatcher.register("mail.copy", lambda b, a: {
        "action": "mail.delete.soft", "args": {},
    })
    dispatcher.register("mail.flag", lambda b, a: {
        "action": "mail.flag",
        "args": {"status": b.get("status", "notFlagged"),
                 "start_at": b.get("start_at"),
                 "due_at": b.get("due_at")},
    })
    dispatcher.register("mail.read", lambda b, a: {
        "action": "mail.read",
        "args": {"is_read": bool(b.get("is_read", False))},
    })
    dispatcher.register("mail.focus", lambda b, a: {
        "action": "mail.focus",
        "args": {"inference_classification": b.get("inference_classification", "focused")},
    })
    dispatcher.register("mail.categorize", lambda b, a: {
        "action": "mail.categorize",
        "args": {"categories": list(b.get("categories", []))},
    })
    dispatcher.register("mail.delete.soft", lambda b, a: {
        "action": "mail.move",
        "args": {"destination_id": b.get("parent_folder_id", ""),
                 "destination_path": b.get("parent_folder_path", "")},
    })

    # Phase 5a — reversible compose verbs.
    dispatcher.register("mail.draft.create", lambda b, a: {
        "action": "mail.draft.delete", "args": {},
    })
    dispatcher.register("mail.draft.update", lambda b, a: {
        "action": "mail.draft.update",
        "args": {
            "subject": b.get("subject", ""),
            "body": (b.get("body", {}) or {}).get("content", ""),
            "body_type": (b.get("body", {}) or {}).get("contentType", "text"),
        },
    })
    dispatcher.register("mail.draft.delete", lambda b, a: {
        "action": "mail.draft.create",
        "args": {
            "subject": b.get("subject", ""),
            "body": (b.get("body", {}) or {}).get("content", ""),
            "body_type": (b.get("body", {}) or {}).get("contentType", "text"),
            "to": [r.get("emailAddress", {}).get("address", "")
                   for r in b.get("toRecipients", []) or []],
        },
    })
    dispatcher.register("mail.attach.add", lambda b, a: {
        "action": "mail.attach.remove",
        "args": {"attachment_id": a.get("id", "")},
    })
    dispatcher.register("mail.attach.remove", lambda b, a: {
        "action": "mail.attach.add",
        "args": {
            "name": b.get("name", ""),
            "content_type": b.get("content_type", "application/octet-stream"),
            "content_bytes_b64": b.get("content_bytes_b64", ""),
        },
    })

    # Phase 5a — irreversible compose verbs (outgoing mail cannot be recalled).
    dispatcher.register_irreversible(
        "mail.send",
        "Sent mail cannot be recalled programmatically. "
        "If the recipient hasn't opened the message yet, use the Outlook client's "
        "'Recall this message' feature.",
    )
    dispatcher.register_irreversible(
        "mail.reply",
        "Sent replies cannot be recalled programmatically.",
    )
    dispatcher.register_irreversible(
        "mail.reply.all",
        "Sent reply-all messages cannot be recalled programmatically.",
    )
    dispatcher.register_irreversible(
        "mail.forward",
        "Sent forwards cannot be recalled programmatically.",
    )
