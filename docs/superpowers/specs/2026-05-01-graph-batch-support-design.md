# Graph `$batch` support — design

Status: approved 2026-05-01
Author: brainstormed with the user

## Goal

Adopt Microsoft Graph `$batch` (up to 20 sub-requests per HTTP call) inside
`m365ctl` to collapse the per-op round-trips that bulk plan execution and
read-side fan-out currently incur. Expected throughput improvement on bulk
plans: ~5× in the first cut (sequential dispatch); the API is shaped so a
later parallel-dispatch follow-up unlocks 15–20× without further call-site
changes.

## Non-goals

- **Parallel batch dispatch.** First cut is strictly sequential (one batch
  in flight). A `K`-in-flight knob is a follow-up.
- **Async / `httpx.AsyncClient` rewrite.** Stay synchronous. Today's
  `GraphClient` is sync; we keep that.
- **`/delta` page loops.** Each page depends on the prior page's
  `@odata.deltaLink` / `@odata.nextLink`; can't be batched across pages of a
  single delta stream.
- **Upload sessions.** Large attachment / large file uploads use signed URLs
  on a different host and stay on the existing `put_chunk` path.
- **`/search/query` calls.** Single POSTs already; no fan-out.
- **`mail.send`, `mail.draft`, attachment-upload flows.** Multi-step orchestration
  doesn't fit the homogeneous batch model. Single-op shape preserved.
- **OneDrive `mutate/clean.py` if it uses the pwsh shim.** Not on Graph.
- **A `--batch` CLI flag.** Always-on; the safety gates (`--confirm`, the
  N≥20 `/dev/tty` prompt) are upstream and unchanged.

## Scope

The change is broad: every place in `m365ctl` that issues N>1 Graph calls in
a natural fan-out shape becomes batched. Three families:

1. **Bulk mutate `--from-plan` execution** (mail and OneDrive).
2. **Pre-mutation `before`-state lookups** that today run a per-op `GET`
   ahead of each mutation.
3. **Read-side fan-out** (e.g., resolving N folder paths, fetching message
   metadata across N folders, listing permissions for N drive items).

## Architecture

### New primitive: `BatchSession` and `BatchFuture`

A new module `src/m365ctl/common/batch.py` adds two types. `GraphClient`
gains one method: `batch() -> BatchSession`.

```python
class BatchFuture:
    """Lazy handle for a Graph $batch sub-response.

    Once the owning session has flushed, .result() returns the parsed dict
    body (sub-response 2xx) or raises GraphError (sub-response 4xx/5xx).
    Calling .result() before the owning session flushes raises
    BatchUnflushedError.
    """
    def result(self) -> dict: ...
    def done(self) -> bool: ...


class BatchSession:
    """Buffers Graph calls into $batch requests of up to 20 sub-requests.

    Mirrors GraphClient's call surface: get / get_absolute / post / patch /
    delete. Each call returns a BatchFuture immediately. Flush triggers:
      - the 20th call is buffered (auto-flush);
      - the `with` block exits.

    .result() on an unflushed future raises BatchUnflushedError (programmer
    error). Callers using a BatchSession must structure code as
    "buffer everything, then resolve" — typically via the verb start/finish
    split below — rather than interleaving buffer + resolve.

    Shares the parent GraphClient's token_provider, transport, sleep, and
    max_attempts. Owns its own retry loop tuned for $batch envelopes.
    """
    def get(self, path: str, *, headers: dict | None = None) -> BatchFuture: ...
    def get_absolute(self, url: str, *, headers: dict | None = None) -> BatchFuture: ...
    def post(self, path: str, *, json: dict, headers: dict | None = None) -> BatchFuture: ...
    def patch(self, path: str, *, json_body: dict, headers: dict | None = None) -> BatchFuture: ...
    def delete(self, path: str) -> BatchFuture: ...

    def flush(self) -> None: ...    # explicit; usually not called

    def __enter__(self) -> "BatchSession": ...
    def __exit__(self, exc_type, exc, tb) -> None: ...   # flushes on exit
```

`GraphClient` adds:

```python
def batch(self) -> BatchSession: ...
```

### Flush mechanics

`BatchSession.flush()` builds a request envelope of the shape:

```json
{
  "requests": [
    {"id": "1", "method": "POST", "url": "me/messages/.../move",
     "body": {"destinationId": "..."}, "headers": {"Content-Type": "application/json"}},
    ...
  ]
}
```

It POSTs to `/$batch` and parses the `responses` array, which Graph may
return out of order — the session keys responses by `id` and resolves the
matching `BatchFuture`.

Sub-request URLs in the envelope are **relative to
`https://graph.microsoft.com/v1.0`** with no leading slash. The session's
`_normalize_path` helper accepts both `me/...` and `/me/...` from callers
and emits the canonical form.

The `Authorization` header is set on the outer `/$batch` POST only; the
session strips `Authorization` from any sub-request headers (defense in
depth — call sites shouldn't be passing it anyway).

### Per-sub transient retry

When a flush returns:

1. Partition responses by sub-status into `{ok, transient_fail, permanent_fail}`.
   Transient codes mirror `m365ctl.common.graph._TRANSIENT_CODES`
   (429, 500, 502, 503, 504).
2. Resolve `ok` and `permanent_fail` futures immediately.
3. If `transient_fail` is non-empty and `attempts_remaining > 0`:
   - Sleep `max(retry_after_seconds_of_transients, computed_backoff)` using
     the session's `sleep` callable.
   - Re-issue a smaller `/$batch` containing just the transient sub-requests,
     re-using their original ids.
   - Repeat until either all resolve or `max_attempts` is exhausted; on
     exhaustion, resolve the still-failing futures with their last
     `GraphError`.
4. The outer POST to `/$batch` is itself wrapped in
   `m365ctl.common.retry.with_retry`, so envelope-level 429 / 5xx are also
   handled (matching `GraphClient`'s existing behavior).

`Retry-After` is honored at both levels: the envelope header on
envelope-level transients, and each sub-response's `headers["Retry-After"]`
on per-sub transients.

### `_Resolved` wrapper for non-batched callers

To keep verbs uniform across batched and non-batched call sites, every
`GraphClient` method returns a `_Resolved` object that exposes the same
`.result()` shape as `BatchFuture` but resolves eagerly:

```python
class _Resolved:
    def __init__(self, value: dict | None = None, error: GraphError | None = None): ...
    def result(self) -> dict:
        if self._error: raise self._error
        return self._value
    def done(self) -> bool: return True
```

This is a behavior change at the Python type layer: `GraphClient.get(...)`
returns `_Resolved` instead of `dict`. Verbs always call `.result()`. HTTP
behavior is unchanged, so tests that mock `httpx` transports continue to
work as-is.

A `GraphCaller` `Protocol` is introduced (`common/graph.py`) so verbs and
helpers can declare they accept either `GraphClient` or `BatchSession`.

## Verb refactor (mutate verbs)

Each mutate verb is split into two halves so the bulk path can buffer-then-resolve:

```python
def start_move(op, client: GraphCaller, logger, *, before) -> tuple[BatchFuture, dict]:
    """Log `start`, buffer the request, return (future, after-projection)."""
    log_mutation_start(logger, op_id=op.op_id, ..., before=before)
    f = client.post(f"{ub}/messages/{op.item_id}/move",
                    json={"destinationId": op.args["destination_id"]})
    after = {"parent_folder_id": op.args["destination_id"]}
    return f, after


def finish_move(op, future, after, logger) -> MailResult:
    """Resolve future, log `end`, return MailResult."""
    try:
        future.result()
    except GraphError as e:
        log_mutation_end(logger, op_id=op.op_id, after=None, result="error", error=str(e))
        return MailResult(op_id=op.op_id, status="error", error=str(e))
    log_mutation_end(logger, op_id=op.op_id, after=after, result="ok")
    return MailResult(op_id=op.op_id, status="ok", after=after)


def execute_move(op, graph: GraphClient, logger, *, before) -> MailResult:
    """Single-op convenience for non-batched callers (e.g., --message-id mode).

    Since GraphClient.post returns an already-resolved _Resolved, finish_move's
    .result() is a no-op here — the HTTP call has already completed inside
    start_move's b.post(...).
    """
    f, after = start_move(op, graph, logger, before=before)
    return finish_move(op, f, after, logger)
```

This keeps single-op call sites (`mail/cli/move.py --message-id`) unchanged
at the CLI layer and preserves existing `test_execute_<verb>` tests.

Verbs touched:

- `mail/mutate/`: `move.py`, `delete.py`, `copy.py`, `categorize.py`,
  `flag.py`, `read.py`, `categories.py`, `clean.py`, `folders.py`,
  `forward.py`, `reply.py`, `attach.py`, `delegate.py`.
- `onedrive/mutate/`: `move.py`, `copy.py`, `delete.py`, `rename.py`,
  `label.py`.

Per-verb diff: extract the HTTP call into `start_<verb>` (returns the
future + after-projection), move the audit-end + MailResult construction
into `finish_<verb>`, keep `execute_<verb>` as a thin wrapper that calls
both. ~10–15 lines net per verb.

### Audit-log timing change

`log_mutation_start` still fires when the verb is invoked (before any
flush). `log_mutation_end` fires when `.result()` returns — i.e., after the
flush.

Today: per op, `start` and `end` records strictly interleave.
After:    in an N-op bulk loop, all N `start` records are appended during
          the buffering pass (with auto-flushes firing every 20 calls,
          interleaved between start records but not between start/end
          pairs), then all N `end` records are appended during the resolve
          pass.

Per-op `op_id` linkage between `start` and `end` records is preserved, so
log-replay tools and `m365ctl undo` continue to work. Crash safety is
preserved or improved (every `start` is durable before its corresponding
flush). Documented in `CHANGELOG.md`.

## Bulk plan executor (two-phase)

A new helper `execute_plan_in_batches` lands in `mail/cli/_bulk.py` (and a
sibling helper for OneDrive in `onedrive/cli/_common.py`):

```python
def execute_plan_in_batches(
    *,
    graph: GraphClient,
    logger: AuditLogger,
    ops: list[Operation],
    fetch_before: Callable[[GraphCaller, Operation], BatchFuture] | None,
    parse_before: Callable[[Operation, dict | None, GraphError | None], dict],
    start_op: Callable[..., tuple[BatchFuture, dict]],   # e.g., start_move
    finish_op: Callable[..., MailResult],                # e.g., finish_move
    on_result: Callable[[Operation, MailResult], None],
) -> int:
    # Phase 1: batch all `before` GETs (skipped if fetch_before is None).
    befores: dict[str, dict] = {}
    if fetch_before is not None:
        with graph.batch() as b:
            futures = [(op, fetch_before(b, op)) for op in ops]
        for op, f in futures:
            try:
                befores[op.op_id] = parse_before(op, f.result(), None)
            except GraphError as e:
                befores[op.op_id] = parse_before(op, None, e)

    # Phase 2: buffer all mutations under one session, then resolve.
    with graph.batch() as b:
        pending = [
            (op, *start_op(op, b, logger, before=befores.get(op.op_id, {})))
            for op in ops
        ]
    # `with` exit flushed; futures resolved (or hold GraphError).
    results: list[tuple[Operation, MailResult]] = [
        (op, finish_op(op, future, after, logger))
        for op, future, after in pending
    ]

    any_error = False
    for op, result in results:
        on_result(op, result)
        if result.status != "ok":
            any_error = True
    return 1 if any_error else 0
```

Each `cli/<verb>.py --from-plan` block collapses to a single
`execute_plan_in_batches` call passing the verb's `start_<verb>` and
`finish_<verb>`. Verbs that don't need a `before` GET (`mail.read`,
`mail.flag`) pass `fetch_before=None` and skip Phase 1 entirely.

CLI files refactored:
- `mail/cli/`: `move.py`, `delete.py`, `copy.py`, `categorize.py`,
  `flag.py`, `read.py`, `archive.py`, `snooze.py`, `focus.py`.
- `onedrive/cli/`: `move.py`, `copy.py`, `delete.py`, `rename.py`,
  `label.py`, `clean.py` (Graph path only).

Per-op failure isolation: a failed `before` GET leaves that op with
`before={}` (matching today's `try/except`); a failed mutation marks that
op `error` but does not stop the rest of the batch. Output ordering is
preserved.

## Read-side fan-out call sites

Each of these is a `for ... in ...:` loop wrapped in `with graph.batch()
as b:` plus a second pass that resolves futures. No external API changes.

| Site                                                      | Today                                            | After                                              |
| --------------------------------------------------------- | ------------------------------------------------ | -------------------------------------------------- |
| `mail/messages.py` `list_messages` over folders           | N sequential `GET /folders/{id}/messages`        | One batch of N first-page GETs; pages serial       |
| `mail/folders.py` `resolve_folder_path` called for a list | N×depth sequential lookups                       | One batch per depth tier                           |
| `mail/mutate/attach.py` per-message attachment listings   | N sequential `GET /messages/{id}/attachments`    | One batched listing pass                           |
| `mail/cli/export.py`, `mail/export/` body+headers fetch   | N sequential GETs                                | Batched in chunks of 20                            |
| `mail/triage/runner.py` per-message metadata fetch        | N sequential GETs                                | Batched in chunks of 20                            |
| `mail/catalog/` per-folder fan-out (non-delta paths only) | N sequential GETs per folder                     | Batched per first-page tier                        |
| `onedrive/cli/audit_sharing.py` per-item permissions      | N sequential `GET /items/{id}/permissions`       | Batched in chunks of 20                            |
| `onedrive/inventory.py` per-item metadata (non-delta)     | N sequential GETs                                | Batched in chunks of 20                            |

Sites with ≤2 calls in their natural shape are not converted. Pagination
within a single stream remains serial.

## Testing strategy

Existing pattern (`MockTransport` injected into `GraphClient`) is preserved.

- `tests/common/test_batch.py` (new) covers:
  - Buffering and auto-flush at 20.
  - `with` exit flush.
  - `.result()` on an unflushed future raises `BatchUnflushedError`.
  - Outer envelope + per-sub mixed 2xx/4xx/429 responses with both
    envelope-level and sub-response-level `Retry-After`.
  - Per-sub transient retry exhaustion behavior.
  - `Authorization` stripping from sub-request headers.
  - URL normalization (leading-slash and prefix stripping).
  - 204-style sub-responses (DELETE) with absent body.
- Existing verb tests stay unchanged; `_Resolved` is transparent.
- Each `--from-plan` integration test grows a sibling that asserts the
  transport received exactly `ceil(N/20)` POSTs to `/$batch` with the
  expected sub-request URLs and bodies.
- Each in-scope read-side fan-out site grows a sibling test asserting one
  `/$batch` envelope contains the expected sub-requests.

No live Graph testing required.

## Risks & mitigations

- **Sub-response shape variance.** 204 sub-responses omit `body`. The
  sub-response decoder must mirror `GraphClient._parse`'s "no content"
  branch and not parse absent bodies as `{}` accidentally.
- **`Retry-After` propagation.** Tested at both envelope and sub-response
  level.
- **Auth-header bleed-through.** `BatchSession` strips `Authorization` from
  sub-request headers.
- **URL normalization.** Helper accepts both leading-slash and bare paths;
  emits canonical form.
- **Throttling under load.** Graph counts each sub-request toward limits.
  Per-sub transient retry handles short bursts; sustained 429s surface as
  per-op errors, same as today.
- **Audit-log grouping behavior change.** Per Section "Audit-log timing
  change" above. `op_id` linkage preserved; `m365ctl undo` unaffected.
  Documented in `CHANGELOG.md`.

## File layout

New:

- `src/m365ctl/common/batch.py` — `BatchSession`, `BatchFuture`, helpers.
- `tests/common/test_batch.py`.

Modified:

- `src/m365ctl/common/graph.py` — `GraphClient.batch()`; methods return
  `_Resolved`; introduce `GraphCaller` Protocol.
- `src/m365ctl/mail/cli/_bulk.py` — `execute_plan_in_batches`.
- `src/m365ctl/onedrive/cli/_common.py` — OneDrive sibling helper.
- All in-scope `mail/cli/<verb>.py` and `onedrive/cli/<verb>.py`
  `--from-plan` blocks.
- All in-scope `mail/mutate/<verb>.py` and `onedrive/mutate/<verb>.py`
  files.
- Read-side fan-out sites listed above.
- `CHANGELOG.md`, `docs/` — release note + brief operator-facing section.

## Rollout

Single PR with the full change. The plan-file workflow plus dry-run already
provide the safety net; no feature flag required. CHANGELOG entry calls out
the audit-log grouping change.
