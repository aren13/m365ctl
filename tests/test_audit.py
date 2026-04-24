from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from m365ctl.common.audit import (
    AuditLogger,
    find_most_recent_delete_before,
    find_op_by_id,
    iter_audit_entries,
    log_mutation_end,
    log_mutation_start,
)


def _logger(tmp_path: Path) -> AuditLogger:
    return AuditLogger(ops_dir=tmp_path / "logs" / "ops")


def test_log_start_creates_jsonl_file(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(
        logger,
        op_id="op-1",
        cmd="od-rename",
        args={"new_name": "new.txt"},
        drive_id="d1",
        item_id="i1",
        before={"parent_path": "/", "name": "old.txt"},
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = tmp_path / "logs" / "ops" / f"{day}.jsonl"
    assert f.exists()
    lines = f.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["op_id"] == "op-1"
    assert rec["cmd"] == "od-rename"
    assert rec["phase"] == "start"
    assert rec["before"] == {"parent_path": "/", "name": "old.txt"}


def test_log_start_then_end_writes_two_lines(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(
        logger, op_id="op-2", cmd="od-move", args={},
        drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "foo.txt"},
    )
    log_mutation_end(
        logger, op_id="op-2",
        after={"parent_path": "/B", "name": "foo.txt"},
        result="ok",
    )
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    recs = [
        json.loads(l)
        for l in (tmp_path / "logs" / "ops" / f"{day}.jsonl")
        .read_text()
        .strip()
        .splitlines()
    ]
    assert [r["phase"] for r in recs] == ["start", "end"]
    assert recs[1]["after"] == {"parent_path": "/B", "name": "foo.txt"}
    assert recs[1]["result"] == "ok"


def test_log_end_with_error(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(logger, op_id="op-3", cmd="od-move", args={},
                       drive_id="d", item_id="i",
                       before={"parent_path": "/", "name": "x"})
    log_mutation_end(logger, op_id="op-3", after=None, result="error",
                     error="HTTP403: forbidden")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = (tmp_path / "logs" / "ops" / f"{day}.jsonl").read_text().splitlines()
    rec = json.loads(lines[-1])
    assert rec["result"] == "error"
    assert rec["error"] == "HTTP403: forbidden"


def test_iter_audit_entries_reads_all_days(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    logger.ops_dir.mkdir(parents=True, exist_ok=True)
    (logger.ops_dir / "2026-04-23.jsonl").write_text(
        json.dumps({"op_id": "a", "phase": "start", "cmd": "od-move"}) + "\n"
        + json.dumps({"op_id": "a", "phase": "end", "result": "ok"}) + "\n"
    )
    (logger.ops_dir / "2026-04-24.jsonl").write_text(
        json.dumps({"op_id": "b", "phase": "start", "cmd": "od-rename"}) + "\n"
    )
    entries = list(iter_audit_entries(logger))
    op_ids = {e["op_id"] for e in entries}
    assert op_ids == {"a", "b"}


def test_find_op_by_id_returns_paired_records(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(logger, op_id="X", cmd="od-rename", args={"new_name": "n"},
                       drive_id="d", item_id="i",
                       before={"parent_path": "/", "name": "o.txt"})
    log_mutation_end(logger, op_id="X",
                     after={"parent_path": "/", "name": "n"}, result="ok")
    start, end = find_op_by_id(logger, "X")
    assert start["phase"] == "start"
    assert end["phase"] == "end"
    assert end["result"] == "ok"


def test_find_op_by_id_missing_returns_none(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    assert find_op_by_id(logger, "nope") == (None, None)


def test_find_most_recent_delete_before_single_match(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(
        logger, op_id="D1", cmd="od-delete", args={},
        drive_id="d", item_id="i",
        before={"parent_path": "/F", "name": "hello.txt"},
    )
    log_mutation_end(logger, op_id="D1",
                     after={"parent_path": "(recycle bin)", "name": "hello.txt"},
                     result="ok")
    got = find_most_recent_delete_before(logger, drive_id="d", item_id="i")
    assert got == {"parent_path": "/F", "name": "hello.txt"}


def test_find_most_recent_delete_before_returns_most_recent(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    log_mutation_start(
        logger, op_id="D1", cmd="od-delete", args={},
        drive_id="d", item_id="i",
        before={"parent_path": "/F", "name": "old.txt"},
    )
    log_mutation_start(
        logger, op_id="D2", cmd="od-delete", args={},
        drive_id="d", item_id="i",
        before={"parent_path": "/G", "name": "new.txt"},
    )
    got = find_most_recent_delete_before(logger, drive_id="d", item_id="i")
    assert got == {"parent_path": "/G", "name": "new.txt"}


def test_find_most_recent_delete_before_no_match(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    got = find_most_recent_delete_before(logger, drive_id="d", item_id="i")
    assert got is None


def test_find_most_recent_delete_before_wrong_cmd_ignored(tmp_path: Path) -> None:
    """od-move with matching (drive,item) must NOT be returned."""
    logger = _logger(tmp_path)
    log_mutation_start(
        logger, op_id="M1", cmd="od-move", args={},
        drive_id="d", item_id="i",
        before={"parent_path": "/A", "name": "x.txt"},
    )
    got = find_most_recent_delete_before(logger, drive_id="d", item_id="i")
    assert got is None


def test_audit_log_append_only(tmp_path: Path) -> None:
    """Writing to the same day re-appends; never truncates."""
    logger = _logger(tmp_path)
    for i in range(5):
        log_mutation_start(logger, op_id=f"op-{i}", cmd="od-move", args={},
                           drive_id="d", item_id=f"i{i}",
                           before={"parent_path": "/", "name": "x"})
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = (tmp_path / "logs" / "ops" / f"{day}.jsonl").read_text().splitlines()
    assert len(lines) == 5
