from __future__ import annotations

from pathlib import Path  # noqa: F401  # re-exported for downstream tests

import pytest

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.settings import execute_set_signature
from m365ctl.mail.signature import (
    SignatureNotConfigured,
    SignatureReadError,  # noqa: F401  # surface check: must be importable
    get_signature,
    set_signature,
)


def test_get_signature_reads_text_file(tmp_path):
    p = tmp_path / "sig.txt"
    p.write_text("Best,\nA")
    sig = get_signature(p)
    assert sig.content_type == "text"
    assert sig.content == "Best,\nA"


def test_get_signature_reads_html_file(tmp_path):
    p = tmp_path / "sig.html"
    p.write_text("<p>Best,</p>")
    sig = get_signature(p)
    assert sig.content_type == "html"
    assert sig.content == "<p>Best,</p>"


def test_get_signature_missing_returns_empty(tmp_path):
    sig = get_signature(tmp_path / "absent.txt")
    assert sig.content == ""
    assert sig.content_type == "text"


def test_get_signature_none_path_raises():
    with pytest.raises(SignatureNotConfigured):
        get_signature(None)


def test_set_signature_writes_file(tmp_path):
    p = tmp_path / "sig.html"
    set_signature(p, content="<p>X</p>")
    assert p.read_text() == "<p>X</p>"


def test_set_signature_creates_parent_dirs(tmp_path):
    p = tmp_path / "deep" / "nested" / "sig.txt"
    set_signature(p, content="X")
    assert p.read_text() == "X"


def test_set_signature_none_path_raises():
    with pytest.raises(SignatureNotConfigured):
        set_signature(None, content="x")


def test_executor_writes_signature(tmp_path):
    p = tmp_path / "sig.txt"
    p.write_text("old")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id=new_op_id(),
        action="mail.settings.signature",
        drive_id="me",
        item_id="",
        args={"signature_path": str(p), "content": "new"},
        dry_run_result="",
    )
    r = execute_set_signature(op, logger=logger, before={"content": "old"})
    assert r.status == "ok"
    assert p.read_text() == "new"


def test_executor_records_old_content_in_before(tmp_path):
    """Caller responsibility: pass before with prior content; executor uses it for audit."""
    p = tmp_path / "sig.txt"
    p.write_text("v1")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = Operation(
        op_id=new_op_id(), action="mail.settings.signature",
        drive_id="me", item_id="", args={"signature_path": str(p), "content": "v2"},
        dry_run_result="",
    )
    execute_set_signature(op, logger=logger, before={"content": "v1"})
    # Audit log file contains a 'before' record with content="v1".
    log_files = list((tmp_path / "ops").glob("*.jsonl"))
    assert log_files, "audit log should be written"
    assert "v1" in log_files[0].read_text()
