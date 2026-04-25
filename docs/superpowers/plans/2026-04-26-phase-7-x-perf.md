# Phase 7.x — Catalog Refresh Perf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Cut the wallclock for first-time `mail catalog refresh` against large folders. The 2026-04-25 smoke clocked the 33K-item Inbox at ~11 minutes; the two highest-leverage wins are:

1. **`$select` on `/messages/delta`** — Graph defaults to returning the full message (body, web links, attachment metadata, etc.). The catalog only needs ~19 fields. Slimming the payload cuts wire bytes ~80% and the parser/normalize work proportionally.
2. **DuckDB transaction batching** — wrap each round's upserts in `BEGIN`/`COMMIT`. DuckDB's per-statement transaction overhead matters at 100s-of-rows-per-round.

Both changes are non-functional (same data persisted) and only touch `_drain_delta`.

**Tech stack:** Existing primitives. No schema or API changes.

**Baseline:** `main` post-PR-#25 (85f8ffc), 926 passing tests, 0 mypy errors, ruff clean. Tag `v1.8.0`.

**Version bump:** 1.8.0 → 1.9.0.

---

## Group 1 — `$select` + transaction batching (one commit)

**Files:**
- Modify: `src/m365ctl/mail/catalog/crawl.py`
- Modify: `tests/test_mail_catalog_crawl.py`

### The selected fields

`normalize_message` reads exactly these Graph fields:
- `id`, `internetMessageId`, `conversationId`, `parentFolderId`
- `subject`, `from`, `toRecipients`, `ccRecipients`
- `receivedDateTime`, `sentDateTime`
- `isRead`, `isDraft`, `hasAttachments`, `importance`
- `flag`, `categories`, `inferenceClassification`
- `bodyPreview`, `webLink`
- `@removed` (tombstones — Graph always includes this; doesn't need explicit `$select`)

Default Graph response also includes `body` (full content), `bodyContentType`, attachment metadata, replyTo, sender, parent folder name, ETag, change-key, and a dozen more fields. Slimming saves real bytes.

The `$select` value:
```
id,internetMessageId,conversationId,parentFolderId,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,isRead,isDraft,hasAttachments,importance,flag,categories,inferenceClassification,bodyPreview,webLink
```

### Steps

- [ ] **Step 1: Failing tests** in `tests/test_mail_catalog_crawl.py`.

Add two new tests:

```python
def test_drain_delta_passes_select_on_first_call(tmp_path):
    """First call to /messages/delta carries $select; subsequent links don't override."""
    from m365ctl.mail.catalog.crawl import _drain_delta
    graph = MagicMock()
    graph.get_paginated.side_effect = [
        iter([([_msg("m1")], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
        iter([([], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _drain_delta(
            graph, conn,
            mailbox_upn="me", folder_id="fld-inbox", folder_path="Inbox",
            start_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
    # First call uses params={"$select": "<fields>"}; subsequent (deltaLink)
    # call uses no params (the link encodes them).
    first_kwargs = graph.get_paginated.call_args_list[0].kwargs
    assert "params" in first_kwargs
    assert "$select" in first_kwargs["params"]
    select_value = first_kwargs["params"]["$select"]
    # Sanity: must include the must-have fields normalize_message reads.
    for field in ("id", "internetMessageId", "from", "receivedDateTime",
                  "isRead", "bodyPreview", "ccRecipients"):
        assert field in select_value, f"{field!r} missing from $select"
    # Subsequent deltaLink call should NOT pass $select (the link encodes it).
    second_kwargs = graph.get_paginated.call_args_list[1].kwargs
    assert "params" not in second_kwargs or not second_kwargs.get("params")


def test_drain_delta_uses_transaction_per_round(tmp_path):
    """Each round wraps upserts in BEGIN/COMMIT for DuckDB throughput."""
    from m365ctl.mail.catalog.crawl import _drain_delta
    graph = MagicMock()
    graph.get_paginated.side_effect = [
        iter([
            ([_msg("m1"), _msg("m2"), _msg("m3")],
             "https://graph.microsoft.com/.../delta?token=DELTA1"),
        ]),
        iter([([], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
    ]

    # Wrap conn.execute calls in a list so we can inspect the order.
    with open_catalog(tmp_path / "m.duckdb") as conn:
        executed: list[str] = []
        original_execute = conn.execute
        def _spy(sql, *args, **kwargs):
            executed.append(sql.strip().split()[0].upper())   # first SQL keyword
            return original_execute(sql, *args, **kwargs)
        conn.execute = _spy   # type: ignore[method-assign]

        _drain_delta(
            graph, conn,
            mailbox_upn="me", folder_id="fld-inbox", folder_path="Inbox",
            start_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
    # Round 1: BEGIN, 3 INSERTs, COMMIT. Round 2: BEGIN, 0 INSERTs, COMMIT.
    assert executed.count("BEGIN") == 2
    assert executed.count("COMMIT") == 2
    # 3 message upserts in round 1, 0 in round 2.
    insert_count = sum(1 for s in executed if s == "INSERT")
    assert insert_count == 3
```

- [ ] **Step 2: Implement** in `src/m365ctl/mail/catalog/crawl.py`.

Add a module-level constant near the top:

```python
# Graph $select for /messages/delta — covers exactly the fields normalize_message reads.
# Default response is much heavier (body, attachments, ETags, etc.) — slimming
# saves wire bytes and parser work. ~80% payload reduction for typical mail.
_DELTA_SELECT = ",".join([
    "id", "internetMessageId", "conversationId", "parentFolderId",
    "subject", "from", "toRecipients", "ccRecipients",
    "receivedDateTime", "sentDateTime",
    "isRead", "isDraft", "hasAttachments", "importance",
    "flag", "categories", "inferenceClassification",
    "bodyPreview", "webLink",
])
```

In `_drain_delta`, replace the `if cursor.startswith("http"): … else: …` block with:

```python
        # First call to /messages/delta carries $select; deltaLink calls
        # already encode the select in the URL query, so we don't pass it
        # again (would just be ignored, but keeps logs clean).
        if cursor.startswith("http"):
            pages = graph.get_paginated(cursor, headers=headers)
        else:
            pages = graph.get_paginated(
                cursor,
                params={"$select": _DELTA_SELECT},
                headers=headers,
            )

        # Wrap the round's upserts in a single DuckDB transaction.
        # Per-statement commits add ~0.5ms each; batching cuts that
        # to once-per-round.
        conn.execute("BEGIN")
        try:
            for items, delta_link in pages:
                for raw in items:
                    row = normalize_message(mailbox_upn, raw, parent_folder_path=folder_path)
                    if row.get("parent_folder_id") is None:
                        row["parent_folder_id"] = folder_id
                    conn.execute(_UPSERT_MESSAGE, row)
                    seen += 1
                    round_items += 1
                if delta_link:
                    round_delta = delta_link
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
```

- [ ] **Step 3:** Quality gates.

- `uv run pytest tests/test_mail_catalog_crawl.py -v` — expect 6+ tests pass (existing 4 + 2 new).
- `uv run pytest --tb=line -q` — expect 926 + 2 = 928 passing, 1 skipped.
- `uv run mypy src/m365ctl` — 0 errors.
- `uv run ruff check` — clean.

- [ ] **Step 4: Commit:**
```
git add src/m365ctl/mail/catalog/crawl.py tests/test_mail_catalog_crawl.py
git commit -m "perf(mail/catalog): \$select + transaction batching in /messages/delta drain"
```

---

## Group 2 — Release 1.9.0

### Task 2.1: Bump + changelog + README + lockfile (2 commits)

- [ ] `pyproject.toml`: 1.8.0 → 1.9.0.

- [ ] Prepend CHANGELOG.md:

```markdown
## 1.9.0 — Phase 7.x: catalog refresh perf

### Performance
- `_drain_delta` now passes `$select` on the first `/messages/delta`
  call, listing only the ~19 fields `normalize_message` actually reads.
  Default Graph response is much heavier (body, attachment metadata,
  ETags, change-keys, sender, replyTo, …); slimming cuts wire payload
  ~80% and parser work proportionally. The 33K-item-Inbox first-time
  refresh that took ~11 minutes in the 2026-04-25 smoke is the target
  workload.
- DuckDB upserts in each round are now wrapped in a single `BEGIN`/
  `COMMIT` instead of per-statement implicit transactions. At 100s of
  rows per round this saves measurable per-call overhead.

### No behaviour change
- Same fields persisted to `mail_messages`. Existing catalogs continue
  to work unchanged. Schema unchanged. CLI unchanged.

### Caveat
Subsequent rounds resume from the deltaLink URL, which already encodes
the original `$select` — we don't re-pass it.
```

- [ ] README Mail bullet:
```markdown
- **Catalog refresh perf (Phase 7.x, 1.9):** `/messages/delta` now uses
  `$select` for the ~19 fields the catalog reads (~80% payload trim),
  and DuckDB upserts batch into one transaction per round. Targets
  first-time large-mailbox onboarding.
```

- [ ] `uv sync --all-extras`. Quality gates. Two release commits per the no-amend rule.

### Task 2.2: Push, PR, merge, tag v1.9.0

Standard cadence.

---

## Self-review

**Performance characterization:**
- The optimization is non-functional: same data persisted, same number of network round-trips, same crawl semantics (rounds, deltaLink resume, 410 restart).
- Wire-bytes reduction depends on average message body size — a 4 KB preview-only response vs a 50 KB body-included response is a ~92% cut. Real-world workload will sit somewhere in that range.
- Transaction batching wins are consistent (DuckDB's per-statement overhead is constant; batching kills it).
- **No live timing benchmark** in this PR — would need a controlled live mailbox. Next live smoke against the user's real Inbox will measure.

**Backwards compat:**
- Existing v1/v2 catalogs unchanged.
- Existing tests for `_drain_delta` continue to pass — both new tests are additive.
- The `crawl.py:_drain_delta` signature is unchanged.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-26-phase-7-x-perf.md`. Branch `phase-7-x-perf` already off `main`.
