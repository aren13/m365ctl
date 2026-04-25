from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365ctl.common.graph import GraphError
from m365ctl.common.planfile import Plan, Operation, PLAN_SCHEMA_VERSION
from m365ctl.mail.catalog.db import open_catalog
from m365ctl.mail.triage.runner import (
    RunnerError, make_header_fetcher, run_emit, run_execute, run_validate,
)


def _seed_messages(catalog_path: Path, rows: list[dict]) -> None:
    with open_catalog(catalog_path) as conn:
        for r in rows:
            base = {
                "mailbox_upn": "me",
                "message_id": r["message_id"],
                "internet_message_id": None,
                "conversation_id": None,
                "parent_folder_id": "fld-inbox",
                "parent_folder_path": r.get("parent_folder_path", "Inbox"),
                "subject": r.get("subject", ""),
                "from_address": r.get("from_address", "x@example.com"),
                "from_name": "X",
                "to_addresses": "",
                "cc_addresses": "",
                "received_at": r.get("received_at"),
                "sent_at": None,
                "is_read": r.get("is_read", False),
                "is_draft": False,
                "has_attachments": r.get("has_attachments", False),
                "importance": r.get("importance", "normal"),
                "flag_status": r.get("flag_status", "notFlagged"),
                "categories": r.get("categories", ""),
                "inference_class": r.get("inference_class", "focused"),
                "body_preview": "",
                "web_link": "",
                "size_estimate": 0,
                "is_deleted": False,
                "last_seen_at": "2026-04-25",
            }
            conn.execute(
                "INSERT INTO mail_messages VALUES ($mailbox_upn, $message_id, "
                "$internet_message_id, $conversation_id, $parent_folder_id, "
                "$parent_folder_path, $subject, $from_address, $from_name, "
                "$to_addresses, $cc_addresses, $received_at, $sent_at, $is_read, $is_draft, "
                "$has_attachments, $importance, $flag_status, $categories, "
                "$inference_class, $body_preview, $web_link, $size_estimate, "
                "$is_deleted, $last_seen_at)",
                base,
            )


def test_run_validate_ok(tmp_path: Path) -> None:
    p = tmp_path / "rules.yaml"
    p.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    # No exception -> ok
    run_validate(p)


def test_run_validate_raises_on_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "rules.yaml"
    p.write_text("""
version: 1
mailbox: me
rules:
  - name: bad
    match: { unread: not-a-bool }
    actions: [{ read: true }]
""")
    with pytest.raises(RunnerError):
        run_validate(p)


def test_run_emit_writes_plan(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: archive
    match:
      all:
        - from: { domain_in: [example.com] }
        - folder: Inbox
    actions:
      - move: { to_folder: Archive }
""")
    catalog = tmp_path / "mail.duckdb"
    _seed_messages(catalog, [
        {"message_id": "m1", "from_address": "a@example.com"},
        {"message_id": "m2", "from_address": "b@other.com"},
    ])
    plan_out = tmp_path / "plan.json"
    plan = run_emit(
        rules_path=rules,
        catalog_path=catalog,
        mailbox_upn="me",
        scope="me",
        plan_out=plan_out,
    )
    assert plan_out.exists()
    assert len(plan.operations) == 1
    assert plan.operations[0].action == "mail.move"
    assert plan.operations[0].args["rule_name"] == "archive"
    assert plan.operations[0].item_id == "m1"


def test_run_execute_dispatches_per_action(tmp_path: Path) -> None:
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-04-25T00:00:00",
        source_cmd="x",
        scope="me",
        operations=[
            Operation(op_id="op-1", action="mail.read",
                      drive_id="me", item_id="m1",
                      args={"rule_name": "r", "is_read": True},
                      dry_run_result=""),
            Operation(op_id="op-2", action="mail.flag",
                      drive_id="me", item_id="m1",
                      args={"rule_name": "r", "status": "flagged"},
                      dry_run_result=""),
        ],
    )
    fake_read = MagicMock(return_value=MagicMock(status="ok", error=None))
    fake_flag = MagicMock(return_value=MagicMock(status="ok", error=None))
    with patch.dict(
        "m365ctl.mail.triage.runner._EXECUTORS",
        {"mail.read": fake_read, "mail.flag": fake_flag},
        clear=False,
    ):
        results = run_execute(
            plan,
            cfg=MagicMock(),
            mailbox_spec="me",
            auth_mode="delegated",
            graph=MagicMock(),
            logger=MagicMock(),
        )
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)
    fake_read.assert_called_once()
    fake_flag.assert_called_once()


def test_make_header_fetcher_calls_graph_with_select() -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe", "value": "<https://x.test/u>"},
        ],
    }
    fetcher = make_header_fetcher(graph, mailbox_spec="me", auth_mode="delegated")
    headers = fetcher("msg-1")
    assert headers == [{"name": "List-Unsubscribe", "value": "<https://x.test/u>"}]
    args, kwargs = graph.get.call_args
    # Path includes /messages/<id>; params include the $select.
    assert "/messages/msg-1" in args[0]
    assert kwargs.get("params") == {"$select": "internetMessageHeaders"}


def test_make_header_fetcher_returns_empty_on_grapherror() -> None:
    graph = MagicMock()
    graph.get.side_effect = GraphError("boom")
    fetcher = make_header_fetcher(graph, mailbox_spec="me", auth_mode="delegated")
    assert fetcher("missing") == []


def test_run_emit_threads_header_fetcher_to_build_plan(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    catalog = tmp_path / "mail.duckdb"
    _seed_messages(catalog, [{"message_id": "m1"}])
    plan_out = tmp_path / "plan.json"
    sentinel = MagicMock(name="fetcher")
    fake_plan = MagicMock(operations=[])
    with patch(
        "m365ctl.mail.triage.runner.build_plan",
        return_value=fake_plan,
    ) as mock_build, patch(
        "m365ctl.mail.triage.runner.write_plan",
    ):
        run_emit(
            rules_path=rules,
            catalog_path=catalog,
            mailbox_upn="me",
            scope="me",
            plan_out=plan_out,
            header_fetcher=sentinel,
        )
    _, kwargs = mock_build.call_args
    assert kwargs.get("header_fetcher") is sentinel


def test_run_emit_with_headers_predicate_invokes_fetcher(tmp_path: Path) -> None:
    rules = tmp_path / "rules.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: kill-newsletters
    match:
      all:
        - folder: Inbox
        - headers: { name: List-Unsubscribe }
    actions:
      - move: { to_folder: Archive }
""")
    catalog = tmp_path / "mail.duckdb"
    _seed_messages(catalog, [
        {"message_id": "m1", "parent_folder_path": "Inbox"},
    ])
    plan_out = tmp_path / "plan.json"
    fetched: list[str] = []

    def fetcher(msg_id: str) -> list[dict[str, str]]:
        fetched.append(msg_id)
        return [{"name": "List-Unsubscribe", "value": "<https://x.test/u>"}]

    plan = run_emit(
        rules_path=rules,
        catalog_path=catalog,
        mailbox_upn="me",
        scope="me",
        plan_out=plan_out,
        header_fetcher=fetcher,
    )
    assert fetched == ["m1"]
    assert len(plan.operations) == 1
    assert plan.operations[0].action == "mail.move"


def test_run_execute_continues_on_per_op_error() -> None:
    plan = Plan(
        version=PLAN_SCHEMA_VERSION,
        created_at="2026-04-25T00:00:00",
        source_cmd="x",
        scope="me",
        operations=[
            Operation(op_id="op-1", action="mail.read",
                      drive_id="me", item_id="m1",
                      args={"rule_name": "r", "is_read": True},
                      dry_run_result=""),
            Operation(op_id="op-2", action="mail.read",
                      drive_id="me", item_id="m2",
                      args={"rule_name": "r", "is_read": True},
                      dry_run_result=""),
        ],
    )
    fake_read = MagicMock(side_effect=[
        MagicMock(status="error", error="404"),
        MagicMock(status="ok", error=None),
    ])
    with patch.dict(
        "m365ctl.mail.triage.runner._EXECUTORS",
        {"mail.read": fake_read},
        clear=False,
    ):
        results = run_execute(
            plan, cfg=MagicMock(), mailbox_spec="me",
            auth_mode="delegated", graph=MagicMock(), logger=MagicMock(),
        )
    assert [r.status for r in results] == ["error", "ok"]
