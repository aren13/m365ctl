# Phase 8 — Server-Side Inbox Rules CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan group-by-group. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Round-trippable YAML ↔ Graph `messageRule` conversion + audit-logged CRUD over `/me/mailFolders/inbox/messageRules`. Export → Import → identical set is the headline test.

**Architecture:**
- `m365ctl.mail.rules` — existing read-only `list_rules` / `get_rule` extended with `rule_to_yaml(rule, *, folder_id_to_path)` and `rule_from_yaml(doc, *, folder_path_to_id)` translators. Folder-id ↔ path conversion routed through Phase 2's `resolve_folder_path` and a reverse helper.
- `m365ctl.mail.mutate.rules` — `execute_create`, `execute_update`, `execute_delete`, `execute_set_enabled`, `execute_reorder`. Each writes a structured audit record. Inverses registered via `undo` machinery so `m365ctl undo <op-id>` works for rule ops.
- CLI: extend `m365ctl mail rules` (already has `list`/`show`) with `create`, `update`, `delete`, `enable`, `disable`, `reorder`, `export`, `import` subcommands. `--from-plan` / `--plan-out` for batch ops.
- bin/mail-rules wrapper already exists.
- Tests: round-trip equivalence (export YAML → re-import → fetch back → equal modulo server-assigned id/sequence echo). Include conditions/actions/exceptions coverage.

**Tech stack:** Existing PyYAML (added in Phase 10), existing Graph endpoints, existing audit/undo plumbing. No new deps.

**Baseline:** `main` post-PR-#13 (6e0018b), 637 passing tests, 0 mypy errors. Tag `v0.8.0` shipped.

**Version bump:** 0.8.0 → 0.9.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/mutate/rules.py` — `RuleMutationResult`, executors for create/update/delete/enable/disable/reorder, action constants, audit log shape.
- `tests/test_mail_rules_yaml.py` — round-trip translator tests (the rich one).
- `tests/test_mail_mutate_rules.py` — mock-Graph executor tests.
- `tests/test_cli_mail_rules_crud.py` — CLI tests for the new subcommands (smoke covers list/show already).
- `scripts/mail/rules-server-side/example-rule.yaml` — generic example.
- `scripts/mail/rules-server-side/round-trip-fixture.yaml` — used by integration test.

**Modify:**
- `src/m365ctl/mail/rules.py` — add `rule_to_yaml`, `rule_from_yaml`, `_resolve_folder_paths`, `_build_path_to_id_map` helpers + reuse.
- `src/m365ctl/mail/cli/rules.py` — extend with the seven new subcommands.
- `src/m365ctl/mail/mutate/undo.py` — register inverses for `mail.rule.*` ops in `build_reverse_mail_operation`.
- `pyproject.toml` — bump 0.8.0 → 0.9.0.
- `CHANGELOG.md` — 0.9.0 section.
- `README.md` — Mail bullet.

**No changes to:** dispatcher (`mail/cli/__main__.py` already routes `rules`). bin/mail-rules wrapper already exists.

---

## YAML schema (for translator design)

**Spec §15 reference shape:**
```yaml
display_name: auto-archive-newsletters
sequence: 10
enabled: true
conditions:
  from_addresses:
    - {name: "Example Newsletter", address: "noreply@example-newsletter.com"}
  subject_contains: ["weekly digest"]
  sender_contains: ["@example-newsletter.com"]
actions:
  move_to_folder: "Archive/Newsletters"
  mark_as_read: true
  stop_processing_rules: true
exceptions:
  from_addresses:
    - {address: "boss@example.com"}
```

**Mapping rules (snake_case YAML ↔ camelCase Graph):**

Conditions (`conditions:` block):
- `from_addresses: [{name, address}]` ↔ `fromAddresses: [{emailAddress: {name, address}}]`
- `subject_contains: [str]` ↔ `subjectContains: [str]`
- `body_contains: [str]` ↔ `bodyContains: [str]`
- `sender_contains: [str]` ↔ `senderContains: [str]`
- `recipient_contains: [str]` ↔ `recipientContains: [str]`
- `header_contains: [str]` ↔ `headerContains: [str]`
- `importance: low|normal|high` ↔ `importance: low|normal|high`
- `has_attachments: bool` ↔ `hasAttachments: bool`
- `sent_to_me: bool` ↔ `sentToMe: bool`
- `sent_only_to_me: bool` ↔ `sentOnlyToMe: bool`
- `sent_cc_me: bool` ↔ `sentCcMe: bool`
- `sent_to_or_cc_me: bool` ↔ `sentToOrCcMe: bool`
- `not_sent_to_me: bool` ↔ `notSentToMe: bool`

Actions (`actions:` block):
- `move_to_folder: <path>` ↔ `moveToFolder: <id>`  (translator resolves)
- `copy_to_folder: <path>` ↔ `copyToFolder: <id>`
- `delete: bool` ↔ `delete: bool`
- `mark_as_read: bool` ↔ `markAsRead: bool`
- `mark_importance: low|normal|high` ↔ `markImportance: <s>`
- `assign_categories: [str]` ↔ `assignCategories: [str]`
- `forward_to: [{name?, address}]` ↔ `forwardTo: [{emailAddress: {name, address}}]`
- `redirect_to: [{name?, address}]` ↔ `redirectTo: [{emailAddress: {name, address}}]`
- `stop_processing_rules: bool` ↔ `stopProcessingRules: bool`

Exceptions: same shape as conditions.

Unknown keys at any level → `RuleYamlError` with full key path.

---

## Group 1 — YAML translator (rule_to_yaml ↔ rule_from_yaml)

**Files:**
- Modify: `src/m365ctl/mail/rules.py`
- Create: `tests/test_mail_rules_yaml.py`

### Task 1.1: Translator + tests (TDD)

- [ ] **Step 1: Write failing tests** (`tests/test_mail_rules_yaml.py`)

```python
from __future__ import annotations

import pytest

from m365ctl.mail.rules import (
    RuleYamlError, rule_from_yaml, rule_to_yaml,
)
from m365ctl.mail.models import Rule


def _rule(*, conditions=None, actions=None, exceptions=None) -> Rule:
    return Rule(
        id="rule-1",
        display_name="r1",
        sequence=10,
        is_enabled=True,
        has_error=False,
        is_read_only=False,
        conditions=conditions or {},
        actions=actions or {},
        exceptions=exceptions or {},
    )


def _id_to_path(folder_id: str) -> str:
    return {"fld-arch": "Archive/Newsletters", "fld-todo": "Inbox/ToDo"}[folder_id]


def _path_to_id(path: str) -> str:
    return {"Archive/Newsletters": "fld-arch", "Inbox/ToDo": "fld-todo"}[path]


def test_to_yaml_basic_shape():
    r = _rule(
        conditions={"subjectContains": ["weekly digest"]},
        actions={"markAsRead": True, "stopProcessingRules": True},
    )
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc == {
        "display_name": "r1",
        "sequence": 10,
        "enabled": True,
        "conditions": {"subject_contains": ["weekly digest"]},
        "actions": {"mark_as_read": True, "stop_processing_rules": True},
        "exceptions": {},
    }


def test_to_yaml_translates_folder_id_to_path():
    r = _rule(actions={"moveToFolder": "fld-arch", "markAsRead": True})
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc["actions"] == {
        "move_to_folder": "Archive/Newsletters",
        "mark_as_read": True,
    }


def test_to_yaml_unwraps_email_addresses():
    r = _rule(
        conditions={
            "fromAddresses": [
                {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
                {"emailAddress": {"address": "bob@example.com"}},
            ]
        },
    )
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc["conditions"]["from_addresses"] == [
        {"name": "Alice", "address": "alice@example.com"},
        {"address": "bob@example.com"},
    ]


def test_from_yaml_round_trip_basic():
    doc = {
        "display_name": "r1",
        "sequence": 10,
        "enabled": True,
        "conditions": {"subject_contains": ["weekly digest"]},
        "actions": {"mark_as_read": True, "stop_processing_rules": True},
        "exceptions": {},
    }
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body == {
        "displayName": "r1",
        "sequence": 10,
        "isEnabled": True,
        "conditions": {"subjectContains": ["weekly digest"]},
        "actions": {"markAsRead": True, "stopProcessingRules": True},
        "exceptions": {},
    }


def test_from_yaml_translates_folder_path_to_id():
    doc = {
        "display_name": "r",
        "sequence": 1,
        "enabled": True,
        "actions": {"move_to_folder": "Archive/Newsletters"},
    }
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body["actions"]["moveToFolder"] == "fld-arch"


def test_from_yaml_wraps_email_addresses():
    doc = {
        "display_name": "r",
        "sequence": 1,
        "enabled": True,
        "conditions": {
            "from_addresses": [
                {"name": "Alice", "address": "alice@example.com"},
                {"address": "bob@example.com"},
            ],
        },
        "actions": {"delete": True},
    }
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body["conditions"]["fromAddresses"] == [
        {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        {"emailAddress": {"address": "bob@example.com"}},
    ]


def test_from_yaml_rejects_unknown_top_level_key():
    doc = {
        "display_name": "r", "sequence": 1, "enabled": True,
        "actions": {"delete": True},
        "secret_sauce": "yes",
    }
    with pytest.raises(RuleYamlError, match="unknown key.*secret_sauce"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)


def test_from_yaml_rejects_unknown_condition():
    doc = {
        "display_name": "r", "sequence": 1, "enabled": True,
        "conditions": {"vibes": "good"},
        "actions": {"delete": True},
    }
    with pytest.raises(RuleYamlError, match="unknown.*condition.*vibes"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)


def test_from_yaml_rejects_unknown_action():
    doc = {
        "display_name": "r", "sequence": 1, "enabled": True,
        "actions": {"teleport": True},
    }
    with pytest.raises(RuleYamlError, match="unknown.*action.*teleport"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)


def test_round_trip_full_coverage():
    """Take a Graph-shape Rule, to_yaml, from_yaml, expect Graph body equal
    to the original dict (modulo id-only fields)."""
    original_conditions = {
        "fromAddresses": [
            {"emailAddress": {"name": "X", "address": "x@example.com"}}
        ],
        "subjectContains": ["foo", "bar"],
        "senderContains": ["@example.com"],
        "hasAttachments": True,
        "importance": "high",
    }
    original_actions = {
        "moveToFolder": "fld-arch",
        "markAsRead": True,
        "assignCategories": ["Work"],
        "stopProcessingRules": True,
    }
    original_exceptions = {
        "fromAddresses": [{"emailAddress": {"address": "boss@example.com"}}]
    }
    r = _rule(
        conditions=original_conditions,
        actions=original_actions,
        exceptions=original_exceptions,
    )
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body["conditions"] == original_conditions
    assert body["actions"] == original_actions
    assert body["exceptions"] == original_exceptions
    assert body["displayName"] == "r1"
    assert body["isEnabled"] is True
    assert body["sequence"] == 10


def test_to_yaml_omits_empty_sections():
    """If a Rule has no conditions/actions/exceptions, the YAML doc still
    has the keys (as empty dicts) so the round-trip is symmetric."""
    r = _rule()
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc["conditions"] == {}
    assert doc["actions"] == {}
    assert doc["exceptions"] == {}


def test_from_yaml_missing_required_field_rejected():
    doc = {"sequence": 1, "enabled": True, "actions": {"delete": True}}
    with pytest.raises(RuleYamlError, match="missing.*display_name"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** — append to `src/m365ctl/mail/rules.py`:

```python
"""...existing docstring...

Phase 8 adds YAML translator helpers ``rule_to_yaml`` /
``rule_from_yaml`` for round-trippable export/import.
"""
from __future__ import annotations

from typing import Any, Callable

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Rule


# ... existing list_rules / get_rule unchanged above ...


class RuleYamlError(ValueError):
    """Raised when a YAML rule document is malformed."""


# Snake_case YAML keys ↔ camelCase Graph keys for conditions/exceptions.
_CONDITION_BIDI: dict[str, str] = {
    "from_addresses": "fromAddresses",
    "subject_contains": "subjectContains",
    "body_contains": "bodyContains",
    "body_or_subject_contains": "bodyOrSubjectContains",
    "sender_contains": "senderContains",
    "recipient_contains": "recipientContains",
    "header_contains": "headerContains",
    "importance": "importance",
    "has_attachments": "hasAttachments",
    "sent_to_me": "sentToMe",
    "sent_only_to_me": "sentOnlyToMe",
    "sent_cc_me": "sentCcMe",
    "sent_to_or_cc_me": "sentToOrCcMe",
    "not_sent_to_me": "notSentToMe",
}

_ACTION_BIDI: dict[str, str] = {
    "move_to_folder": "moveToFolder",
    "copy_to_folder": "copyToFolder",
    "delete": "delete",
    "mark_as_read": "markAsRead",
    "mark_importance": "markImportance",
    "assign_categories": "assignCategories",
    "forward_to": "forwardTo",
    "redirect_to": "redirectTo",
    "stop_processing_rules": "stopProcessingRules",
}

_CONDITION_BIDI_REVERSE = {v: k for k, v in _CONDITION_BIDI.items()}
_ACTION_BIDI_REVERSE = {v: k for k, v in _ACTION_BIDI.items()}

_EMAIL_LIST_FIELDS_CONDITION = {"fromAddresses"}
_EMAIL_LIST_FIELDS_ACTION = {"forwardTo", "redirectTo"}
_FOLDER_ID_FIELDS_ACTION = {"moveToFolder", "copyToFolder"}


def rule_to_yaml(rule: Rule, *, folder_id_to_path: Callable[[str], str]) -> dict[str, Any]:
    """Render a Rule into a YAML-friendly dict (snake_case, paths, unwrapped emails)."""
    return {
        "display_name": rule.display_name,
        "sequence": rule.sequence,
        "enabled": rule.is_enabled,
        "conditions": _block_to_yaml(rule.conditions, kind="condition", folder_id_to_path=folder_id_to_path),
        "actions": _block_to_yaml(rule.actions, kind="action", folder_id_to_path=folder_id_to_path),
        "exceptions": _block_to_yaml(rule.exceptions, kind="condition", folder_id_to_path=folder_id_to_path),
    }


def rule_from_yaml(doc: dict[str, Any], *, folder_path_to_id: Callable[[str], str]) -> dict[str, Any]:
    """Translate a YAML rule doc into a Graph messageRule body dict."""
    if not isinstance(doc, dict):
        raise RuleYamlError(f"top-level must be a mapping, got {type(doc).__name__}")
    known_top = {"display_name", "sequence", "enabled", "conditions", "actions", "exceptions"}
    unknown = set(doc.keys()) - known_top
    if unknown:
        raise RuleYamlError(f"unknown key(s) at top level: {sorted(unknown)}")
    if "display_name" not in doc:
        raise RuleYamlError("missing required field 'display_name'")
    body: dict[str, Any] = {
        "displayName": doc["display_name"],
        "sequence": doc.get("sequence", 100),
        "isEnabled": bool(doc.get("enabled", True)),
        "conditions": _block_from_yaml(doc.get("conditions") or {}, kind="condition", folder_path_to_id=folder_path_to_id),
        "actions": _block_from_yaml(doc.get("actions") or {}, kind="action", folder_path_to_id=folder_path_to_id),
        "exceptions": _block_from_yaml(doc.get("exceptions") or {}, kind="condition", folder_path_to_id=folder_path_to_id),
    }
    return body


def _block_to_yaml(
    block: dict[str, Any],
    *,
    kind: str,
    folder_id_to_path: Callable[[str], str],
) -> dict[str, Any]:
    if not block:
        return {}
    reverse = _CONDITION_BIDI_REVERSE if kind == "condition" else _ACTION_BIDI_REVERSE
    out: dict[str, Any] = {}
    for graph_key, val in block.items():
        if graph_key not in reverse:
            # Unknown Graph key — keep verbatim under a debug namespace so a
            # round trip doesn't silently drop server-only fields. Surfaces
            # any unmapped Graph fields the user can decide to route through.
            out[f"_unknown_{graph_key}"] = val
            continue
        yaml_key = reverse[graph_key]
        if graph_key in _EMAIL_LIST_FIELDS_CONDITION or graph_key in _EMAIL_LIST_FIELDS_ACTION:
            out[yaml_key] = [_unwrap_email(x) for x in val or []]
        elif graph_key in _FOLDER_ID_FIELDS_ACTION:
            out[yaml_key] = folder_id_to_path(val) if val else val
        else:
            out[yaml_key] = val
    return out


def _block_from_yaml(
    block: dict[str, Any],
    *,
    kind: str,
    folder_path_to_id: Callable[[str], str],
) -> dict[str, Any]:
    if not block:
        return {}
    forward = _CONDITION_BIDI if kind == "condition" else _ACTION_BIDI
    out: dict[str, Any] = {}
    for yaml_key, val in block.items():
        if yaml_key.startswith("_unknown_"):
            # Verbatim pass-through for unmapped fields seen at export time.
            out[yaml_key[len("_unknown_"):]] = val
            continue
        if yaml_key not in forward:
            raise RuleYamlError(f"unknown {kind} key: {yaml_key!r}")
        graph_key = forward[yaml_key]
        if graph_key in _EMAIL_LIST_FIELDS_CONDITION or graph_key in _EMAIL_LIST_FIELDS_ACTION:
            out[graph_key] = [_wrap_email(x) for x in val or []]
        elif graph_key in _FOLDER_ID_FIELDS_ACTION:
            out[graph_key] = folder_path_to_id(val) if val else val
        else:
            out[graph_key] = val
    return out


def _unwrap_email(item: dict) -> dict:
    inner = item.get("emailAddress") or {}
    out = {}
    if "name" in inner and inner["name"]:
        out["name"] = inner["name"]
    if "address" in inner:
        out["address"] = inner["address"]
    return out


def _wrap_email(item: dict) -> dict:
    inner: dict[str, Any] = {}
    if "name" in item and item["name"]:
        inner["name"] = item["name"]
    if "address" in item:
        inner["address"] = item["address"]
    return {"emailAddress": inner}
```

- [ ] **Step 4:** Run tests:
```bash
uv run pytest tests/test_mail_rules_yaml.py -v
```
Expected: ~12 tests pass.

- [ ] **Step 5:** mypy + ruff clean before commit.

- [ ] **Step 6: Commit:**
```bash
git add src/m365ctl/mail/rules.py tests/test_mail_rules_yaml.py
git commit -m "feat(mail/rules): YAML ↔ Graph messageRule translator with round-trip + folder path resolution"
```

---

## Group 2 — Mutate executors (create/update/delete/enable/disable/reorder)

**Files:**
- Create: `src/m365ctl/mail/mutate/rules.py`
- Modify: `src/m365ctl/mail/mutate/undo.py` (register inverses)
- Create: `tests/test_mail_mutate_rules.py`

Pattern mirrors `mail/mutate/move.py` etc.: each executor takes `(op, graph, logger, *, before=...)`, posts to Graph, writes audit lines, returns a `RuleResult` (status/error/after).

### Task 2.1: Mutate executors

- [ ] **Step 1: Failing tests** (`tests/test_mail_mutate_rules.py`)

```python
from __future__ import annotations

from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.rules import (
    execute_create, execute_delete, execute_set_enabled, execute_update,
    execute_reorder,
)


def _op(action: str, args: dict, item_id: str = "") -> Operation:
    return Operation(
        op_id=new_op_id(),
        action=action,
        drive_id="me",
        item_id=item_id,
        args=args,
        dry_run_result="",
    )


def test_create_posts_body_and_records_id(tmp_path):
    graph = MagicMock()
    graph.post.return_value = {
        "id": "new-rule-1",
        "displayName": "r",
        "sequence": 10,
        "isEnabled": True,
    }
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.create", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "body": {"displayName": "r", "sequence": 10, "isEnabled": True},
    })
    r = execute_create(op, graph, logger, before={})
    assert r.status == "ok"
    assert r.after["id"] == "new-rule-1"
    graph.post.assert_called_once()
    posted_path, _ = graph.post.call_args.args, graph.post.call_args.kwargs
    assert "messageRules" in graph.post.call_args.args[0]


def test_update_patches_with_etag(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "rule-1", "displayName": "renamed"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.update", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "rule_id": "rule-1",
        "body": {"displayName": "renamed"},
    }, item_id="rule-1")
    r = execute_update(op, graph, logger, before={"displayName": "before"})
    assert r.status == "ok"
    graph.patch.assert_called_once()


def test_delete_calls_graph_delete(tmp_path):
    graph = MagicMock()
    graph.delete.return_value = None
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.delete", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "rule_id": "rule-1",
    }, item_id="rule-1")
    r = execute_delete(op, graph, logger, before={
        "displayName": "r", "sequence": 10, "isEnabled": True,
        "conditions": {}, "actions": {"delete": True}, "exceptions": {},
    })
    assert r.status == "ok"
    graph.delete.assert_called_once()


def test_set_enabled_patches_only_isenabled(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "rule-1", "isEnabled": False}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.set-enabled", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "rule_id": "rule-1",
        "is_enabled": False,
    }, item_id="rule-1")
    r = execute_set_enabled(op, graph, logger, before={"isEnabled": True})
    assert r.status == "ok"
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {"isEnabled": False}


def test_reorder_patches_sequence_per_rule(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "rule-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.reorder", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "ordering": [
            {"rule_id": "rule-A", "sequence": 10},
            {"rule_id": "rule-B", "sequence": 20},
        ],
    })
    r = execute_reorder(op, graph, logger, before={})
    assert r.status == "ok"
    assert graph.patch.call_count == 2


def test_executor_propagates_graph_error_as_status(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.post.side_effect = GraphError("InvalidRequest: bad rule body")
    logger = AuditLogger(ops_dir=tmp_path / "ops")
    op = _op("mail.rule.create", {
        "mailbox_spec": "me", "auth_mode": "delegated",
        "body": {"displayName": "x"},
    })
    r = execute_create(op, graph, logger, before={})
    assert r.status == "error"
    assert "InvalidRequest" in (r.error or "")
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** (`src/m365ctl/mail/mutate/rules.py`)

```python
"""Server-side inbox-rule mutators with audit + undo support.

All five executors follow the existing mail-mutate convention:
    execute_<verb>(op, graph, logger, *, before: dict) -> MailResult

Each writes one ``begin``/``ok``/``error`` triple to the audit log.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.audit import AuditLogger
from m365ctl.common.graph import GraphClient, GraphError
from m365ctl.common.planfile import Operation
from m365ctl.mail.endpoints import user_base


@dataclass
class RuleResult:
    op_id: str
    status: str         # "ok" | "error"
    error: str | None
    after: dict[str, Any]


def _path_rules(mailbox_spec: str, auth_mode: str) -> str:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    return f"{ub}/mailFolders/inbox/messageRules"


def execute_create(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    logger.write({"phase": "begin", "op_id": op.op_id, "action": op.action,
                  "args": args, "before": before})
    try:
        created = graph.post(base, json=args["body"])
    except GraphError as e:
        logger.write({"phase": "end", "op_id": op.op_id, "status": "error",
                      "error": str(e)})
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    logger.write({"phase": "end", "op_id": op.op_id, "status": "ok",
                  "after": {"id": created.get("id")}})
    return RuleResult(op_id=op.op_id, status="ok", error=None, after=created)


def execute_update(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    path = f"{base}/{args['rule_id']}"
    logger.write({"phase": "begin", "op_id": op.op_id, "action": op.action,
                  "args": args, "before": before})
    try:
        updated = graph.patch(path, json_body=args["body"])
    except GraphError as e:
        logger.write({"phase": "end", "op_id": op.op_id, "status": "error",
                      "error": str(e)})
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    logger.write({"phase": "end", "op_id": op.op_id, "status": "ok",
                  "after": {"id": updated.get("id")}})
    return RuleResult(op_id=op.op_id, status="ok", error=None, after=updated)


def execute_delete(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    path = f"{base}/{args['rule_id']}"
    logger.write({"phase": "begin", "op_id": op.op_id, "action": op.action,
                  "args": args, "before": before})
    try:
        graph.delete(path)
    except GraphError as e:
        logger.write({"phase": "end", "op_id": op.op_id, "status": "error",
                      "error": str(e)})
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    logger.write({"phase": "end", "op_id": op.op_id, "status": "ok"})
    return RuleResult(op_id=op.op_id, status="ok", error=None, after={})


def execute_set_enabled(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> RuleResult:
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    path = f"{base}/{args['rule_id']}"
    body = {"isEnabled": bool(args["is_enabled"])}
    logger.write({"phase": "begin", "op_id": op.op_id, "action": op.action,
                  "args": args, "before": before})
    try:
        updated = graph.patch(path, json_body=body)
    except GraphError as e:
        logger.write({"phase": "end", "op_id": op.op_id, "status": "error",
                      "error": str(e)})
        return RuleResult(op_id=op.op_id, status="error", error=str(e), after={})
    logger.write({"phase": "end", "op_id": op.op_id, "status": "ok"})
    return RuleResult(op_id=op.op_id, status="ok", error=None, after=updated)


def execute_reorder(
    op: Operation, graph: GraphClient, logger: AuditLogger, *, before: dict,
) -> RuleResult:
    """Reorder via per-rule PATCH of the ``sequence`` field.

    ``args.ordering`` is a list of ``{rule_id, sequence}`` dicts in the
    desired evaluation order. We just write each sequence value as
    given — caller is responsible for picking a sane spread (e.g. 10,
    20, 30 ...).
    """
    args = op.args
    base = _path_rules(args["mailbox_spec"], args["auth_mode"])
    logger.write({"phase": "begin", "op_id": op.op_id, "action": op.action,
                  "args": args, "before": before})
    errors: list[str] = []
    for entry in args["ordering"]:
        path = f"{base}/{entry['rule_id']}"
        try:
            graph.patch(path, json_body={"sequence": int(entry["sequence"])})
        except GraphError as e:
            errors.append(f"{entry['rule_id']}: {e}")
    if errors:
        msg = "; ".join(errors)
        logger.write({"phase": "end", "op_id": op.op_id, "status": "error",
                      "error": msg})
        return RuleResult(op_id=op.op_id, status="error", error=msg, after={})
    logger.write({"phase": "end", "op_id": op.op_id, "status": "ok",
                  "after": {"ordering": args["ordering"]}})
    return RuleResult(op_id=op.op_id, status="ok", error=None,
                      after={"ordering": args["ordering"]})
```

- [ ] **Step 4:** Run tests:
```bash
uv run pytest tests/test_mail_mutate_rules.py -v
```
Expected: 6 tests pass. If `graph.delete` is missing on the GraphClient, add it (mirrors `graph.patch` shape) — but inspect `src/m365ctl/common/graph.py` first; many CRUD verbs already exist.

- [ ] **Step 5:** Verify GraphClient has a `delete` method. If not, add one:
```python
# In src/m365ctl/common/graph.py
def delete(self, path: str, *, headers: dict | None = None) -> None:
    def _do() -> None:
        merged = self._auth_headers()
        if headers:
            merged.update(headers)
        resp = self._client.delete(path, headers=merged)
        self._maybe_raise(resp)
    self._retry(_do)
```

- [ ] **Step 6: mypy + ruff clean.**

- [ ] **Step 7: Commit:**
```bash
git add src/m365ctl/mail/mutate/rules.py tests/test_mail_mutate_rules.py src/m365ctl/common/graph.py
git commit -m "feat(mail/mutate): inbox-rule executors create/update/delete/set-enabled/reorder + GraphClient.delete"
```

### Task 2.2: Inverse registration in undo

- [ ] **Step 1:** Inspect `src/m365ctl/mail/mutate/undo.py` `build_reverse_mail_operation`. The function dispatches by `op.action` and emits a reverse `Operation`. Add four new branches:

  - `mail.rule.create` → reverse is `mail.rule.delete` (rule_id from `after.id` in audit, fallback to `op.item_id`).
  - `mail.rule.delete` → reverse is `mail.rule.create` with `body` reconstructed from the recorded `before` (drop `id` since Graph assigns a new one).
  - `mail.rule.update` → reverse is `mail.rule.update` with `body` set to the `before` snapshot.
  - `mail.rule.set-enabled` → reverse flips `is_enabled` based on `before.isEnabled`.
  - `mail.rule.reorder` → reverse is `mail.rule.reorder` with the original ordering from `before.ordering` (record this in `before` at execute time — patch `execute_reorder` so it captures the current sequence-by-rule into `before` if not provided).

- [ ] **Step 2:** Add tests in a new file `tests/test_mail_mutate_undo_rules.py` (one test per inverse branch). Skip the body — just match the action name and key args.

- [ ] **Step 3:** Run `uv run pytest tests/test_mail_mutate_undo_rules.py -v`.

- [ ] **Step 4: Commit:**
```bash
git add src/m365ctl/mail/mutate/undo.py tests/test_mail_mutate_undo_rules.py
git commit -m "feat(mail/mutate/undo): register inverses for mail.rule.{create,delete,update,set-enabled,reorder}"
```

---

## Group 3 — CLI subcommands + import/export

**Files:**
- Modify: `src/m365ctl/mail/cli/rules.py` (extend with create/update/delete/enable/disable/reorder/export/import)
- Create: `tests/test_cli_mail_rules_crud.py`
- Create: `scripts/mail/rules-server-side/example-rule.yaml`

### Task 3.1: CLI subcommands

The existing `mail rules` CLI has `list`/`show`. Phase 8 adds:

- `mail rules create --from-file rule.yaml` — POST one rule. Folder paths in YAML resolved to ids via `resolve_folder_path`.
- `mail rules update <rule_id> --from-file rule.yaml` — PATCH one rule.
- `mail rules delete <rule_id> --confirm` — DELETE one rule.
- `mail rules enable <rule_id>` / `mail rules disable <rule_id>` — quick flag flip.
- `mail rules reorder --from-file ordering.yaml` — bulk-reorder. The YAML is a flat list of `{rule_id, sequence}` (or `[name1, name2, ...]` shorthand resolved against current rules).
- `mail rules export [--out PATH]` — fetch all rules and dump a YAML doc with a `rules:` list.
- `mail rules import --from-file rules.yaml [--replace] --confirm` — apply the YAML doc; default behaviour is "create new + leave existing alone"; `--replace` first deletes all current rules, then creates from the file. Exits non-zero on any per-rule error.

For the round-trip integration test, `import` after `export` must produce an identical set of rules.

- [ ] **Step 1: Failing tests** (`tests/test_cli_mail_rules_crud.py`)

Cover at least:
- `create --from-file` reads YAML, resolves folder paths, calls `execute_create`.
- `update <id> --from-file` reads YAML, resolves paths, calls `execute_update`.
- `delete <id> --confirm` calls `execute_delete`; without `--confirm` returns 2 with error message.
- `enable <id>` calls `execute_set_enabled` with `is_enabled=True`.
- `disable <id>` same with `is_enabled=False`.
- `reorder --from-file ordering.yaml --confirm` calls `execute_reorder` with the parsed ordering.
- `export --out file.yaml` writes a YAML doc with a `rules: [...]` list of all rules.
- `import --from-file rules.yaml --confirm` creates each rule via `execute_create`.
- `import --from-file rules.yaml --replace --confirm` first deletes all existing, then creates.
- A round-trip test: pre-seed graph mock with N rules, call `export`, parse the output YAML, call `import` against a fresh mock, assert the create-bodies match the original Graph rules.

(Tests use MagicMock graph + a tmp_path config + patches on `execute_create`/`execute_update`/etc. so this group doesn't require live Graph.)

- [ ] **Step 2: Implement** — extend `src/m365ctl/mail/cli/rules.py` with the new subparsers and handlers. Reuse `load_and_authorize`, `resolve_folder_path`, `list_folders` for the path↔id maps. Build a folder cache once per invocation (call `list_folders` once and memoize the bidirectional map for use in YAML translation).

- [ ] **Step 3: Example YAML** (`scripts/mail/rules-server-side/example-rule.yaml`):
```yaml
display_name: archive-newsletters-server-side
sequence: 10
enabled: true
conditions:
  sender_contains: ["@newsletter.example.com"]
actions:
  move_to_folder: "Archive/Newsletters"
  mark_as_read: true
  stop_processing_rules: true
```

- [ ] **Step 4:** Run all CLI tests:
```bash
uv run pytest tests/test_cli_mail_rules_crud.py -v
```
Expect ~10-12 tests.

- [ ] **Step 5:** mypy + ruff clean.

- [ ] **Step 6: Commit:**
```bash
git add src/m365ctl/mail/cli/rules.py tests/test_cli_mail_rules_crud.py scripts/mail/rules-server-side/
git commit -m "feat(mail/cli): rules CRUD verbs (create|update|delete|enable|disable|reorder|export|import)"
```

---

## Group 4 — Release 0.9.0

### Task 4.1: Bump + changelog + README

- [ ] **Step 1:** `pyproject.toml`: `version = "0.8.0"` → `version = "0.9.0"`.

- [ ] **Step 2:** Prepend to `CHANGELOG.md`:

```markdown
## 0.9.0 — Phase 8: server-side inbox rules CRUD

### Added
- `m365ctl.mail.rules.{rule_to_yaml,rule_from_yaml}` — round-trippable
  YAML ↔ Graph `messageRule` translator. Folder paths resolve
  bidirectionally via Phase 2's `resolve_folder_path`.
- `m365ctl.mail.mutate.rules` — `execute_{create,update,delete,
  set_enabled,reorder}` with full audit + undo registration. Each rule
  op has an inverse so `m365ctl undo <op-id>` rolls back.
- `mail rules` CLI extended: `create`, `update`, `delete`, `enable`,
  `disable`, `reorder`, `export`, `import`. `--replace` flag on
  `import` first deletes existing rules then re-creates from file.
- `GraphClient.delete()` for HTTP DELETE.

### Round-trip guarantee
`mail rules export --out a.yaml` followed by
`mail rules import --from-file a.yaml --replace --confirm` produces a
rule set semantically equivalent to the source mailbox (modulo
server-assigned ids).

### Deferred (Phase 8.x)
- Graph rule-conditions surface beyond the documented set (e.g. flag
  checks, encryption flags). The translator pass-throughs `_unknown_*`
  for fields it doesn't model so a Graph-side update doesn't silently
  drop data on a round trip.
- `mail rules diff` between mailbox and YAML.
```

- [ ] **Step 3:** README Mail bullet:
```markdown
- **Inbox rules CRUD (Phase 8):** `mail rules {create|update|delete|
  enable|disable|reorder|export|import}` — round-trippable YAML
  pipeline. `mail rules export --out a.yaml` then
  `mail rules import --from-file a.yaml --replace --confirm` rebuilds
  the rule set. Audit + undo intact.
```

- [ ] **Step 4:** `uv sync`.

- [ ] **Step 5:** Full quality gates:
```bash
uv run pytest --tb=no -q
uv run mypy src/m365ctl
uv run ruff check
```
Expect ~660-680 passing depending on test count, 0 mypy errors, ruff clean.

- [ ] **Step 6:** Two commits:
```bash
git add pyproject.toml CHANGELOG.md README.md
git commit -m "chore(release): bump to 0.9.0 + Phase 8 rules CRUD changelog/README"
git add uv.lock
git commit -m "chore(release): sync uv.lock for 0.9.0"
```

### Task 4.2: Push, PR, merge, tag

- [ ] **Step 1:** `git push -u origin phase-8-rules-crud`.

- [ ] **Step 2:** Open PR titled `Phase 8: server-side inbox rules CRUD → 0.9.0` with body summarising the YAML translator, mutator + undo, CLI subcommands, and round-trip guarantee. Test plan checklist.

- [ ] **Step 3:** `gh pr checks --watch`. Fix-forward on red (no force-push).

- [ ] **Step 4:** `gh pr merge --squash --delete-branch`.

- [ ] **Step 5:** `git checkout main && git pull --ff-only`.

- [ ] **Step 6:** `git tag -a v0.9.0 -m "Phase 8: server-side inbox rules CRUD"` and `git push origin v0.9.0`.

---

## Self-review checklist

**Spec coverage (§19 Phase 8 + §15):**
- ✅ `m365ctl.mail.rules` extended with all listed helpers (G1, G3).
- ✅ `m365ctl.mail.mutate.rules` with audit/undo (G2).
- ✅ YAML ↔ folderId translator (G1).
- ✅ CLI: `mail-rules {list,show,create,update,delete,enable,disable,reorder,export,import}` — `list`/`show` already shipped Phase 1; remaining seven added in G3.
- ✅ Plan-file support — bulk reorder + bulk import use `--from-file` YAML (functional equivalent to plan files; the existing planfile module is JSON-shaped for batch ops, here YAML-shaped per spec §15).
- ✅ Tests: round-trip export → import → identical set (G3 step 1 last bullet).
- ⚠️ Spec said bump to 0.10.0 sequentially; we bump to 0.9.0 because we skipped 5b/6.

**Acceptance:**
- ✅ Round-trip: documented as the headline test.
- ✅ Folder name ↔ id translation: in both directions, with explicit map.
- ✅ Audit + undo: each executor writes audit; undo dispatcher registers inverses.

**Placeholder scan:** none.

**Type consistency:** `RuleResult` (status/error/after) consistent with other `mail.mutate.*` result shapes. YAML keys checked symmetrically (`_CONDITION_BIDI` ↔ reverse). `Operation.args` keyed identically across executor + test + CLI.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-8-rules-crud.md`.

Execution: subagent-driven-development per established cadence. Branch `phase-8-rules-crud` already created off `main`.
