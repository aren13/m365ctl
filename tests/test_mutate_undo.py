from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fazla_od.audit import AuditLogger, log_mutation_end, log_mutation_start
from fazla_od.mutate.undo import Irreversible, build_reverse_operation


def _ap(logger: AuditLogger, op_id: str, cmd: str, args: dict,
        drive_id: str, item_id: str,
        before: dict, after: dict | None, result: str,
        error: str | None = None) -> None:
    log_mutation_start(logger, op_id=op_id, cmd=cmd, args=args,
                       drive_id=drive_id, item_id=item_id, before=before)
    log_mutation_end(logger, op_id=op_id, after=after, result=result,
                     error=error)


def test_reverse_rename_restores_original_name(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="R1", cmd="od-rename",
        args={"new_name": "new.txt"}, drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "old.txt"},
        after={"parent_path": "/", "name": "new.txt"}, result="ok")
    rev = build_reverse_operation(logger, "R1")
    assert rev.action == "rename"
    assert rev.args == {"new_name": "old.txt"}
    assert rev.item_id == "i"


def test_reverse_move_moves_back(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="M1", cmd="od-move",
        args={"new_parent_item_id": "B"}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x", "parent_id": "A"},
        after={"parent_path": "/B", "name": "x", "parent_id": "B"},
        result="ok")
    rev = build_reverse_operation(logger, "M1")
    assert rev.action == "move"
    assert "new_parent_item_id" in rev.args or "new_parent_path" in rev.args


def test_reverse_copy_deletes_the_copy(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="C1", cmd="od-copy",
        args={"target_drive_id": "d2", "target_parent_item_id": "P",
              "new_name": "dup.txt"},
        drive_id="d1", item_id="i1",
        before={"parent_path": "/", "name": "x.txt"},
        after={"new_item_id": "NEW", "target_drive_id": "d2",
               "target_parent_item_id": "P", "new_name": "dup.txt"},
        result="ok")
    rev = build_reverse_operation(logger, "C1")
    assert rev.action == "delete"
    assert rev.drive_id == "d2"
    assert rev.item_id == "NEW"


def test_reverse_recycle_delete_is_restore(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="D1", cmd="od-delete",
        args={}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x"},
        after={"parent_path": "(recycle bin)", "name": "x",
               "recycled_from": "/A"}, result="ok")
    rev = build_reverse_operation(logger, "D1")
    assert rev.action == "restore"
    assert rev.drive_id == "d"
    assert rev.item_id == "i"


def test_delete_reverse_op_packs_original_before_into_args(tmp_path):
    """Audit-time `before` is threaded through rev.args so the undo
    executor can feed name/parent_path to the PnP fallback even though
    the item is in the recycle bin and no live lookup can recover it."""
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="D2", cmd="od-delete",
        args={}, drive_id="d", item_id="i",
        before={"parent_path": "/drives/d/root:/F", "name": "x.txt"},
        after={"parent_path": "(recycle bin)", "name": "x.txt",
               "recycled_from": "/drives/d/root:/F"}, result="ok")
    rev = build_reverse_operation(logger, "D2")
    assert rev.args["orig_name"] == "x.txt"
    assert rev.args["orig_parent_path"] == "/drives/d/root:/F"


def test_reverse_recycle_purge_is_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="P1", cmd="od-clean(recycle-bin)",
        args={}, drive_id="d", item_id="i",
        before={"parent_path": "(recycle bin)", "name": "x"},
        after={"parent_path": "(permanently deleted)", "name": "x",
               "irreversible": True}, result="ok")
    with pytest.raises(Irreversible, match="permanently"):
        build_reverse_operation(logger, "P1")


def test_reverse_label_apply_is_remove(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="L1", cmd="od-label(apply)",
        args={"label": "Confidential",
              "site_url": "https://fazla.sharepoint.com/"},
        drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "x",
                "server_relative_url": "/Documents/x"},
        after={"parent_path": "/", "name": "x", "label": "Confidential"},
        result="ok")
    rev = build_reverse_operation(logger, "L1")
    assert rev.action == "label-remove"
    assert rev.args["site_url"] == "https://fazla.sharepoint.com/"


def test_reverse_op_failed_originally_raises(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="F1", cmd="od-move",
        args={"new_parent_item_id": "B"}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x"},
        after=None, result="error", error="accessDenied: nope")
    with pytest.raises(Irreversible, match="did not succeed"):
        build_reverse_operation(logger, "F1")


def test_reverse_unknown_op_id_raises(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    with pytest.raises(Irreversible, match="not found"):
        build_reverse_operation(logger, "nonexistent")


def test_reverse_move_without_parent_id_is_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="M2", cmd="od-move",
        args={"new_parent_item_id": "B"}, drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x"},  # NO parent_id
        after={"parent_path": "/B", "name": "x"}, result="ok")
    with pytest.raises(Irreversible, match="parent_id"):
        build_reverse_operation(logger, "M2")


def test_reverse_label_remove_is_apply_of_prior_label(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="L2", cmd="od-label(remove)",
        args={"site_url": "https://fazla.sharepoint.com/"},
        drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "x",
                "server_relative_url": "/Documents/x",
                "label": "Internal"},
        after={"parent_path": "/", "name": "x", "label": None},
        result="ok")
    rev = build_reverse_operation(logger, "L2")
    assert rev.action == "label-apply"
    assert rev.args["label"] == "Internal"
    assert rev.args["site_url"] == "https://fazla.sharepoint.com/"


def test_reverse_label_remove_without_prior_label_is_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="L3", cmd="od-label(remove)",
        args={"site_url": "https://fazla.sharepoint.com/"},
        drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "x",
                "server_relative_url": "/Documents/x"},  # no 'label' key
        after={"parent_path": "/", "name": "x", "label": None},
        result="ok")
    with pytest.raises(Irreversible, match="prior label unknown"):
        build_reverse_operation(logger, "L3")


def test_reverse_old_versions_clean_is_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="V1", cmd="od-clean(old-versions)",
        args={"keep": 3}, drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "x"},
        after={"versions_deleted": ["v1", "v2"]}, result="ok")
    with pytest.raises(Irreversible, match="version"):
        build_reverse_operation(logger, "V1")


def test_reverse_stale_shares_clean_is_irreversible(tmp_path):
    logger = AuditLogger(ops_dir=tmp_path / "logs/ops")
    _ap(logger, op_id="S1", cmd="od-clean(stale-shares)",
        args={"older_than_days": 90}, drive_id="d", item_id="i",
        before={"parent_path": "/", "name": "x"},
        after={"permissions_revoked": ["p1"]}, result="ok")
    with pytest.raises(Irreversible, match="sharing link"):
        build_reverse_operation(logger, "S1")
