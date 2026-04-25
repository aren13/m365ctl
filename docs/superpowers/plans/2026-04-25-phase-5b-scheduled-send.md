# Phase 5b — Scheduled Send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** `mail send <draft_id> --schedule-at <iso>` defers delivery via the MAPI `PR_DEFERRED_DELIVERY_TIME` extended property. Graph PATCHes the draft with `singleValueExtendedProperties: [{id: "SystemTime 0x3FEF", value: "<iso>"}]`, then POSTs `/send`. The Outlook client holds the message locally until the deliver-at time; if the client is offline at that moment, send is queued until next online.

**Architecture:**
- Add `execute_send_scheduled(op, graph, logger, *, before)` in `mail/mutate/send.py` — same shape as `execute_send_draft` plus a PATCH step.
- Add `--schedule-at <iso>` flag to `mail/cli/send.py`. Gated on `cfg.mail.schedule_send_enabled` (already in config).
- Help text documents the caveat.

**Tech stack:** Existing primitives. No new deps.

**Baseline:** `main` post-PR-#19 (7034721), 846 passing tests, 0 mypy errors. Tag `v1.2.0`.

**Version bump:** 1.2.0 → 1.3.0.

---

## Group 1 — Executor + CLI + tests (one commit)

**Files:**
- Modify: `src/m365ctl/mail/mutate/send.py`
- Modify: `src/m365ctl/mail/cli/send.py`
- Create: `tests/test_mail_mutate_send_scheduled.py`
- Modify: `tests/test_cli_mail_send.py` (add scheduled-send CLI tests)

### Steps

- [ ] **Step 1: Failing tests** at `tests/test_mail_mutate_send_scheduled.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger
from m365ctl.common.planfile import Operation, new_op_id
from m365ctl.mail.mutate.send import execute_send_scheduled


def _op(*, schedule_at: str = "2026-05-01T09:00:00+00:00") -> Operation:
    return Operation(
        op_id=new_op_id(),
        action="mail.send.scheduled",
        drive_id="me",
        item_id="draft-1",
        args={"auth_mode": "delegated", "schedule_at": schedule_at},
        dry_run_result="",
    )


def test_patches_extended_property_then_sends(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "ok"

    # PATCH first, then POST /send.
    assert graph.method_calls[0][0] == "patch"
    patch_path, _ = graph.patch.call_args.args, graph.patch.call_args.kwargs
    body = graph.patch.call_args.kwargs["json_body"]
    assert body == {
        "singleValueExtendedProperties": [
            {"id": "SystemTime 0x3FEF", "value": "2026-05-01T09:00:00+00:00"},
        ],
    }
    assert "/messages/draft-1" in graph.patch.call_args.args[0]

    assert graph.method_calls[1][0] == "post_raw"
    assert "/messages/draft-1/send" in graph.post_raw.call_args.args[0]


def test_app_only_routes_via_users_upn(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = Operation(
        op_id=new_op_id(),
        action="mail.send.scheduled",
        drive_id="bob@example.com",
        item_id="draft-1",
        args={"auth_mode": "app-only",
              "schedule_at": "2026-05-01T09:00:00+00:00"},
        dry_run_result="",
    )
    execute_send_scheduled(op, graph, logger, before={})
    assert "/users/bob@example.com/messages/draft-1" in graph.patch.call_args.args[0]


def test_patch_failure_aborts_send(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.patch.side_effect = GraphError("BadRequest: invalid extended property")
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "error"
    assert "BadRequest" in (r.error or "")
    graph.post_raw.assert_not_called()


def test_send_failure_after_patch(tmp_path):
    from m365ctl.common.graph import GraphError
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    graph.post_raw.side_effect = GraphError("Forbidden")
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "error"
    assert "Forbidden" in (r.error or "")
    # PATCH happened first; the extended property is set on the draft even
    # though send failed. Operator can re-send with `mail send <draft>`.
    graph.patch.assert_called_once()


def test_records_schedule_at_in_audit_after(tmp_path):
    graph = MagicMock()
    graph.patch.return_value = {"id": "draft-1"}
    logger = AuditLogger(ops_dir=tmp_path / "ops")

    op = _op()
    r = execute_send_scheduled(op, graph, logger, before={})

    assert r.status == "ok"
    assert r.after.get("schedule_at") == "2026-05-01T09:00:00+00:00"
```

- [ ] **Step 2:** Run, verify ImportError.

- [ ] **Step 3: Implement** — append to `src/m365ctl/mail/mutate/send.py`:

```python
_DEFERRED_DELIVERY_PROP_ID = "SystemTime 0x3FEF"


def execute_send_scheduled(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """PATCH the draft with PR_DEFERRED_DELIVERY_TIME, then POST /send.

    Outlook holds the message locally until ``schedule_at``. Caveat:
    depends on the Outlook client being online at the deliver-at time.
    """
    ub = _user_base(op)
    schedule_at = op.args["schedule_at"]
    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-send-scheduled",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    patch_body = {
        "singleValueExtendedProperties": [
            {"id": _DEFERRED_DELIVERY_PROP_ID, "value": schedule_at},
        ],
    }
    try:
        graph.patch(f"{ub}/messages/{op.item_id}", json_body=patch_body)
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    try:
        graph.post_raw(f"{ub}/messages/{op.item_id}/send")
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    after: dict[str, Any] = {"sent_at": _now_utc_iso(), "schedule_at": schedule_at}
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

- [ ] **Step 4: CLI plumbing** — modify `src/m365ctl/mail/cli/send.py`:
  - Add argparse flag: `--schedule-at <iso>` (str, optional).
  - When set:
    1. Validate `cfg.mail.schedule_send_enabled` is True. If False → exit 2 with stderr `"scheduled-send is disabled in config (set [mail].schedule_send_enabled = true)"`.
    2. Validate the ISO string parses (`datetime.fromisoformat(s.replace("Z", "+00:00"))`). Else exit 2 with stderr.
    3. Validate the parsed datetime is in the future (compare to `datetime.now(timezone.utc)`). Else exit 2.
    4. Build the operation with `action="mail.send.scheduled"` and `args["schedule_at"]=<iso>`. Dispatch to `execute_send_scheduled`.
  - Help text addition to the `--schedule-at` flag: `"Defer delivery via PR_DEFERRED_DELIVERY_TIME. Caveat: requires Outlook client online at the scheduled time."`.
  - `--schedule-at` is mutually exclusive with `--new` (scheduled send only works against an existing draft).

- [ ] **Step 5: CLI tests** — extend `tests/test_cli_mail_send.py` (or create a new file `tests/test_cli_mail_send_scheduled.py`):
  - `mail send <draft> --schedule-at <future-iso> --confirm` calls `execute_send_scheduled`.
  - `mail send <draft> --schedule-at <iso>` without `--confirm` returns 2.
  - `mail send <draft> --schedule-at "garbage"` returns 2 with parse error.
  - `mail send <draft> --schedule-at <past-iso>` returns 2 with "must be in the future".
  - With `cfg.mail.schedule_send_enabled = false`, returns 2 with the disabled message.
  - `mail send --new --schedule-at <iso>` returns 2 (mutex).

- [ ] **Step 6:** Quality gates: pytest (846 + ~10 = ~856), mypy 0, ruff clean.

- [ ] **Step 7: Commit:**
```bash
git add src/m365ctl/mail/mutate/send.py src/m365ctl/mail/cli/send.py \
        tests/test_mail_mutate_send_scheduled.py tests/test_cli_mail_send_scheduled.py
git commit -m "feat(mail/send): scheduled send via PR_DEFERRED_DELIVERY_TIME (gated on schedule_send_enabled)"
```

---

## Group 2 — Release 1.3.0

### Task 2.1: Bump + changelog + README + lockfile

- [ ] `pyproject.toml`: 1.2.0 → 1.3.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.3.0 — Phase 5b: scheduled send

### Added
- `m365ctl.mail.mutate.send.execute_send_scheduled` — PATCHes the draft
  with `singleValueExtendedProperties: PR_DEFERRED_DELIVERY_TIME` then
  POSTs `/send`. Outlook holds the message locally until the deliver-at
  time.
- CLI: `mail send <draft-id> --schedule-at <iso> --confirm`. Gated on
  `[mail].schedule_send_enabled = true` in config.toml.
- Help text documents the caveat that delivery depends on the Outlook
  client being online at the scheduled time.

### Validation
- `--schedule-at` parses ISO-8601 (with `Z` or `+00:00`).
- Scheduled time must be in the future.
- Mutually exclusive with `--new` (only existing drafts can be scheduled).
```

- [ ] README Mail bullet:
```markdown
- **Scheduled send (Phase 5b, 1.3):** `mail send <draft> --schedule-at <iso>`
  defers delivery via the MAPI `PR_DEFERRED_DELIVERY_TIME` extended
  property. Gated behind `[mail].schedule_send_enabled`.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits.

### Task 2.2: Push, PR, merge, tag v1.3.0

Standard cadence.

---

## Self-review

**Spec coverage (§19 Phase 5b):**
- ✅ `mail.compose.send_scheduled(draft_id, deliver_at)` — implemented as `execute_send_scheduled` in the executor pattern (consistent with the rest of `mail.mutate.*`).
- ✅ CLI: `mail send --schedule-at`, gated on config.
- ✅ Help text caveat.
- ⚠️ Spec said bump to 0.7.0 sequentially; we bump to 1.3.0 because we shipped most of the spec already.

**Type consistency:** `MailResult` shape unchanged. Audit API matches Phase 6/8/9 pattern.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-5b-scheduled-send.md`. Branch `phase-5b-scheduled-send` already off `main`.
