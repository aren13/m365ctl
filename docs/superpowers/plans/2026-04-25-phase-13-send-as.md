# Phase 13 — Send-As / On-Behalf-Of Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Send mail as another mailbox via app-only `POST /users/{from_upn}/sendMail`. The authenticated principal (the app) and the effective sender (the from-UPN) are both audit-logged. Mandatory `--unsafe-scope` confirmation when `from_upn` is not in `allow_mailboxes`.

**Architecture:**
- New `execute_send_as(op, graph, logger, *, before)` in `mail/mutate/send.py`. Forces app-only routing to `/users/{from_upn}/sendMail`. Audit fields include `effective_sender=from_upn`, `authenticated_principal=<client_id>` (read from `cfg.client_id`).
- New `m365ctl.mail.cli.sendas` — argparse for `mail sendas <from-upn> --to <addr>... --subject ... --body ... [--cc] [--bcc] [--body-file] [--body-type text|html] [--unsafe-scope] --confirm`.
- New bin wrapper `bin/mail-sendas`.
- `assert_mailbox_allowed(f"upn:{from_upn}", cfg, auth_mode="app-only", unsafe_scope=args.unsafe_scope)` for the gate. If `from_upn` is in `allow_mailboxes` (as `upn:<addr>` or as a wildcard), proceed without `--unsafe-scope`. If not in scope, the existing safety helper requires `--unsafe-scope` AND a TTY confirm — that flow is reused as-is.

**Tech stack:** Existing primitives. No new deps.

**Baseline:** `main` post-PR-#20 (2e5021c), 857 passing tests, 0 mypy errors. Tag `v1.3.0`.

**Version bump:** 1.3.0 → 1.4.0.

---

## File Structure

**New:**
- `src/m365ctl/mail/cli/sendas.py` — argparse + dispatcher for `mail sendas`.
- `bin/mail-sendas` — exec wrapper.
- `tests/test_mail_mutate_send_as.py`
- `tests/test_cli_mail_sendas.py`

**Modify:**
- `src/m365ctl/mail/mutate/send.py` — add `execute_send_as`.
- `src/m365ctl/mail/cli/__main__.py` — route `sendas` verb.
- `pyproject.toml` — bump 1.3.0 → 1.4.0.
- `CHANGELOG.md` — 1.4.0 section.
- `README.md` — Mail bullet.

---

## Group 1 — Executor + CLI + tests (one commit)

**Files:**
- Modify: `src/m365ctl/mail/mutate/send.py`
- Create: `src/m365ctl/mail/cli/sendas.py`
- Modify: `src/m365ctl/mail/cli/__main__.py`
- Create: `bin/mail-sendas`
- Create: `tests/test_mail_mutate_send_as.py`, `tests/test_cli_mail_sendas.py`

### Task 1.1: Executor TDD

**Implementation sketch** for `execute_send_as`:

```python
def execute_send_as(
    op: Operation,
    graph: GraphClient,
    logger: AuditLogger,
    *,
    before: dict[str, Any],
) -> MailResult:
    """POST /users/{from_upn}/sendMail (app-only).

    Audit records both the effective sender (the mailbox being sent
    as) and the authenticated principal (the app client_id).
    """
    from_upn = op.args["from_upn"]
    ub = f"/users/{from_upn}"  # forced app-only — caller must enforce
    try:
        message = build_message_payload(
            subject=op.args.get("subject", ""),
            body=op.args.get("body", ""),
            body_type=op.args.get("body_type", "text"),
            to=list(op.args.get("to", [])),
            cc=list(op.args.get("cc", []) or []),
            bcc=list(op.args.get("bcc", []) or []),
            importance=op.args.get("importance"),
            require_subject=True,
        )
    except BodyFormatError as e:
        log_mutation_start(
            logger, op_id=op.op_id, cmd="mail-sendas",
            args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
        )
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return MailResult(op_id=op.op_id, status="error", error=str(e))

    log_mutation_start(
        logger, op_id=op.op_id, cmd="mail-sendas",
        args=op.args, drive_id=op.drive_id, item_id=op.item_id, before=before,
    )
    payload = {"message": message, "saveToSentItems": True}
    try:
        graph.post(f"{ub}/sendMail", json=payload)
    except GraphError as e:
        log_mutation_end(
            logger, op_id=op.op_id, after=None, result="error", error=str(e),
        )
        return MailResult(op_id=op.op_id, status="error", error=str(e))

    after: dict[str, Any] = {
        "sent_at": _now_utc_iso(),
        "effective_sender": from_upn,
        "authenticated_principal": op.args.get("authenticated_principal", ""),
    }
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)
```

Note: like `execute_send_new`, this uses Graph's `/sendMail` POST — Graph treats this as a 202 Accepted with empty body, so `graph.post` is fine (the existing `execute_send_new` uses `graph.post`; if it actually uses `post_raw` for empty-body, match that). Inspect `mail/mutate/send.py:execute_send_new` to confirm.

**Tests** at `tests/test_mail_mutate_send_as.py` — 6 tests:
- `execute_send_as` POSTs to `/users/<from_upn>/sendMail` regardless of the `auth_mode` value (app-only is forced).
- Body payload is `{message: …, saveToSentItems: true}`.
- Audit `after` records both `effective_sender` and `authenticated_principal`.
- Body-format error returns `status="error"` without calling `graph.post`.
- Graph error on POST returns `status="error"`.
- Recipients flow through `build_message_payload` correctly (re-uses existing helper).

### Task 1.2: CLI TDD

**CLI surface:**
```
mail sendas <from-upn> --to <addr> [--to <addr>...] [--cc <addr>...] [--bcc <addr>...]
            --subject <s> [--body <s> | --body-file <p>] [--body-type text|html]
            [--importance low|normal|high] [--unsafe-scope] --confirm
```

**Behaviour:**
- `from-upn` is positional, the bare UPN of the mailbox to send as.
- Always app-only (this is the "send as another mailbox" action; delegated would just be `mail send`).
- `assert_mailbox_allowed(f"upn:{from_upn}", cfg, auth_mode="app-only", unsafe_scope=args.unsafe_scope)` — out-of-scope from-upns require `--unsafe-scope` plus the existing TTY confirmation. The safety helper does this; the CLI just calls it.
- `--confirm` required (this is irreversible by definition — you can't "unsend"). Without → exit 2.
- Build `Operation(action="mail.send.as", drive_id=from_upn, item_id="", args={...})` and call `execute_send_as`.
- Read `cfg.client_id` and pass into `op.args["authenticated_principal"]` so the executor records it.

**Tests** at `tests/test_cli_mail_sendas.py` — 6 tests:
- `mail sendas <upn> --to a@x.com --subject s --body b --confirm` → executor called with right `from_upn` + recipients + body.
- Without `--confirm` returns 2 with stderr.
- With `from_upn` in `allow_mailboxes` as `upn:<addr>` → `assert_mailbox_allowed` accepts; executor called.
- With `from_upn` NOT in `allow_mailboxes` and no `--unsafe-scope` → `ScopeViolation` propagates, CLI catches and exits 2.
- With `from_upn` NOT in `allow_mailboxes` AND `--unsafe-scope` → calls `_confirm_via_tty` (mock) and proceeds when "y".
- `--body-file` + `--body-type html` reads the file, passes correct body_type.

### Steps

1. Failing tests (both files).
2. Run, verify ImportError.
3. Implement `execute_send_as`. Confirm `graph.post` vs `graph.post_raw` choice by reading existing `execute_send_new`.
4. Implement `mail/cli/sendas.py`. Don't reuse `add_common_args` — sendas doesn't take `--mailbox` (the from-UPN is positional and means a different thing). Just define `--config`, the positional, the recipient flags, body flags, `--unsafe-scope`, `--confirm`.
5. Wire dispatcher: `mail/cli/__main__.py` add `elif verb == "sendas": from m365ctl.mail.cli.sendas import main as f`. Add `_USAGE` line:
   ```
   "  sendas       sendas <from-upn> --to <addr> ... (app-only; --unsafe-scope if out-of-scope)\n"
   ```
   Also add a top-level note that sendas is irreversible (no audit/undo because there's no inverse).
6. Bin wrapper `bin/mail-sendas` + `chmod +x`.
7. Quality gates: pytest (857 + ~12 = ~869), mypy 0, ruff clean.
8. Commit:
```
git add src/m365ctl/mail/mutate/send.py src/m365ctl/mail/cli/sendas.py \
        src/m365ctl/mail/cli/__main__.py bin/mail-sendas \
        tests/test_mail_mutate_send_as.py tests/test_cli_mail_sendas.py
git commit -m "feat(mail/sendas): app-only send-as with audit trail of effective_sender + authenticated_principal"
```

### Task 1.3: Mark `mail.send.as` as irreversible

Send-as is by nature irreversible (you can't recall a sent email). Register it in `mail/mutate/undo.py` with `register_irreversible`:

```python
dispatcher.register_irreversible(
    "mail.send.as",
    "Send-as is irreversible — the message is delivered. The audit log "
    "records both the effective_sender and the authenticated_principal "
    "for compliance.",
)
```

Add to `build_reverse_mail_operation` a branch that raises `Irreversible` for `cmd == "mail-sendas"`.

Test at `tests/test_mail_mutate_undo_send_as.py` — one test asserting the reverse-build raises `Irreversible`.

Quality gates, commit:
```
feat(mail/mutate/undo): register mail.send.as as irreversible
```

---

## Group 2 — Release 1.4.0

### Task 2.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.3.0 → 1.4.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.4.0 — Phase 13: send-as / on-behalf-of

### Added
- `m365ctl.mail.mutate.send.execute_send_as` — POST `/users/{from_upn}/sendMail` (app-only). Audit records both `effective_sender` (the mailbox being sent as) and `authenticated_principal` (the app `client_id`).
- CLI: `mail sendas <from-upn> --to <addr> ... --subject ... --body ... --confirm`. Bin wrapper `bin/mail-sendas`.
- Out-of-scope from-UPNs require `--unsafe-scope` plus a TTY confirmation, reusing the existing `assert_mailbox_allowed` flow.

### Irreversible
- `mail.send.as` is registered as irreversible in the undo dispatcher; `m365ctl undo <op-id>` returns a clear error citing the audit-log compliance fields.
```

- [ ] README Mail bullet:
```markdown
- **Send-as (Phase 13, 1.4):** `mail sendas <from-upn> --to <addr> ... --confirm`
  sends as another mailbox via app-only `/users/{upn}/sendMail`. Both the
  effective sender and the authenticated principal are audit-logged.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits.

### Task 2.2: Push, PR, merge, tag v1.4.0

Standard cadence.

---

## Self-review

**Spec coverage (§19 Phase 13):**
- ✅ `mail.compose.send_as(from_upn, …)` — implemented as `execute_send_as` in the established executor pattern.
- ✅ CLI: `mail sendas <upn> …` — G1.
- ✅ Audit log records both `effective_sender` and `authenticated_principal` — G1.1.
- ✅ Mandatory `--unsafe-scope` if `from_upn` not in `allow_mailboxes` — reuses `assert_mailbox_allowed`.
- ⚠️ Spec said bump to 0.15.0 sequentially; we bump to 1.4.0 because we shipped most of the spec already.

**Type consistency:** `MailResult` shape unchanged. Audit API matches Phase 6/8/9/12.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-25-phase-13-send-as.md`. Branch `phase-13-send-as` already off `main`.
