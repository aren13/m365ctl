"""Build reverse-ops from audit-log entries.

Reversible table:
- rename       -> rename back to ``before.name``
- move         -> move back (use ``before.parent_id`` if present, else
                  ``before.parent_path`` (best-effort))
- copy         -> delete the copy (use ``after.new_item_id`` as target)
- delete       -> restore from recycle bin
- label-apply  -> label-remove
- label-remove -> label-apply (if label recorded in ``before``)

Irreversible:
- recycle-purge (permanentDelete)
- any op whose original result != 'ok'
- share-revoke (stale-shares) — can't re-create a sharing link with the
  same id, so undo emits an Irreversible with manual-share instructions
"""
from __future__ import annotations

from m365ctl.common.audit import AuditLogger, find_op_by_id
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.common.undo import Dispatcher


class Irreversible(RuntimeError):
    """Raised when an op cannot be automatically reversed."""


def build_reverse_operation(logger: AuditLogger, op_id: str) -> Operation:
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
    item_id = start["item_id"]

    if cmd == "od-rename":
        return Operation(
            op_id=new_op_id(), action="od.rename",
            drive_id=drive_id, item_id=item_id,
            args={"new_name": before["name"]},
            dry_run_result=f"(undo of {op_id}) rename back to {before['name']!r}",
        )

    if cmd == "od-move":
        if "parent_id" not in before:
            raise Irreversible(
                f"move op {op_id!r} audit record has no parent_id in before; "
                f"cannot auto-reverse (catalog would need to re-resolve "
                f"{before.get('parent_path', '?')!r} to a drive-item id)."
            )
        return Operation(
            op_id=new_op_id(), action="od.move",
            drive_id=drive_id, item_id=item_id,
            args={"new_parent_item_id": before["parent_id"]},
            dry_run_result=f"(undo of {op_id}) move back to "
                           f"{before.get('parent_path', '?')}",
        )

    if cmd == "od-copy":
        new_item = after.get("new_item_id")
        if not new_item:
            raise Irreversible(
                f"copy op {op_id!r} has no recorded new_item_id — cannot undo"
            )
        return Operation(
            op_id=new_op_id(), action="od.delete",
            drive_id=after.get("target_drive_id", drive_id),
            item_id=new_item,
            args={},
            dry_run_result=f"(undo of {op_id}) delete copy {new_item!r}",
        )

    if cmd == "od-delete":
        # Thread the original delete's `before` block through args: at undo
        # time the item is in the recycle bin, so a live Graph lookup 404s
        # and leaves the executor with no name/parent_path to pass to the
        # PnP fallback. The audit record captured these at delete time.
        return Operation(
            op_id=new_op_id(), action="od.restore",
            drive_id=drive_id, item_id=item_id,
            args={
                "orig_name": before.get("name", ""),
                "orig_parent_path": before.get("parent_path", ""),
            },
            dry_run_result=f"(undo of {op_id}) restore {before.get('name','?')} "
                           f"from recycle bin",
        )

    if cmd == "od-clean(recycle-bin)":
        raise Irreversible(
            f"op {op_id!r} was a recycle-bin purge — items are permanently "
            f"deleted and not recoverable by this toolkit. If retention "
            f"backup is available, contact Microsoft 365 admin."
        )

    if cmd == "od-label(apply)":
        site_url = (start.get("args") or {}).get("site_url")
        if not site_url:
            raise Irreversible(
                f"label-apply op {op_id!r} has no site_url in audit args; "
                f"cannot build reverse-op"
            )
        return Operation(
            op_id=new_op_id(), action="od.label-remove",
            drive_id=drive_id, item_id=item_id,
            args={"site_url": site_url},
            dry_run_result=f"(undo of {op_id}) remove label "
                           f"{start['args'].get('label','?')!r}",
        )

    if cmd == "od-label(remove)":
        prior_label = before.get("label")
        if not prior_label:
            raise Irreversible(
                f"op {op_id!r} removed a label but prior label unknown"
            )
        site_url = (start.get("args") or {}).get("site_url")
        if not site_url:
            raise Irreversible(
                f"label-remove op {op_id!r} has no site_url in audit args; "
                f"cannot build reverse-op"
            )
        return Operation(
            op_id=new_op_id(), action="od.label-apply",
            drive_id=drive_id, item_id=item_id,
            args={"site_url": site_url, "label": prior_label},
            dry_run_result=f"(undo of {op_id}) re-apply {prior_label!r}",
        )

    if cmd == "od-clean(old-versions)":
        raise Irreversible(
            f"op {op_id!r} deleted file versions — version history cannot "
            f"be reconstructed. Original version content is gone."
        )

    if cmd == "od-clean(stale-shares)":
        raise Irreversible(
            f"op {op_id!r} revoked sharing link(s). Sharing links cannot be "
            f"reissued with the same URL. Re-share manually if needed."
        )

    raise Irreversible(f"no reverse-op known for cmd {cmd!r}")


# --- Dispatcher registration ---------------------------------------------
# Lightweight `(before, after) -> dict` inverse builders that the
# domain-agnostic `common.undo.Dispatcher` consumes. They produce a minimal
# `{"action": ..., "args": {...}}` spec describing the inverse op. The CLI
# still delegates actual executor routing through `build_reverse_operation`
# (which reads the full audit log record); the Dispatcher is used for
# preflight: action lookup, legacy normalization, and irreversible rejection.


def _inverse_rename(before: dict, after: dict) -> dict:
    return {"action": "od.rename",
            "args": {"new_name": before.get("name", "")}}


def _inverse_move(before: dict, after: dict) -> dict:
    return {"action": "od.move",
            "args": {"new_parent_item_id": before.get("parent_id", "")}}


def _inverse_delete(before: dict, after: dict) -> dict:
    return {"action": "od.restore",
            "args": {"orig_name": before.get("name", ""),
                     "orig_parent_path": before.get("parent_path", "")}}


def _inverse_restore(before: dict, after: dict) -> dict:
    return {"action": "od.delete", "args": {}}


def _inverse_label_apply(before: dict, after: dict) -> dict:
    return {"action": "od.label-remove", "args": {}}


def _inverse_label_remove(before: dict, after: dict) -> dict:
    return {"action": "od.label-apply",
            "args": {"label": before.get("label", "")}}


def register_od_inverses(dispatcher: Dispatcher) -> None:
    """Register every OneDrive inverse builder on the supplied dispatcher."""
    dispatcher.register("od.rename", _inverse_rename)
    dispatcher.register("od.move", _inverse_move)
    dispatcher.register("od.delete", _inverse_delete)
    dispatcher.register("od.restore", _inverse_restore)
    dispatcher.register("od.label-apply", _inverse_label_apply)
    dispatcher.register("od.label-remove", _inverse_label_remove)
    # Irreversible OneDrive verbs.
    dispatcher.register_irreversible(
        "od.copy",
        "Copy target lives only as a new item; delete the copy to 'undo'.",
    )
    dispatcher.register_irreversible(
        "od.download",
        "Downloads are local-file artifacts; delete the file to 'undo'.",
    )
    dispatcher.register_irreversible(
        "od.version-delete",
        "Deleted file versions cannot be restored via Graph.",
    )
    dispatcher.register_irreversible(
        "od.share-revoke",
        "Revoked sharing links cannot be restored; re-share explicitly.",
    )
    dispatcher.register_irreversible(
        "od.recycle-purge",
        "Purged recycle-bin items are irrecoverable.",
    )
