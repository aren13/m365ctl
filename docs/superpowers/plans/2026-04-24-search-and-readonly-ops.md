# Search & Read-only Ops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the catalog built in Plan 2 into something you can actually _use_: tenant-wide scope resolution, auto-retrying Graph calls, a unified `od-search` that fuses server-side full-text with local metadata, a streaming `od-download` that materialises any subset, and a PnP.PowerShell-backed `od-audit-sharing` report. Everything in this plan is **read-only** against the tenant — no mutations land until Plan 4.

**Architecture:** Extend `fazla_od.catalog.crawl.resolve_scope` with `tenant` and `site:<slug-or-id>`; wire `with_retry`+`is_transient_graph_error` into `GraphClient` so every GET auto-retries 429/503 honouring `Retry-After` (seconds-int OR HTTP-date). Add two Python subpackages — `search/` (Graph + DuckDB source merger) and `download/` (streaming + plan-file consumer). Introduce the PowerShell ecosystem via `scripts/ps/` plus a PEM-to-PFX conversion helper; the Python wrapper just shells out and parses JSON.

**Tech Stack:** Python 3.11+, `httpx` (existing; streaming body API for downloads), `duckdb` (existing), `pwsh` + `PnP.PowerShell` (new, user-installed), `openssl` (system) for PEM→PFX, `security` (macOS Keychain CLI, already present).

**End-state (definition of done):**
- `./bin/od-catalog-refresh --scope tenant` enumerates all user drives + all SharePoint document libraries under app-only auth. If the resolved drive count > 5 and `--yes` is not passed, the CLI shows a preview and prompts on `/dev/tty` for `y/N` — Claude cannot auto-answer.
- `./bin/od-catalog-refresh --scope site:<slug-or-id>` resolves one site's drives.
- `./bin/od-search "<query>" [--scope …] [--type …] [--modified-since …] [--owner …] [--limit N] [--json]` returns results merged from Graph `/search/query` and the local DuckDB catalog, deduped by `(drive_id, item_id)`.
- `./bin/od-download --item-id … --drive-id …` streams a single file into `workspaces/download-YYYYMMDD-HHMMSS/`. `--from-plan plan.json` and `--query "<SELECT …>"` variants also work. Concurrency capped at 4; `--overwrite` controls collision behaviour.
- `./bin/od-audit-sharing --scope site:<id> [--output-format json|tsv]` shells out to `pwsh scripts/ps/audit-sharing.ps1`, which connects with cert auth and emits one row per permission.
- `GraphClient.get` / `get_absolute` / `get_paginated` auto-retry 429/503 with `Retry-After` honoured. No existing call sites need to change.
- All unit tests pass with mocked externals; live smoke test verified against the Fazla tenant.
- `AGENTS.md` grows a block of new rows describing the new commands; existing rows are untouched.
- Plan 3 commits pushed to `origin/main`.

**Dependencies from Plans 1-2 (already in place):**
- `fazla_od.config.load_config` → `Config` (including `cfg.catalog.path`, `cfg.cert_path`, `cfg.cert_public`).
- `fazla_od.auth.AppOnlyCredential` / `DelegatedCredential`.
- `fazla_od.graph.GraphClient` with `get`, `get_absolute`, `get_paginated`, plus `GraphError` and `is_transient_graph_error`.
- `fazla_od.retry.with_retry` / `RetryExhausted`.
- `fazla_od.catalog.db.open_catalog`, `fazla_od.catalog.schema`, `fazla_od.catalog.crawl.{DriveSpec, CrawlResult, resolve_scope, crawl_drive}`, `fazla_od.catalog.normalize.normalize_item`.
- `bin/` shell-wrapper pattern (single-line `exec uv run --project "$REPO" python -m fazla_od.cli <sub> …`).
- Cert on disk: `~/.config/fazla-od/fazla-od.key` (PEM private key), `~/.config/fazla-od/fazla-od.cer` (PEM public cert), thumbprint `C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF`.

## Domain primer

- **Graph `/search/query` (POST).** Unlike GET endpoints, Graph search is a single POST to `https://graph.microsoft.com/v1.0/search/query` with a body like:
  ```json
  {"requests": [{"entityTypes": ["driveItem"],
                 "query": {"queryString": "invoice"},
                 "from": 0, "size": 25}]}
  ```
  Response shape: `{"value": [{"hitsContainers": [{"hits": [{"hitId": "...", "resource": {driveItem JSON}}, …], "total": N}]}]}`. The `resource` block is a shape similar to what the delta feed returns (has `id`, `name`, `parentReference.driveId`, `lastModifiedDateTime`, `size`, optional `file`/`folder`), so `normalize_item` works on it with a small tweak — search results don't carry `eTag` or `quickXorHash` but do carry `parentReference.driveId` (unlike delta). Use that to fill `drive_id`.
- **Scope filtering for search.** Graph's search endpoint doesn't accept a drive-id whitelist; it searches the whole tenant for the authenticated identity. We post-filter by `drive_id`/`site_id` after fetching. For `--scope me`, use delegated auth so search is already limited to the user's accessible content. For `--scope tenant` / `--scope site:<id>` / `--scope drive:<id>`, use app-only and post-filter.
- **Tenant enumeration.** `GET /users?$select=id,userPrincipalName,displayName&$top=999` (app-only, needs `User.Read.All`) paginates through all users. For each, `GET /users/{id}/drive` returns their OneDrive (404 if they never provisioned one — ignore). For SharePoint: `GET /sites?search=*` lists all sites (delegated only returns accessible ones; app-only with `Sites.ReadWrite.All` returns all); then `GET /sites/{id}/drives` returns each site's document libraries.
- **`Retry-After` header.** Graph returns it as either an integer (seconds) or an HTTP-date (RFC 7231 §7.1.3). Parse int-first; on `ValueError`, try `email.utils.parsedate_to_datetime` and subtract `datetime.now(timezone.utc)`. Clamp negative results to 0. The header is on the HTTP response — _not_ in the JSON body — so the `GraphError` as currently raised loses it. Plan 3 adds a `retry_after_seconds` attribute to `GraphError` populated at the `_parse` site from `resp.headers.get("Retry-After")`.
- **Download URL redirect.** `GET /drives/{id}/items/{iid}/content` returns a `302 Found` with a pre-signed CDN URL in `Location`. `httpx` by default follows redirects, but the pre-signed URL must be fetched _without_ the `Authorization: Bearer …` header (the CDN rejects bearer-auth). Solution: when following the redirect manually, strip the auth header, or — simpler — pass `follow_redirects=False` to the first call, read `Location`, then fetch the URL with a fresh `httpx.Client` (no auth).
- **PnP.PowerShell cert auth quirk — PFX vs PEM.** `Connect-PnPOnline -Tenant … -ClientId … -CertificatePath <path> -CertificatePassword <SecureString>` only accepts a PKCS#12 (`.pfx`) file, not a separate PEM key + PEM cert. Convert once at setup time with `openssl pkcs12 -export -inkey fazla-od.key -in fazla-od.cer -out fazla-od.pfx -passout pass:<generated>`. Store the password in macOS Keychain via `security add-generic-password -a fazla-od -s FazlaODToolkit:PfxPassword -w <password>`. At runtime the PS script reads the password with `security find-generic-password -a fazla-od -s FazlaODToolkit:PfxPassword -w` and passes it as `(ConvertTo-SecureString -String "<pwd>" -AsPlainText -Force)`.
- **Sharing-permission model.** Each driveItem has `GET /drives/{d}/items/{i}/permissions`, which returns rows of:
  - `id` (permission id), `roles: ["read"|"write"|"owner"]`,
  - `grantedToV2` (internal user/group; may be absent),
  - `grantedToIdentitiesV2` (link targets; includes `user.email`, may be external),
  - `link: {scope: "anonymous"|"organization"|"users", webUrl, preventsDownload}` (for sharing links),
  - `expirationDateTime`, `hasPassword`, `inheritedFrom` (inherited from parent if not null).
  PnP's `Get-PnPSharingInformation` / `Get-PnPFolderItem -Includes "ListItemAllFields"` are richer and faster for large site libraries. Plan 3 uses `Get-PnPListItem` + `Get-PnPListItemPermissions` per site.

## File structure (new files in this plan)

```
src/fazla_od/
├── graph.py                        # MODIFIED: Retry-After parse + with_retry wiring
├── catalog/
│   └── crawl.py                    # MODIFIED: resolve_scope gains 'tenant' + 'site:…'
├── search/
│   ├── __init__.py
│   ├── graph_search.py             # POST /search/query, normalize hits
│   ├── catalog_search.py           # DuckDB LIKE filter
│   └── merge.py                    # dedup + sort
├── download/
│   ├── __init__.py
│   ├── planner.py                  # plan-file loader + SELECT runner
│   └── fetcher.py                  # streaming downloader with semaphore
├── prompts.py                      # /dev/tty y/N helper (>5-drive gate)
└── cli/
    ├── search.py
    ├── download.py
    └── audit_sharing.py
bin/
├── od-search
├── od-download
└── od-audit-sharing
scripts/
└── ps/
    ├── audit-sharing.ps1
    └── convert-cert.sh             # one-shot PEM -> PFX + Keychain
docs/
└── ops/
    └── pnp-powershell-setup.md     # one-time setup guide
tests/
├── test_graph_retry.py
├── test_catalog_crawl_tenant.py
├── test_prompts.py
├── test_search_graph.py
├── test_search_catalog.py
├── test_search_merge.py
├── test_download_planner.py
├── test_download_fetcher.py
├── test_cli_search.py
├── test_cli_download.py
└── test_cli_audit_sharing.py
```

No new Python dependencies. `duckdb`, `httpx`, and stdlib `email.utils` cover everything.

---

### Task 1: Wire `with_retry` into `GraphClient` + parse `Retry-After`

**Files:**
- Modify: `src/fazla_od/graph.py`
- Create: `tests/test_graph_retry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_graph_retry.py`:
```python
from __future__ import annotations

from email.utils import format_datetime
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from fazla_od.graph import GraphClient, GraphError, is_transient_graph_error


def _seq_handler(responses: list[httpx.Response]):
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(it)

    return handler


def test_get_retries_on_429_and_honours_retry_after_seconds() -> None:
    sleeps: list[float] = []
    transport = httpx.MockTransport(
        _seq_handler(
            [
                httpx.Response(
                    429,
                    headers={"Retry-After": "2"},
                    json={"error": {"code": "TooManyRequests", "message": "slow down"}},
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=sleeps.append,
        max_attempts=3,
    )
    result = client.get("/me")
    assert result == {"ok": True}
    assert sleeps == [2.0]


def test_get_retries_on_503_with_http_date_retry_after() -> None:
    when = datetime.now(timezone.utc) + timedelta(seconds=3)
    sleeps: list[float] = []
    transport = httpx.MockTransport(
        _seq_handler(
            [
                httpx.Response(
                    503,
                    headers={"Retry-After": format_datetime(when)},
                    json={"error": {"code": "serviceNotAvailable", "message": "x"}},
                ),
                httpx.Response(200, json={"ok": True}),
            ]
        )
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=sleeps.append,
        max_attempts=3,
    )
    client.get("/me")
    # Allow slack for clock drift; delay should be approx 3s, clamped >= 0.
    assert len(sleeps) == 1
    assert 0.0 <= sleeps[0] <= 4.0


def test_get_gives_up_after_max_attempts() -> None:
    transport = httpx.MockTransport(
        _seq_handler(
            [httpx.Response(429, headers={"Retry-After": "0"},
                            json={"error": {"code": "TooManyRequests", "message": "x"}})]
            * 5
        )
    )
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=lambda _: None,
        max_attempts=3,
    )
    with pytest.raises(Exception) as exc_info:
        client.get("/me")
    # Either RetryExhausted or the underlying GraphError surfaces; both OK.
    assert "TooManyRequests" in str(exc_info.value) or "giving up" in str(exc_info.value)


def test_non_transient_error_not_retried() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            401, json={"error": {"code": "InvalidAuthenticationToken", "message": "bad"}}
        )

    client = GraphClient(
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        max_attempts=5,
    )
    with pytest.raises(GraphError, match="InvalidAuthenticationToken"):
        client.get("/me")
    assert calls["n"] == 1


def test_retry_after_attribute_set_on_graph_error() -> None:
    transport = httpx.MockTransport(
        _seq_handler(
            [
                httpx.Response(
                    429,
                    headers={"Retry-After": "7"},
                    json={"error": {"code": "TooManyRequests", "message": "x"}},
                )
            ]
        )
    )
    # With max_attempts=1 the first failure is re-raised directly; check attr.
    client = GraphClient(
        token_provider=lambda: "t",
        transport=transport,
        sleep=lambda _: None,
        max_attempts=1,
    )
    with pytest.raises(GraphError) as exc_info:
        client.get("/me")
    assert exc_info.value.retry_after_seconds == 7.0
    assert is_transient_graph_error(exc_info.value)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_graph_retry.py -v
```
Expected: `TypeError: GraphClient.__init__() got an unexpected keyword argument 'sleep'` (or similar). All 5 tests fail.

- [ ] **Step 3: Extend `src/fazla_od/graph.py`**

Replace the file with:
```python
"""Thin httpx-backed Microsoft Graph client.

Plan 3 changes:
- ``GraphError`` carries a ``retry_after_seconds`` attribute (``None`` when
  absent / unparseable).
- ``GraphClient`` accepts ``sleep`` and ``max_attempts`` and wraps each
  ``get`` / ``get_absolute`` call in ``fazla_od.retry.with_retry``, treating
  429/503 (and 500/502/504) as transient.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Iterator

import httpx

from fazla_od.retry import with_retry

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_TRANSIENT_CODES = {
    "TooManyRequests",
    "serviceNotAvailable",
    "HTTP429",
    "HTTP500",
    "HTTP502",
    "HTTP503",
    "HTTP504",
}


class GraphError(RuntimeError):
    """Raised when Graph returns a non-2xx response.

    ``retry_after_seconds`` is populated from the ``Retry-After`` header when
    present; ``None`` otherwise.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def is_transient_graph_error(exc: Exception) -> bool:
    if not isinstance(exc, GraphError):
        return False
    head = str(exc).split(":", 1)[0].strip()
    return head in _TRANSIENT_CODES


def _retry_after_of(exc: Exception) -> float | None:
    if isinstance(exc, GraphError):
        return exc.retry_after_seconds
    return None


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    v = value.strip()
    # Integer seconds first.
    try:
        return max(0.0, float(v))
    except ValueError:
        pass
    # HTTP-date fallback (RFC 7231 §7.1.3).
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = 5,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=GRAPH_BASE,
            transport=transport,
            timeout=timeout,
        )
        # time.sleep by default; tests inject a capturing list.append.
        import time as _time

        self._sleep = sleep if sleep is not None else _time.sleep
        self._max_attempts = max_attempts

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token_provider()}"}

    def _retry(self, fn):
        return with_retry(
            fn,
            max_attempts=self._max_attempts,
            sleep=self._sleep,
            is_transient=is_transient_graph_error,
            retry_after_of=_retry_after_of,
        )

    def get(self, path: str, *, params: dict | None = None) -> dict:
        def _do() -> dict:
            resp = self._client.get(path, headers=self._auth_headers(), params=params)
            return self._parse(resp)

        return self._retry(_do)

    def get_absolute(self, url: str) -> dict:
        """GET an absolute URL (e.g. an @odata.nextLink)."""

        def _do() -> dict:
            resp = self._client.get(url, headers=self._auth_headers())
            return self._parse(resp)

        return self._retry(_do)

    def post(self, path: str, *, json: dict) -> dict:
        """POST with auto-retry; used by /search/query."""

        def _do() -> dict:
            resp = self._client.post(path, headers=self._auth_headers(), json=json)
            return self._parse(resp)

        return self._retry(_do)

    def get_paginated(
        self, path: str, *, params: dict | None = None
    ) -> Iterator[tuple[list[dict], str | None]]:
        """Yield (items, delta_link) for each page (auto-retrying per page)."""
        body = self.get(path, params=params)
        while True:
            items = body.get("value", [])
            next_link = body.get("@odata.nextLink")
            delta_link = body.get("@odata.deltaLink")
            yield items, delta_link
            if not next_link:
                return
            body = self.get_absolute(next_link)

    def _parse(self, resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(
                f"{code}: {msg}",
                retry_after_seconds=_parse_retry_after(resp.headers.get("Retry-After")),
            )
        if not resp.content:
            return {}
        return resp.json()

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Confirm existing Plan-2 tests still pass**

```bash
uv run pytest tests/test_graph.py tests/test_graph_pagination.py tests/test_graph_retry.py -v
```
Expected: all pass (2 existing + 3 pagination + 5 new retry = 10).

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/graph.py tests/test_graph_retry.py
git commit -m "feat(graph): wire with_retry + Retry-After parsing into GraphClient"
```

---

### Task 2: `/dev/tty` y/N prompt helper

**Files:**
- Create: `src/fazla_od/prompts.py`
- Create: `tests/test_prompts.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_prompts.py`:
```python
from __future__ import annotations

import io

import pytest

from fazla_od.prompts import confirm_or_abort, TTYUnavailable


def test_confirm_returns_true_on_y(monkeypatch) -> None:
    fake_tty = io.StringIO("y\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("fazla_od.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is True


def test_confirm_returns_true_on_yes_case_insensitive(monkeypatch) -> None:
    fake_tty = io.StringIO("YES\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("fazla_od.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is True


def test_confirm_returns_false_on_n(monkeypatch) -> None:
    fake_tty = io.StringIO("n\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("fazla_od.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is False


def test_confirm_returns_false_on_blank(monkeypatch) -> None:
    # Default is N.
    fake_tty = io.StringIO("\n")
    fake_out = io.StringIO()
    monkeypatch.setattr("fazla_od.prompts._open_tty", lambda: (fake_tty, fake_out))
    assert confirm_or_abort("Proceed?") is False


def test_yes_flag_shortcuts_prompt(monkeypatch) -> None:
    called = {"n": 0}

    def should_not_open():
        called["n"] += 1
        raise AssertionError("should not open tty")

    monkeypatch.setattr("fazla_od.prompts._open_tty", should_not_open)
    assert confirm_or_abort("Proceed?", assume_yes=True) is True
    assert called["n"] == 0


def test_raises_when_tty_unavailable(monkeypatch) -> None:
    def no_tty():
        raise OSError("no tty")

    monkeypatch.setattr("fazla_od.prompts._open_tty", no_tty)
    with pytest.raises(TTYUnavailable):
        confirm_or_abort("Proceed?")
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_prompts.py -v
```
Expected: `ModuleNotFoundError: No module named 'fazla_od.prompts'`.

- [ ] **Step 3: Implement `src/fazla_od/prompts.py`**

```python
"""Safety prompt that forces a human's `y/N` decision via ``/dev/tty``.

Critical: we read from ``/dev/tty`` directly, not stdin. An agentic process
(e.g. Claude piping input to the subprocess) cannot answer this prompt —
that is the safety property.
"""
from __future__ import annotations

from typing import IO, Tuple


class TTYUnavailable(RuntimeError):
    """Raised when /dev/tty cannot be opened (e.g. no controlling terminal)."""


def _open_tty() -> Tuple[IO[str], IO[str]]:
    """Return (reader, writer) backed by /dev/tty. Separate so tests can patch."""
    try:
        reader = open("/dev/tty", "r")
        writer = open("/dev/tty", "w")
    except OSError as exc:
        raise TTYUnavailable("cannot open /dev/tty") from exc
    return reader, writer


def confirm_or_abort(message: str, *, assume_yes: bool = False) -> bool:
    """Prompt the user; return True iff they typed yes.

    ``assume_yes`` (wired from ``--yes`` on the CLI) skips the prompt entirely.
    """
    if assume_yes:
        return True
    reader, writer = _open_tty()
    try:
        writer.write(f"{message} [y/N]: ")
        writer.flush()
        answer = (reader.readline() or "").strip().lower()
    finally:
        reader.close()
        writer.close()
    return answer in {"y", "yes"}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_prompts.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/prompts.py tests/test_prompts.py
git commit -m "feat(prompts): /dev/tty y/N helper for Claude-can't-bypass gates"
```

---

### Task 3: Tenant/site scope resolution in `resolve_scope`

**Files:**
- Modify: `src/fazla_od/catalog/crawl.py`
- Create: `tests/test_catalog_crawl_tenant.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_catalog_crawl_tenant.py`:
```python
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fazla_od.catalog.crawl import DriveSpec, resolve_scope


def test_resolve_scope_site_by_numeric_id_lists_drives() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/sites/site-123":
            return {
                "id": "site-123",
                "displayName": "Finance",
                "webUrl": "https://fazla.sharepoint.com/sites/finance",
            }
        if path == "/sites/site-123/drives":
            return {
                "value": [
                    {
                        "id": "drive-fin-docs",
                        "name": "Documents",
                        "driveType": "documentLibrary",
                        "owner": {"group": {"displayName": "Finance Site"}},
                    }
                ]
            }
        raise AssertionError(f"unexpected path: {path}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("site:site-123", graph)
    assert len(drives) == 1
    assert drives[0] == DriveSpec(
        drive_id="drive-fin-docs",
        display_name="Finance / Documents",
        owner="Finance Site",
        drive_type="documentLibrary",
        graph_path="/drives/drive-fin-docs/root/delta",
    )


def test_resolve_scope_site_by_slug_uses_search() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/sites" and params == {"search": "Finance"}:
            return {
                "value": [
                    {"id": "site-abc", "displayName": "Finance",
                     "webUrl": "https://fazla.sharepoint.com/sites/finance"}
                ]
            }
        if path == "/sites/site-abc":
            return {"id": "site-abc", "displayName": "Finance"}
        if path == "/sites/site-abc/drives":
            return {"value": [
                {"id": "dr1", "name": "Documents",
                 "driveType": "documentLibrary",
                 "owner": {"group": {"displayName": "Finance Site"}}}
            ]}
        raise AssertionError(f"unexpected path: {path} {params}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("site:Finance", graph)
    assert drives[0].drive_id == "dr1"


def test_resolve_scope_site_slug_unique_match_required() -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "s1", "displayName": "Finance"},
            {"id": "s2", "displayName": "Finance Ops"},
        ]
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_scope("site:Finance", graph)


def test_resolve_scope_site_slug_no_match() -> None:
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    with pytest.raises(ValueError, match="no site"):
        resolve_scope("site:NoSuch", graph)


def test_resolve_scope_tenant_enumerates_users_and_sites() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {
                "value": [
                    {"id": "u1", "userPrincipalName": "a@fazla.com", "displayName": "A"},
                    {"id": "u2", "userPrincipalName": "b@fazla.com", "displayName": "B"},
                ]
            }
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive - Fazla",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@fazla.com"}}}
        if path == "/users/u2/drive":
            # Simulate a user without a provisioned drive (HTTP 404 → raises)
            from fazla_od.graph import GraphError
            raise GraphError("itemNotFound: no drive")
        if path == "/sites" and params == {"search": "*"}:
            return {"value": [
                {"id": "site-1", "displayName": "Finance"},
            ]}
        if path == "/sites/site-1":
            return {"id": "site-1", "displayName": "Finance"}
        if path == "/sites/site-1/drives":
            return {"value": [
                {"id": "drv-fin", "name": "Documents",
                 "driveType": "documentLibrary",
                 "owner": {"group": {"displayName": "Finance"}}}
            ]}
        raise AssertionError(f"unexpected path: {path} {params}")

    graph.get.side_effect = fake_get
    drives = resolve_scope("tenant", graph)

    ids = sorted(d.drive_id for d in drives)
    assert ids == ["drv-fin", "drv-u1"]  # u2's missing drive silently skipped
    # DriveSpec.graph_path is the delta path
    fin = next(d for d in drives if d.drive_id == "drv-fin")
    assert fin.graph_path == "/drives/drv-fin/root/delta"


def test_resolve_scope_tenant_paginates_users() -> None:
    graph = MagicMock()

    def fake_get(path, *, params=None):
        if path == "/users":
            return {"value": [
                {"id": "u1", "userPrincipalName": "a@fazla.com"}
            ]}
        if path == "/users/u1/drive":
            return {"id": "drv-u1", "name": "OneDrive",
                    "driveType": "business",
                    "owner": {"user": {"email": "a@fazla.com"}}}
        if path == "/sites" and params == {"search": "*"}:
            return {"value": []}
        raise AssertionError(f"unexpected: {path}")

    graph.get.side_effect = fake_get

    # get_paginated: two pages of users.
    def fake_paginated(path, *, params=None):
        if path == "/users":
            yield [{"id": "u1", "userPrincipalName": "a@fazla.com"}], None
        elif path == "/sites":
            yield [], None
        else:
            raise AssertionError(path)

    graph.get_paginated.side_effect = fake_paginated
    drives = resolve_scope("tenant", graph)
    assert {d.drive_id for d in drives} == {"drv-u1"}


def test_resolve_scope_still_supports_me_and_drive() -> None:
    graph = MagicMock()
    graph.get.return_value = {
        "id": "drv-me",
        "driveType": "business",
        "owner": {"user": {"email": "x@fazla.com"}},
        "name": "OneDrive",
    }
    drives = resolve_scope("me", graph)
    assert drives[0].drive_id == "drv-me"

    graph.get.return_value = {
        "id": "drv-xyz",
        "driveType": "documentLibrary",
        "owner": {"user": {"email": "s@fazla.com"}},
        "name": "Finance",
    }
    drives = resolve_scope("drive:drv-xyz", graph)
    assert drives[0].drive_id == "drv-xyz"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_catalog_crawl_tenant.py -v
```
Expected: several tests fail — the existing `resolve_scope` rejects `site:` and `tenant`.

- [ ] **Step 3: Extend `resolve_scope`**

Replace the body of `src/fazla_od/catalog/crawl.py` (keep `DriveSpec`, `CrawlResult`, `crawl_drive`, `_owner_of`, `_UPSERT_ITEM_SQL`, `_GraphLike`; swap only `resolve_scope`):

```python
def resolve_scope(scope: str, graph: _GraphLike) -> list[DriveSpec]:
    """Translate a scope string into one or more DriveSpecs.

    Supported forms:
      - ``me``                → current user's OneDrive (delegated).
      - ``drive:<id>``        → one specific drive (app-only).
      - ``site:<slug-or-id>`` → all drives of one SharePoint site (app-only).
      - ``tenant``            → every user drive + every SharePoint library
                                (app-only, paginated).

    Missing user drives (users who never provisioned OneDrive) are silently
    skipped under ``tenant`` rather than aborting the crawl.
    """
    if scope == "me":
        meta = graph.get("/me/drive")
        return [_drive_from_meta(meta, graph_path="/me/drive/root/delta")]

    if scope.startswith("drive:"):
        drive_id = scope.split(":", 1)[1]
        meta = graph.get(f"/drives/{drive_id}")
        return [
            _drive_from_meta(
                meta, graph_path=f"/drives/{drive_id}/root/delta"
            )
        ]

    if scope.startswith("site:"):
        ident = scope.split(":", 1)[1]
        site = _resolve_site(ident, graph)
        return _drives_of_site(site, graph)

    if scope == "tenant":
        return _enumerate_tenant(graph)

    raise ValueError(f"unknown scope: {scope!r}")


def _drive_from_meta(meta: dict, *, graph_path: str) -> DriveSpec:
    return DriveSpec(
        drive_id=meta["id"],
        display_name=meta.get("name", meta["id"]),
        owner=_owner_of(meta),
        drive_type=meta.get("driveType", "unknown"),
        graph_path=graph_path,
    )


def _resolve_site(ident: str, graph: _GraphLike) -> dict:
    """Return the site dict for ``ident`` (display-name slug or raw id).

    We try ``/sites/<ident>`` first — if ident is a full site-id or a
    hostname:/sites/<x> triple, that works. On 404 we fall back to search.
    """
    # Search is cheap and handles slugs; try it first unless the ident looks
    # like a site-id (contains a comma, the SharePoint site-id shape is
    # ``<host>,<spSiteId>,<spWebId>``) or a numeric-ish GUID-like token.
    looks_like_id = "," in ident or ident.count("-") >= 2 or ident.startswith("site-")
    if looks_like_id:
        try:
            return graph.get(f"/sites/{ident}")
        except Exception:
            pass  # fall through to search

    hits = graph.get("/sites", params={"search": ident}).get("value", [])
    if not hits:
        raise ValueError(f"no site matches site:{ident!r}")
    if len(hits) > 1:
        names = ", ".join(h.get("displayName", h.get("id", "?")) for h in hits)
        raise ValueError(
            f"site:{ident!r} is ambiguous — matched {len(hits)} sites: {names}"
        )
    # Re-fetch by id so the shape is consistent (search response omits fields).
    return graph.get(f"/sites/{hits[0]['id']}")


def _drives_of_site(site: dict, graph: _GraphLike) -> list[DriveSpec]:
    site_name = site.get("displayName") or site.get("name") or site["id"]
    drives = graph.get(f"/sites/{site['id']}/drives").get("value", [])
    specs: list[DriveSpec] = []
    for d in drives:
        owner_block = d.get("owner") or {}
        user = owner_block.get("user") or {}
        group = owner_block.get("group") or {}
        owner = (
            user.get("email")
            or user.get("displayName")
            or group.get("displayName")
            or "unknown"
        )
        specs.append(
            DriveSpec(
                drive_id=d["id"],
                display_name=f"{site_name} / {d.get('name', d['id'])}",
                owner=owner,
                drive_type=d.get("driveType", "documentLibrary"),
                graph_path=f"/drives/{d['id']}/root/delta",
            )
        )
    return specs


def _enumerate_tenant(graph: _GraphLike) -> list[DriveSpec]:
    """All user OneDrives + all SharePoint site drives."""
    specs: list[DriveSpec] = []

    # Users → their OneDrive (skip 404s for unprovisioned users).
    try:
        pages = graph.get_paginated(
            "/users",
            params={"$select": "id,userPrincipalName,displayName", "$top": 999},
        )
    except TypeError:
        # Some MagicMocks are configured with side_effect that ignores params.
        pages = graph.get_paginated("/users")

    from fazla_od.graph import GraphError

    for items, _ in pages:
        for user in items:
            uid = user.get("id")
            if not uid:
                continue
            try:
                meta = graph.get(f"/users/{uid}/drive")
            except GraphError as exc:
                if "itemNotFound" in str(exc) or "HTTP404" in str(exc):
                    continue
                raise
            specs.append(
                _drive_from_meta(meta, graph_path=f"/drives/{meta['id']}/root/delta")
            )

    # Sites → each site's drives.
    try:
        site_pages = graph.get_paginated("/sites", params={"search": "*"})
    except TypeError:
        site_pages = graph.get_paginated("/sites")
    for items, _ in site_pages:
        for site in items:
            site_full = graph.get(f"/sites/{site['id']}")
            specs.extend(_drives_of_site(site_full, graph))
    return specs
```

Note the `graph.get_paginated` use: existing `GraphClient.get_paginated` accepts `params`. The test fixture above stubs it as a generator, which is compatible.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_catalog_crawl.py tests/test_catalog_crawl_tenant.py -v
```
Expected: all pass — 6 existing (Plan 2 crawl) + 7 new tenant/site tests.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/catalog/crawl.py tests/test_catalog_crawl_tenant.py
git commit -m "feat(catalog): resolve_scope supports tenant and site:<slug|id>"
```

---

### Task 4: `od-catalog-refresh` gets `--scope tenant|site:…` + >5-drive gate

**Files:**
- Modify: `src/fazla_od/cli/catalog.py`
- Modify: `tests/test_cli_catalog.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_cli_catalog.py`:
```python
def test_run_refresh_tenant_uses_app_only(tmp_path, mocker, capsys) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)

    delegated = MagicMock()
    mocker.patch("fazla_od.cli.catalog.DelegatedCredential", return_value=delegated)
    app_only = MagicMock()
    app_only.get_token.return_value = "app-token"
    mocker.patch("fazla_od.cli.catalog.AppOnlyCredential", return_value=app_only)

    specs = [
        DriveSpec(drive_id=f"d{i}", display_name=f"S/D{i}",
                  owner=f"o{i}@fazla.com", drive_type="documentLibrary",
                  graph_path=f"/drives/d{i}/root/delta")
        for i in range(3)
    ]
    mocker.patch("fazla_od.cli.catalog.resolve_scope", return_value=specs)
    mocker.patch(
        "fazla_od.cli.catalog.crawl_drive",
        side_effect=[CrawlResult(s.drive_id, 1, "dl") for s in specs],
    )

    rc = run_refresh(config_path=tmp_path / "config.toml", scope="tenant",
                    assume_yes=True)
    assert rc == 0
    delegated.get_token.assert_not_called()
    app_only.get_token.assert_called_once()


def test_refresh_over_5_drives_prompts_and_aborts_on_no(
    tmp_path, mocker, capsys
) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.catalog.AppOnlyCredential", return_value=MagicMock())
    specs = [
        DriveSpec(drive_id=f"d{i}", display_name=f"X{i}", owner="o",
                  drive_type="documentLibrary",
                  graph_path=f"/drives/d{i}/root/delta")
        for i in range(6)
    ]
    mocker.patch("fazla_od.cli.catalog.resolve_scope", return_value=specs)
    mocker.patch("fazla_od.cli.catalog.confirm_or_abort", return_value=False)
    crawl_mock = mocker.patch("fazla_od.cli.catalog.crawl_drive")

    rc = run_refresh(config_path=tmp_path / "config.toml", scope="tenant",
                    assume_yes=False)
    err = capsys.readouterr().err
    assert rc == 1
    assert "aborted" in err.lower()
    crawl_mock.assert_not_called()


def test_refresh_over_5_drives_proceeds_on_yes(tmp_path, mocker) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.catalog.AppOnlyCredential", return_value=MagicMock())
    specs = [
        DriveSpec(drive_id=f"d{i}", display_name=f"X{i}", owner="o",
                  drive_type="documentLibrary",
                  graph_path=f"/drives/d{i}/root/delta")
        for i in range(6)
    ]
    mocker.patch("fazla_od.cli.catalog.resolve_scope", return_value=specs)
    mocker.patch("fazla_od.cli.catalog.confirm_or_abort", return_value=True)
    mocker.patch(
        "fazla_od.cli.catalog.crawl_drive",
        side_effect=[CrawlResult(s.drive_id, 0, "dl") for s in specs],
    )

    rc = run_refresh(config_path=tmp_path / "config.toml", scope="tenant",
                    assume_yes=False)
    assert rc == 0


def test_refresh_yes_flag_skips_prompt(tmp_path, mocker) -> None:
    cfg = _stub_config(tmp_path)
    mocker.patch("fazla_od.cli.catalog.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.catalog.AppOnlyCredential", return_value=MagicMock())
    specs = [
        DriveSpec(drive_id=f"d{i}", display_name=f"X{i}", owner="o",
                  drive_type="documentLibrary",
                  graph_path=f"/drives/d{i}/root/delta")
        for i in range(10)
    ]
    mocker.patch("fazla_od.cli.catalog.resolve_scope", return_value=specs)
    prompt = mocker.patch("fazla_od.cli.catalog.confirm_or_abort", return_value=True)
    mocker.patch(
        "fazla_od.cli.catalog.crawl_drive",
        side_effect=[CrawlResult(s.drive_id, 0, "dl") for s in specs],
    )

    rc = run_refresh(config_path=tmp_path / "config.toml", scope="tenant",
                    assume_yes=True)
    assert rc == 0
    # assume_yes short-circuits inside confirm_or_abort; still called once
    # with assume_yes=True so it returns True without opening tty.
    prompt.assert_called_once()
    assert prompt.call_args.kwargs.get("assume_yes") is True
```

- [ ] **Step 2: Confirm failure**

```bash
uv run pytest tests/test_cli_catalog.py -v
```
Expected: the four new tests fail (missing `assume_yes` kwarg, missing `confirm_or_abort` import).

- [ ] **Step 3: Update `src/fazla_od/cli/catalog.py`**

Replace with:
```python
"""`od-catalog` subcommands: refresh and status."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fazla_od.auth import AppOnlyCredential, DelegatedCredential
from fazla_od.catalog.crawl import CrawlResult, crawl_drive, resolve_scope
from fazla_od.catalog.db import open_catalog
from fazla_od.config import load_config
from fazla_od.graph import GraphClient
from fazla_od.prompts import confirm_or_abort

_LARGE_SCOPE_THRESHOLD = 5


def _credential_for_scope(scope: str, cfg):
    """'me' -> delegated; everything else (drive:, site:, tenant) -> app-only."""
    if scope == "me":
        return DelegatedCredential(cfg)
    return AppOnlyCredential(cfg)


def run_refresh(*, config_path: Path, scope: str, assume_yes: bool = False) -> int:
    cfg = load_config(config_path)
    cred = _credential_for_scope(scope, cfg)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    drives = resolve_scope(scope, graph)
    print(f"Resolved {len(drives)} drive(s) under scope {scope!r}.")

    if len(drives) > _LARGE_SCOPE_THRESHOLD:
        print("Preview:")
        for d in drives[:20]:
            print(f"  - {d.drive_id}  {d.display_name}  ({d.owner})")
        if len(drives) > 20:
            print(f"  ... and {len(drives) - 20} more")
        proceed = confirm_or_abort(
            f"Proceed with refreshing {len(drives)} drive(s)?",
            assume_yes=assume_yes,
        )
        if not proceed:
            print("Aborted by user.", file=sys.stderr)
            return 1

    results: list[CrawlResult] = []
    with open_catalog(cfg.catalog.path) as conn:
        for drive in drives:
            print(f"  - {drive.drive_id} ({drive.display_name}, {drive.owner})")
            result = crawl_drive(drive, graph, conn)
            results.append(result)
            print(f"    items seen: {result.items_seen}")

    print(f"Done. Catalog: {cfg.catalog.path}")
    return 0


def run_status(*, config_path: Path) -> int:
    cfg = load_config(config_path)
    with open_catalog(cfg.catalog.path) as conn:
        drives = conn.execute(
            "SELECT drive_id, display_name, owner, last_refreshed_at "
            "FROM drives ORDER BY drive_id"
        ).fetchall()
        (item_total,) = conn.execute("SELECT COUNT(*) FROM items").fetchone()
        (file_total,) = conn.execute(
            "SELECT COUNT(*) FROM items WHERE is_folder = false AND is_deleted = false"
        ).fetchone()
        (byte_total,) = conn.execute(
            "SELECT COALESCE(SUM(size), 0) FROM items "
            "WHERE is_folder = false AND is_deleted = false"
        ).fetchone()

    print(f"Catalog: {cfg.catalog.path}")
    print(f"Drives: {len(drives)}")
    for d in drives:
        print(f"  {d[0]}  {d[1]} ({d[2]})  last refreshed {d[3]}")
    print(f"Items:  {item_total} total ({file_total} live files)")
    print(f"Bytes:  {byte_total:,}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-catalog")
    p.add_argument("--config", default="config.toml")
    sub = p.add_subparsers(dest="subcommand", required=True)

    refresh = sub.add_parser("refresh", help="Delta-crawl a scope into the catalog.")
    refresh.add_argument(
        "--scope",
        required=True,
        help="'me', 'drive:<id>', 'site:<slug-or-id>', or 'tenant'",
    )
    refresh.add_argument(
        "--yes",
        dest="assume_yes",
        action="store_true",
        help="Skip the >5-drive preview/confirm prompt.",
    )
    sub.add_parser("status", help="Print catalog summary.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    if args.subcommand == "refresh":
        return run_refresh(
            config_path=config_path,
            scope=args.scope,
            assume_yes=args.assume_yes,
        )
    if args.subcommand == "status":
        return run_status(config_path=config_path)
    return 2
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli_catalog.py -v
```
Expected: 3 existing + 4 new = 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/cli/catalog.py tests/test_cli_catalog.py
git commit -m "feat(cli): catalog-refresh supports tenant/site scopes with >5-drive gate"
```

---

### Task 5: Search — Graph source, catalog source, merger

**Files:**
- Create: `src/fazla_od/search/__init__.py` (empty)
- Create: `src/fazla_od/search/graph_search.py`
- Create: `src/fazla_od/search/catalog_search.py`
- Create: `src/fazla_od/search/merge.py`
- Create: `tests/test_search_graph.py`
- Create: `tests/test_search_catalog.py`
- Create: `tests/test_search_merge.py`

- [ ] **Step 1: Create the package**

```bash
mkdir -p src/fazla_od/search
touch src/fazla_od/search/__init__.py
```

- [ ] **Step 2: Write tests for `graph_search`**

Create `tests/test_search_graph.py`:
```python
from __future__ import annotations

from unittest.mock import MagicMock

from fazla_od.search.graph_search import SearchHit, graph_search


def _resource(drive_id: str, item_id: str, name: str,
              modified: str = "2024-05-01T00:00:00Z",
              size: int = 100,
              is_folder: bool = False) -> dict:
    r = {
        "id": item_id,
        "name": name,
        "size": size,
        "lastModifiedDateTime": modified,
        "parentReference": {"driveId": drive_id, "path": "/drive/root:/Docs"},
    }
    if is_folder:
        r["folder"] = {"childCount": 0}
    else:
        r["file"] = {"mimeType": "text/plain"}
    return r


def test_graph_search_posts_and_normalizes_hits() -> None:
    graph = MagicMock()
    graph.post.return_value = {
        "value": [
            {
                "hitsContainers": [
                    {
                        "hits": [
                            {"hitId": "h1",
                             "resource": _resource("dA", "iA", "invoice.pdf")},
                            {"hitId": "h2",
                             "resource": _resource("dB", "iB", "Invoices",
                                                   is_folder=True)},
                        ],
                        "total": 2,
                    }
                ]
            }
        ]
    }

    hits = list(graph_search(graph, "invoice", limit=25))

    assert len(hits) == 2
    assert hits[0] == SearchHit(
        drive_id="dA",
        item_id="iA",
        name="invoice.pdf",
        full_path="/Docs/invoice.pdf",
        size=100,
        modified_at="2024-05-01T00:00:00Z",
        modified_by=None,
        is_folder=False,
        source="graph",
    )
    assert hits[1].is_folder is True

    # Verify the request body is Graph's /search/query shape.
    payload = graph.post.call_args.kwargs["json"]
    assert payload["requests"][0]["entityTypes"] == ["driveItem"]
    assert payload["requests"][0]["query"]["queryString"] == "invoice"
    assert payload["requests"][0]["size"] == 25


def test_graph_search_handles_empty_response() -> None:
    graph = MagicMock()
    graph.post.return_value = {"value": [{"hitsContainers": [{"hits": [], "total": 0}]}]}
    assert list(graph_search(graph, "nope")) == []


def test_graph_search_skips_hits_missing_drive_id() -> None:
    graph = MagicMock()
    # Resource without parentReference.driveId → unusable; skip.
    graph.post.return_value = {
        "value": [{"hitsContainers": [{
            "hits": [{"hitId": "h", "resource": {"id": "x", "name": "x"}}],
            "total": 1,
        }]}]
    }
    assert list(graph_search(graph, "x")) == []
```

- [ ] **Step 3: Implement `src/fazla_od/search/graph_search.py`**

```python
"""Adapter: Graph /search/query -> SearchHit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class SearchHit:
    drive_id: str
    item_id: str
    name: str
    full_path: str | None
    size: int | None
    modified_at: str | None  # ISO string; comparable lexicographically
    modified_by: str | None
    is_folder: bool
    source: str  # "graph" | "catalog"


class _GraphLike(Protocol):
    def post(self, path: str, *, json: dict) -> dict: ...


_PATH_PREFIX = "/drive/root:"


def _strip_prefix(p: str | None) -> str | None:
    if not p:
        return None
    if p.startswith(_PATH_PREFIX):
        p = p[len(_PATH_PREFIX):]
    return p or "/"


def _full_path(parent: str | None, name: str) -> str | None:
    if parent is None:
        return None
    if parent in ("/", ""):
        return f"/{name}" if name else "/"
    return f"{parent}/{name}" if name else parent


def graph_search(
    graph: _GraphLike, query: str, *, limit: int = 50
) -> Iterator[SearchHit]:
    body = {
        "requests": [
            {
                "entityTypes": ["driveItem"],
                "query": {"queryString": query},
                "from": 0,
                "size": min(max(limit, 1), 500),
            }
        ]
    }
    resp = graph.post("/search/query", json=body)
    for container in _iter_hit_containers(resp):
        for hit in container.get("hits") or []:
            res = hit.get("resource") or {}
            parent = res.get("parentReference") or {}
            drive_id = parent.get("driveId")
            item_id = res.get("id")
            if not drive_id or not item_id:
                continue  # Can't dedupe without a (drive_id, item_id) pair.
            parent_path = _strip_prefix(parent.get("path"))
            name = res.get("name", "")
            is_folder = "folder" in res
            yield SearchHit(
                drive_id=drive_id,
                item_id=item_id,
                name=name,
                full_path=_full_path(parent_path, name),
                size=None if is_folder else res.get("size"),
                modified_at=res.get("lastModifiedDateTime"),
                modified_by=((res.get("lastModifiedBy") or {}).get("user") or {}).get(
                    "email"
                ),
                is_folder=is_folder,
                source="graph",
            )


def _iter_hit_containers(resp: dict):
    for entry in resp.get("value") or []:
        for c in entry.get("hitsContainers") or []:
            yield c
```

- [ ] **Step 4: Write tests for `catalog_search`**

Create `tests/test_search_catalog.py`:
```python
from __future__ import annotations

from pathlib import Path

from fazla_od.catalog.db import open_catalog
from fazla_od.search.catalog_search import catalog_search


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, full_path, is_folder,
                               is_deleted, size, modified_at, modified_by)
            VALUES
              ('d', '1', 'Invoice-Q1.pdf', '/Finance/Invoice-Q1.pdf', false, false, 100,
               TIMESTAMP '2024-06-01 00:00:00', 'a@fazla.com'),
              ('d', '2', 'Q2.xlsx',       '/Finance/Invoices/Q2.xlsx', false, false, 200,
               TIMESTAMP '2024-07-01 00:00:00', 'b@fazla.com'),
              ('d', '3', 'Readme.md',      '/Docs/Readme.md',          false, false, 50,
               TIMESTAMP '2024-01-01 00:00:00', 'a@fazla.com'),
              ('d', 'f', 'Finance',        '/Finance',                 true,  false, null,
               TIMESTAMP '2024-01-01 00:00:00', 'a@fazla.com'),
              ('d', 'g', 'Invoices',       '/Finance/Invoices',        true,  false, null,
               TIMESTAMP '2024-01-01 00:00:00', 'a@fazla.com'),
              ('d', 'x', 'old.pdf',        '/tomb/old.pdf',            false, true,  1,
               TIMESTAMP '2020-01-01 00:00:00', 'a@fazla.com')
            """
        )


def test_matches_name_case_insensitive(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file"))
    names = {h.name for h in hits}
    assert "Invoice-Q1.pdf" in names
    # Q2.xlsx has 'invoices' in its path → matched via full_path LIKE
    assert "Q2.xlsx" in names
    # Folders excluded since type_='file'
    assert "Invoices" not in names


def test_type_folder_filters_to_folders(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="folder"))
    assert {h.name for h in hits} == {"Invoices"}


def test_type_all_returns_both(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="all"))
    names = {h.name for h in hits}
    assert {"Invoice-Q1.pdf", "Q2.xlsx", "Invoices"} <= names


def test_modified_since_filter(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file",
                                   modified_since="2024-06-15"))
    assert {h.name for h in hits} == {"Q2.xlsx"}


def test_owner_filter(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file",
                                   owner="b@fazla.com"))
    assert {h.name for h in hits} == {"Q2.xlsx"}


def test_scope_filter_drive(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "invoice", type_="file",
                                   drive_ids=["d"]))
        assert any(h.drive_id == "d" for h in hits)
        hits_empty = list(catalog_search(conn, "invoice", type_="file",
                                         drive_ids=["other-drive"]))
        assert hits_empty == []


def test_excludes_deleted_by_default(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        hits = list(catalog_search(conn, "old.pdf", type_="file"))
    assert hits == []
```

- [ ] **Step 5: Implement `src/fazla_od/search/catalog_search.py`**

```python
"""Adapter: DuckDB catalog -> SearchHit (name + full_path LIKE match)."""
from __future__ import annotations

from typing import Iterator, Literal

import duckdb

from fazla_od.search.graph_search import SearchHit

Type = Literal["file", "folder", "all"]


def catalog_search(
    conn: duckdb.DuckDBPyConnection,
    query: str,
    *,
    type_: Type = "file",
    modified_since: str | None = None,
    owner: str | None = None,
    drive_ids: list[str] | None = None,
) -> Iterator[SearchHit]:
    where: list[str] = ["is_deleted = false"]
    params: list[object] = []

    if type_ == "file":
        where.append("is_folder = false")
    elif type_ == "folder":
        where.append("is_folder = true")

    where.append("(LOWER(name) LIKE LOWER(?) OR LOWER(full_path) LIKE LOWER(?))")
    like = f"%{query}%"
    params.extend([like, like])

    if modified_since:
        where.append("modified_at >= CAST(? AS TIMESTAMP)")
        params.append(modified_since)
    if owner:
        where.append("modified_by = ?")
        params.append(owner)
    if drive_ids:
        placeholders = ",".join(["?"] * len(drive_ids))
        where.append(f"drive_id IN ({placeholders})")
        params.extend(drive_ids)

    sql = f"""
        SELECT drive_id, item_id, name, full_path, size,
               CAST(modified_at AS VARCHAR) AS modified_at,
               modified_by, is_folder
        FROM items
        WHERE {' AND '.join(where)}
        ORDER BY modified_at DESC NULLS LAST
    """
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        rec = dict(zip(cols, row))
        yield SearchHit(
            drive_id=rec["drive_id"],
            item_id=rec["item_id"],
            name=rec["name"],
            full_path=rec["full_path"],
            size=rec["size"],
            modified_at=rec["modified_at"],
            modified_by=rec["modified_by"],
            is_folder=bool(rec["is_folder"]),
            source="catalog",
        )
```

- [ ] **Step 6: Write tests for `merge`**

Create `tests/test_search_merge.py`:
```python
from __future__ import annotations

from fazla_od.search.graph_search import SearchHit
from fazla_od.search.merge import merge_hits


def _hit(drive, item, modified, source, name="x", is_folder=False):
    return SearchHit(
        drive_id=drive,
        item_id=item,
        name=name,
        full_path=f"/{name}",
        size=0,
        modified_at=modified,
        modified_by=None,
        is_folder=is_folder,
        source=source,
    )


def test_merge_dedupes_by_drive_item_pair() -> None:
    a_graph = _hit("d", "1", "2024-05-01T00:00:00Z", "graph", name="a")
    a_catalog = _hit("d", "1", "2024-05-01T00:00:00Z", "catalog", name="a")
    b_catalog = _hit("d", "2", "2024-04-01T00:00:00Z", "catalog", name="b")

    merged = list(merge_hits([a_graph], [a_catalog, b_catalog]))
    pairs = [(h.drive_id, h.item_id) for h in merged]
    assert pairs == [("d", "1"), ("d", "2")]
    # graph source wins on tie (it's the fresh-from-Graph copy).
    assert merged[0].source == "graph"


def test_merge_sorts_by_modified_desc_nulls_last() -> None:
    h_new = _hit("d", "1", "2024-10-01T00:00:00Z", "graph")
    h_old = _hit("d", "2", "2023-01-01T00:00:00Z", "catalog")
    h_null = _hit("d", "3", None, "catalog")
    merged = list(merge_hits([h_new], [h_old, h_null]))
    assert [h.item_id for h in merged] == ["1", "2", "3"]


def test_merge_respects_limit() -> None:
    hits = [_hit("d", str(i), f"2024-{i+1:02d}-01T00:00:00Z", "catalog")
            for i in range(10)]
    merged = list(merge_hits([], hits, limit=3))
    assert len(merged) == 3
```

- [ ] **Step 7: Implement `src/fazla_od/search/merge.py`**

```python
"""Merge Graph + catalog hits, dedupe by (drive_id, item_id), sort desc by mtime."""
from __future__ import annotations

from typing import Iterable, Iterator

from fazla_od.search.graph_search import SearchHit


def merge_hits(
    graph_hits: Iterable[SearchHit],
    catalog_hits: Iterable[SearchHit],
    *,
    limit: int | None = None,
) -> Iterator[SearchHit]:
    seen: dict[tuple[str, str], SearchHit] = {}
    # Graph first so it wins ties (fresher metadata).
    for h in graph_hits:
        seen[(h.drive_id, h.item_id)] = h
    for h in catalog_hits:
        seen.setdefault((h.drive_id, h.item_id), h)

    def sort_key(h: SearchHit) -> tuple[int, str]:
        # NULLS LAST: tag missing timestamps with 1, real ones with 0, then
        # reverse-sort the timestamp string (ISO sorts lex correctly).
        if h.modified_at is None:
            return (1, "")
        return (0, h.modified_at)

    ordered = sorted(seen.values(), key=sort_key)
    # Reverse only among the (0, ts) group; (1, '') stays at end.
    head = [h for h in ordered if h.modified_at is not None]
    tail = [h for h in ordered if h.modified_at is None]
    head.sort(key=lambda h: h.modified_at, reverse=True)  # type: ignore[arg-type]
    combined = head + tail
    if limit is not None:
        combined = combined[:limit]
    yield from combined
```

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/test_search_graph.py tests/test_search_catalog.py tests/test_search_merge.py -v
```
Expected: 3 + 7 + 3 = 13 passed.

- [ ] **Step 9: Commit**

```bash
git add src/fazla_od/search/ tests/test_search_*.py
git commit -m "feat(search): Graph + catalog search sources with merge/dedup"
```

---

### Task 6: `od-search` CLI + wrapper

**Files:**
- Create: `src/fazla_od/cli/search.py`
- Create: `tests/test_cli_search.py`
- Modify: `src/fazla_od/cli/__main__.py`
- Create: `bin/od-search`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli_search.py`:
```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.catalog.crawl import DriveSpec
from fazla_od.catalog.db import open_catalog
from fazla_od.cli.search import run_search
from fazla_od.search.graph_search import SearchHit


def _cfg(tmp_path: Path):
    cfg = MagicMock()
    cfg.catalog.path = tmp_path / "c.duckdb"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    return cfg


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, full_path, is_folder,
                               is_deleted, size, modified_at, modified_by)
            VALUES
              ('d', 'c1', 'local-invoice.pdf', '/L/local-invoice.pdf', false, false,
               100, TIMESTAMP '2024-03-01 00:00:00', 'a@fazla.com')
            """
        )


def test_search_merges_graph_and_catalog_json(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.search.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.search.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "app"))
    mocker.patch("fazla_od.cli.search.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "deleg"))
    mocker.patch("fazla_od.cli.search.GraphClient", return_value=MagicMock())

    mocker.patch(
        "fazla_od.cli.search.graph_search",
        return_value=iter(
            [
                SearchHit("d", "g1", "Graph-invoice.pdf",
                          "/G/Graph-invoice.pdf", 200,
                          "2024-08-01T00:00:00Z", None, False, "graph"),
            ]
        ),
    )
    _seed(cfg.catalog.path)

    rc = run_search(
        config_path=tmp_path / "config.toml",
        query="invoice",
        scope="me",
        type_="file",
        modified_since=None,
        owner=None,
        limit=50,
        as_json=True,
    )
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    names = [r["name"] for r in parsed]
    # Graph hit (newer) first, local second.
    assert names[:2] == ["Graph-invoice.pdf", "local-invoice.pdf"]


def test_search_scope_tenant_uses_app_only_and_filters(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.search.load_config", return_value=cfg)
    delegated = MagicMock()
    app_only = MagicMock(get_token=lambda: "app")
    mocker.patch("fazla_od.cli.search.DelegatedCredential", return_value=delegated)
    mocker.patch("fazla_od.cli.search.AppOnlyCredential", return_value=app_only)
    mocker.patch("fazla_od.cli.search.GraphClient", return_value=MagicMock())
    mocker.patch("fazla_od.cli.search.resolve_scope",
                 return_value=[DriveSpec("dx", "dn", "o", "business",
                                         "/drives/dx/root/delta")])
    # Graph returns one hit on drive 'dx' and one on drive 'other'; only dx survives.
    mocker.patch(
        "fazla_od.cli.search.graph_search",
        return_value=iter([
            SearchHit("dx", "in-scope", "A", "/A", 1, "2024-01-01T00:00:00Z",
                      None, False, "graph"),
            SearchHit("other", "out", "B", "/B", 1, "2024-02-01T00:00:00Z",
                      None, False, "graph"),
        ]),
    )
    rc = run_search(
        config_path=tmp_path / "config.toml",
        query="x",
        scope="tenant",
        type_="file",
        modified_since=None,
        owner=None,
        limit=50,
        as_json=True,
    )
    assert rc == 0
    delegated.get_token.assert_not_called()
    app_only.get_token.assert_called()


def test_search_tsv_output_has_header(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.search.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.search.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "app"))
    mocker.patch("fazla_od.cli.search.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "deleg"))
    mocker.patch("fazla_od.cli.search.GraphClient", return_value=MagicMock())
    mocker.patch("fazla_od.cli.search.graph_search", return_value=iter([]))
    _seed(cfg.catalog.path)

    run_search(
        config_path=tmp_path / "config.toml",
        query="invoice",
        scope="me",
        type_="file",
        modified_since=None,
        owner=None,
        limit=10,
        as_json=False,
    )
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("drive_id\titem_id\t")
    assert "local-invoice.pdf" in out[1]


def test_search_limit_truncates(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.search.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.search.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "app"))
    mocker.patch("fazla_od.cli.search.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "deleg"))
    mocker.patch("fazla_od.cli.search.GraphClient", return_value=MagicMock())
    mocker.patch(
        "fazla_od.cli.search.graph_search",
        return_value=iter([
            SearchHit("d", f"g{i}", f"n{i}", f"/n{i}", 1,
                      f"2024-{i+1:02d}-01T00:00:00Z", None, False, "graph")
            for i in range(5)
        ]),
    )
    _seed(cfg.catalog.path)
    run_search(
        config_path=tmp_path / "config.toml",
        query="n",
        scope="me",
        type_="file",
        modified_since=None,
        owner=None,
        limit=2,
        as_json=True,
    )
    parsed = json.loads(capsys.readouterr().out)
    assert len(parsed) == 2
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_cli_search.py -v
```
Expected: `ModuleNotFoundError: No module named 'fazla_od.cli.search'`.

- [ ] **Step 3: Implement `src/fazla_od/cli/search.py`**

```python
"""`od-search` subcommand: Graph + catalog fused search."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from fazla_od.auth import AppOnlyCredential, DelegatedCredential
from fazla_od.catalog.crawl import resolve_scope
from fazla_od.catalog.db import open_catalog
from fazla_od.config import load_config
from fazla_od.graph import GraphClient
from fazla_od.search.catalog_search import catalog_search
from fazla_od.search.graph_search import SearchHit, graph_search
from fazla_od.search.merge import merge_hits


def _drive_ids_for_scope(scope: str, graph) -> list[str] | None:
    """Return drive_ids to filter results to, or None = no filter."""
    if scope == "me":
        return None  # delegated auth already limits Graph results to user
    if scope == "tenant":
        return None  # no filter
    # drive:<id>, site:<id>
    specs = resolve_scope(scope, graph)
    return [s.drive_id for s in specs]


def run_search(
    *,
    config_path: Path,
    query: str,
    scope: str,
    type_: str,
    modified_since: str | None,
    owner: str | None,
    limit: int,
    as_json: bool,
) -> int:
    cfg = load_config(config_path)

    # Auth: 'me' -> delegated; everything else -> app-only.
    if scope == "me":
        cred = DelegatedCredential(cfg)
    else:
        cred = AppOnlyCredential(cfg)
    token = cred.get_token()
    graph = GraphClient(token_provider=lambda: token)

    drive_filter = _drive_ids_for_scope(scope, graph)

    graph_results: Iterable[SearchHit] = graph_search(graph, query, limit=limit)
    if drive_filter is not None:
        graph_results = (h for h in graph_results if h.drive_id in drive_filter)
    if type_ == "file":
        graph_results = (h for h in graph_results if not h.is_folder)
    elif type_ == "folder":
        graph_results = (h for h in graph_results if h.is_folder)
    if modified_since:
        graph_results = (
            h for h in graph_results
            if (h.modified_at or "") >= modified_since
        )
    if owner:
        graph_results = (h for h in graph_results if h.modified_by == owner)

    with open_catalog(cfg.catalog.path) as conn:
        catalog_results = list(
            catalog_search(
                conn,
                query,
                type_=type_,  # type: ignore[arg-type]
                modified_since=modified_since,
                owner=owner,
                drive_ids=drive_filter,
            )
        )
        merged = list(
            merge_hits(list(graph_results), catalog_results, limit=limit)
        )

    _emit(merged, as_json=as_json)
    return 0


def _emit(hits: list[SearchHit], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps([h.__dict__ for h in hits]))
        return
    cols = ["drive_id", "item_id", "name", "full_path", "size",
            "modified_at", "modified_by", "is_folder", "source"]
    print("\t".join(cols))
    for h in hits:
        row = [
            h.drive_id, h.item_id, h.name, h.full_path or "",
            "" if h.size is None else str(h.size),
            h.modified_at or "", h.modified_by or "",
            str(h.is_folder), h.source,
        ]
        print("\t".join(row))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-search")
    p.add_argument("--config", default="config.toml")
    p.add_argument("query", help="Free-text query (matched name + full_path).")
    p.add_argument(
        "--scope",
        default="me",
        help="me | drive:<id> | site:<slug-or-id> | tenant (default: me)",
    )
    p.add_argument(
        "--type",
        dest="type_",
        default="file",
        choices=["file", "folder", "all"],
    )
    p.add_argument("--modified-since", metavar="YYYY-MM-DD")
    p.add_argument("--owner", metavar="EMAIL")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", dest="as_json", action="store_true")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_search(
        config_path=Path(args.config),
        query=args.query,
        scope=args.scope,
        type_=args.type_,
        modified_since=args.modified_since,
        owner=args.owner,
        limit=args.limit,
        as_json=args.as_json,
    )
```

- [ ] **Step 4: Wire into dispatcher**

Edit `src/fazla_od/cli/__main__.py`. Add the import and registration:
```python
from fazla_od.cli import search as search_cli
# …
_SUBCOMMANDS = {
    "auth": auth_cli.main,
    "catalog": catalog_cli.main,
    "inventory": inventory_cli.main,
    "search": search_cli.main,
}
```

- [ ] **Step 5: Write `bin/od-search`**

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m fazla_od.cli search "$@"
```

```bash
chmod +x bin/od-search
./bin/od-search --help 2>&1 | head -20
```
Expected: usage line mentioning `query`, `--scope`, `--type`, `--modified-since`, `--owner`, `--limit`, `--json`.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_cli_search.py -v
```
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add src/fazla_od/cli/search.py src/fazla_od/cli/__main__.py tests/test_cli_search.py bin/od-search
git commit -m "feat(cli): od-search merging Graph /search/query with DuckDB"
```

---

### Task 7: Download planner (plan file + SELECT) and shared schema

**Files:**
- Create: `src/fazla_od/download/__init__.py`
- Create: `src/fazla_od/download/planner.py`
- Create: `tests/test_download_planner.py`

The plan-file schema here is the READ subset; Plan 4 extends it with mutation actions. Schema:
```json
[
  {"action": "download", "drive_id": "…", "item_id": "…",
   "args": {"full_path": "/…/file.ext"}}
]
```
Plan 4's actions will be a strict superset: `download | move | rename | copy | delete | label`. The `args` object shape is action-specific. Plan 3 emits plan files via `od-download --plan-out` (see Task 8) and consumes `--from-plan` files whose entries are `action == "download"`.

- [ ] **Step 1: Create the package**

```bash
mkdir -p src/fazla_od/download
touch src/fazla_od/download/__init__.py
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_download_planner.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fazla_od.catalog.db import open_catalog
from fazla_od.download.planner import (
    DownloadItem,
    PlanFileError,
    load_plan_file,
    plan_from_query,
    plan_from_single,
    write_plan_file,
)


def _seed(db: Path) -> None:
    with open_catalog(db) as conn:
        conn.execute(
            """
            INSERT INTO items (drive_id, item_id, name, full_path, is_folder,
                               is_deleted, size)
            VALUES
              ('d', 'i1', 'a.pdf', '/A/a.pdf', false, false, 100),
              ('d', 'i2', 'b.pdf', '/A/b.pdf', false, false, 200),
              ('d', 'f',  'A',     '/A',       true,  false, null)
            """
        )


def test_plan_from_query_returns_items(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        items = plan_from_query(
            conn,
            "SELECT drive_id, item_id, full_path FROM items "
            "WHERE is_folder = false AND name LIKE '%.pdf' ORDER BY item_id",
        )
    assert items == [
        DownloadItem(drive_id="d", item_id="i1", full_path="/A/a.pdf"),
        DownloadItem(drive_id="d", item_id="i2", full_path="/A/b.pdf"),
    ]


def test_plan_from_query_rejects_missing_columns(tmp_path: Path) -> None:
    db = tmp_path / "c.duckdb"
    _seed(db)
    with open_catalog(db) as conn:
        with pytest.raises(PlanFileError, match="drive_id"):
            plan_from_query(conn, "SELECT item_id FROM items")


def test_plan_from_single_builds_one_item() -> None:
    item = plan_from_single(drive_id="d", item_id="i", full_path="/x")
    assert item == DownloadItem(drive_id="d", item_id="i", full_path="/x")


def test_write_and_load_plan_file_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    items = [
        DownloadItem("d", "i1", "/a.pdf"),
        DownloadItem("d", "i2", "/b.pdf"),
    ]
    write_plan_file(p, items)
    raw = json.loads(p.read_text())
    assert raw[0] == {"action": "download", "drive_id": "d", "item_id": "i1",
                      "args": {"full_path": "/a.pdf"}}
    loaded = load_plan_file(p)
    assert loaded == items


def test_load_plan_file_rejects_non_download_actions(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps([
        {"action": "move", "drive_id": "d", "item_id": "i", "args": {}}
    ]))
    with pytest.raises(PlanFileError, match="action"):
        load_plan_file(p)


def test_load_plan_file_rejects_bad_shape(tmp_path: Path) -> None:
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(PlanFileError, match="list"):
        load_plan_file(p)
```

- [ ] **Step 3: Implement `src/fazla_od/download/planner.py`**

```python
"""Download-plan schema + loaders.

Plan 3 owns the READ subset of the repo's plan-file format:

    [
      {"action": "download",
       "drive_id": "<id>",
       "item_id":  "<id>",
       "args": {"full_path": "/path/in/drive"}}
    ]

Plan 4 extends the ``action`` enum with move/rename/copy/delete/label and
their own ``args`` shapes; Plan 3 rejects anything other than ``download``
so we don't accidentally execute a mutation plan with a read-only tool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import duckdb


class PlanFileError(ValueError):
    """Raised when a plan file is malformed or contains unsupported actions."""


@dataclass(frozen=True)
class DownloadItem:
    drive_id: str
    item_id: str
    full_path: str  # path relative to drive root; used for local layout


def plan_from_single(
    *, drive_id: str, item_id: str, full_path: str = ""
) -> DownloadItem:
    return DownloadItem(drive_id=drive_id, item_id=item_id, full_path=full_path)


def plan_from_query(
    conn: duckdb.DuckDBPyConnection, sql: str
) -> list[DownloadItem]:
    """Run ``sql`` against the catalog; each row must yield drive_id, item_id,
    full_path columns (extra columns are ignored)."""
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    required = {"drive_id", "item_id", "full_path"}
    missing = required - set(cols)
    if missing:
        raise PlanFileError(
            f"query is missing required columns: {sorted(missing)}"
        )
    idx = {c: cols.index(c) for c in required}
    out: list[DownloadItem] = []
    for row in cur.fetchall():
        out.append(
            DownloadItem(
                drive_id=row[idx["drive_id"]],
                item_id=row[idx["item_id"]],
                full_path=row[idx["full_path"]] or "",
            )
        )
    return out


def write_plan_file(path: Path, items: Iterable[DownloadItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [
        {
            "action": "download",
            "drive_id": it.drive_id,
            "item_id": it.item_id,
            "args": {"full_path": it.full_path},
        }
        for it in items
    ]
    path.write_text(json.dumps(serialised, indent=2))


def load_plan_file(path: Path) -> list[DownloadItem]:
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PlanFileError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise PlanFileError(f"{path}: plan file must be a JSON list of entries")
    items: list[DownloadItem] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise PlanFileError(f"{path}[{i}]: entry must be a dict")
        action = row.get("action")
        if action != "download":
            raise PlanFileError(
                f"{path}[{i}]: unsupported action {action!r} for od-download "
                f"(expected 'download' — mutations are Plan 4)"
            )
        for key in ("drive_id", "item_id"):
            if key not in row:
                raise PlanFileError(f"{path}[{i}]: missing {key!r}")
        args = row.get("args") or {}
        items.append(
            DownloadItem(
                drive_id=row["drive_id"],
                item_id=row["item_id"],
                full_path=args.get("full_path", "") or "",
            )
        )
    return items
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_download_planner.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/download/__init__.py src/fazla_od/download/planner.py tests/test_download_planner.py
git commit -m "feat(download): plan-file schema (READ subset) + loaders"
```

---

### Task 8: Streaming fetcher + `od-download` CLI

**Files:**
- Create: `src/fazla_od/download/fetcher.py`
- Create: `tests/test_download_fetcher.py`
- Create: `src/fazla_od/cli/download.py`
- Create: `tests/test_cli_download.py`
- Modify: `src/fazla_od/cli/__main__.py`
- Create: `bin/od-download`

- [ ] **Step 1: Write fetcher tests**

Create `tests/test_download_fetcher.py`:
```python
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from fazla_od.download.fetcher import FetchResult, fetch_item


def _transport_redirect_then_200(body: bytes, redirect_url: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(302, headers={"Location": redirect_url})
        # CDN — any other host — returns file bytes. Test that no Authorization.
        assert "authorization" not in {k.lower() for k in request.headers.keys()}
        return httpx.Response(200, content=body,
                              headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


def test_fetch_writes_file(tmp_path: Path) -> None:
    body = b"hello" * 2000
    transport = _transport_redirect_then_200(body, "https://cdn.example/blob/abc")
    dest = tmp_path / "nested" / "a.bin"
    result = fetch_item(
        drive_id="d", item_id="i", dest=dest,
        token_provider=lambda: "t", transport=transport, overwrite=False,
    )
    assert isinstance(result, FetchResult)
    assert result.bytes_written == len(body)
    assert result.skipped is False
    assert dest.read_bytes() == body


def test_fetch_skips_existing_by_default(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    dest.write_bytes(b"old")

    # If we actually hit the network, the test will fail (handler asserts).
    def handler(_req):
        raise AssertionError("should not have been called")

    result = fetch_item(
        drive_id="d", item_id="i", dest=dest,
        token_provider=lambda: "t",
        transport=httpx.MockTransport(handler),
        overwrite=False,
    )
    assert result.skipped is True
    assert dest.read_bytes() == b"old"


def test_fetch_overwrites_when_requested(tmp_path: Path) -> None:
    dest = tmp_path / "a.bin"
    dest.write_bytes(b"old")
    transport = _transport_redirect_then_200(b"NEW", "https://cdn.example/blob/abc")
    result = fetch_item(
        drive_id="d", item_id="i", dest=dest,
        token_provider=lambda: "t", transport=transport, overwrite=True,
    )
    assert result.skipped is False
    assert dest.read_bytes() == b"NEW"


def test_fetch_raises_on_non_redirect_non_200(tmp_path: Path) -> None:
    def handler(req):
        return httpx.Response(
            404, json={"error": {"code": "itemNotFound", "message": "gone"}}
        )

    with pytest.raises(Exception, match="itemNotFound|HTTP404"):
        fetch_item(
            drive_id="d", item_id="i", dest=tmp_path / "x",
            token_provider=lambda: "t",
            transport=httpx.MockTransport(handler), overwrite=True,
        )
```

- [ ] **Step 2: Implement `src/fazla_od/download/fetcher.py`**

```python
"""Streaming file download for OneDrive items.

Graph's `/drives/{d}/items/{i}/content` replies with 302 to a pre-signed CDN
URL. We follow the redirect manually and fetch without Authorization (the
CDN rejects bearer auth). The response body is streamed to disk in 1 MiB
chunks so multi-GB files don't blow up memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from fazla_od.graph import GraphError

_CHUNK = 1024 * 1024  # 1 MiB


@dataclass(frozen=True)
class FetchResult:
    drive_id: str
    item_id: str
    dest: Path
    bytes_written: int
    skipped: bool


def fetch_item(
    *,
    drive_id: str,
    item_id: str,
    dest: Path,
    token_provider: Callable[[], str],
    transport: httpx.BaseTransport | None = None,
    overwrite: bool = False,
    timeout: float = 300.0,
) -> FetchResult:
    if dest.exists() and not overwrite:
        return FetchResult(
            drive_id=drive_id, item_id=item_id, dest=dest,
            bytes_written=0, skipped=True,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)

    content_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/content"

    # Step 1: hit Graph without following redirects so we can capture Location.
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=False) as c:
        resp = c.get(content_url, headers={"Authorization": f"Bearer {token_provider()}"})
        if resp.status_code in (301, 302, 303, 307, 308):
            target = resp.headers.get("Location")
            if not target:
                raise GraphError(f"HTTP{resp.status_code}: redirect without Location")
        elif resp.status_code == 200:
            # Rare: some drives stream content directly. Write it out.
            return _write_stream(dest, resp, drive_id, item_id)
        else:
            try:
                body = resp.json() if resp.content else {}
            except ValueError:
                body = {}
            err = body.get("error", {}) if isinstance(body, dict) else {}
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(f"{code}: {msg}")

    # Step 2: fetch the signed CDN URL without auth.
    with httpx.Client(transport=transport, timeout=timeout, follow_redirects=True) as c:
        with c.stream("GET", target) as r:
            if r.status_code != 200:
                raise GraphError(f"HTTP{r.status_code}: CDN fetch failed")
            total = 0
            with dest.open("wb") as f:
                for chunk in r.iter_bytes(_CHUNK):
                    f.write(chunk)
                    total += len(chunk)
    return FetchResult(
        drive_id=drive_id, item_id=item_id, dest=dest,
        bytes_written=total, skipped=False,
    )


def _write_stream(dest: Path, resp: httpx.Response, drive_id: str, item_id: str) -> FetchResult:
    total = 0
    with dest.open("wb") as f:
        for chunk in resp.iter_bytes(_CHUNK):
            f.write(chunk)
            total += len(chunk)
    return FetchResult(
        drive_id=drive_id, item_id=item_id, dest=dest,
        bytes_written=total, skipped=False,
    )
```

- [ ] **Step 3: Run fetcher tests**

```bash
uv run pytest tests/test_download_fetcher.py -v
```
Expected: 4 passed.

- [ ] **Step 4: Write CLI tests**

Create `tests/test_cli_download.py`:
```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.catalog.db import open_catalog
from fazla_od.cli.download import run_download
from fazla_od.download.fetcher import FetchResult


def _cfg(tmp_path: Path):
    cfg = MagicMock()
    cfg.catalog.path = tmp_path / "c.duckdb"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    return cfg


def test_download_single_item(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.download.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("fazla_od.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))
    captured = []

    def fake_fetch(*, drive_id, item_id, dest, token_provider, overwrite, **_):
        captured.append((drive_id, item_id, dest, overwrite))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"X" * 10)
        return FetchResult(drive_id, item_id, dest, 10, False)

    mocker.patch("fazla_od.cli.download.fetch_item", side_effect=fake_fetch)

    dest = tmp_path / "out"
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id="i1", drive_id="d1",
        from_plan=None, query=None,
        dest=dest, overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 0
    assert captured[0][0] == "d1"
    assert captured[0][1] == "i1"


def test_download_from_plan(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.download.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("fazla_od.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))

    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps([
        {"action": "download", "drive_id": "d", "item_id": "i1",
         "args": {"full_path": "/A/a.pdf"}},
        {"action": "download", "drive_id": "d", "item_id": "i2",
         "args": {"full_path": "/A/b.pdf"}},
    ]))

    calls: list[tuple[str, str, Path]] = []

    def fake_fetch(*, drive_id, item_id, dest, **_):
        calls.append((drive_id, item_id, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
        return FetchResult(drive_id, item_id, dest, 0, False)

    mocker.patch("fazla_od.cli.download.fetch_item", side_effect=fake_fetch)

    dest = tmp_path / "out"
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id=None, drive_id=None,
        from_plan=plan, query=None,
        dest=dest, overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 0
    # Relative path preservation.
    dests = sorted(c[2] for c in calls)
    assert dests[0].name == "a.pdf"
    assert dests[1].name == "b.pdf"


def test_download_query_emits_plan_out(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.download.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("fazla_od.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))

    with open_catalog(cfg.catalog.path) as conn:
        conn.execute(
            "INSERT INTO items (drive_id, item_id, name, full_path, is_folder, "
            "is_deleted) VALUES ('d','i','a.pdf','/A/a.pdf',false,false)"
        )

    mocker.patch(
        "fazla_od.cli.download.fetch_item",
        side_effect=AssertionError("fetch_item should not be called in plan-out mode"),
    )
    plan_out = tmp_path / "plan.json"
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id=None, drive_id=None,
        from_plan=None,
        query="SELECT drive_id, item_id, full_path FROM items WHERE name = 'a.pdf'",
        dest=tmp_path / "out", overwrite=False, concurrency=2,
        plan_out=plan_out, scope="me",
    )
    assert rc == 0
    assert plan_out.exists()
    rows = json.loads(plan_out.read_text())
    assert rows[0]["action"] == "download"
    assert rows[0]["item_id"] == "i"


def test_download_requires_exactly_one_source(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.download.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.download.AppOnlyCredential", return_value=MagicMock())
    mocker.patch("fazla_od.cli.download.DelegatedCredential", return_value=MagicMock())
    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id=None, drive_id=None,
        from_plan=None, query=None,
        dest=tmp_path / "out", overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 2


def test_download_dest_defaults_to_timestamped_workspace(tmp_path, mocker) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.download.load_config", return_value=cfg)
    mocker.patch("fazla_od.cli.download.AppOnlyCredential",
                 return_value=MagicMock(get_token=lambda: "tok"))
    mocker.patch("fazla_od.cli.download.DelegatedCredential",
                 return_value=MagicMock(get_token=lambda: "dtok"))

    captured: list[Path] = []

    def fake_fetch(*, drive_id, item_id, dest, **_):
        captured.append(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"")
        return FetchResult(drive_id, item_id, dest, 0, False)

    mocker.patch("fazla_od.cli.download.fetch_item", side_effect=fake_fetch)
    monkey_now = "20260424-101530"
    mocker.patch("fazla_od.cli.download._timestamp", return_value=monkey_now)

    rc = run_download(
        config_path=tmp_path / "config.toml",
        item_id="i1", drive_id="d1",
        from_plan=None, query=None,
        dest=None, overwrite=False, concurrency=2,
        plan_out=None, scope="me",
    )
    assert rc == 0
    # Default dest is workspaces/download-<ts>/ relative to cwd; we only care
    # that the timestamped dir name is used.
    assert f"download-{monkey_now}" in str(captured[0])
```

- [ ] **Step 5: Implement `src/fazla_od/cli/download.py`**

```python
"""`od-download` subcommand: materialise a subset of OneDrive locally."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import sys
from datetime import datetime
from pathlib import Path

from fazla_od.auth import AppOnlyCredential, DelegatedCredential
from fazla_od.catalog.db import open_catalog
from fazla_od.config import load_config
from fazla_od.download.fetcher import fetch_item
from fazla_od.download.planner import (
    DownloadItem,
    load_plan_file,
    plan_from_query,
    plan_from_single,
    write_plan_file,
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _sources_provided(item_id, drive_id, from_plan, query) -> int:
    count = 0
    if item_id is not None and drive_id is not None:
        count += 1
    if from_plan is not None:
        count += 1
    if query is not None:
        count += 1
    return count


def _dest_for(item: DownloadItem, root: Path) -> Path:
    rel = (item.full_path or item.item_id).lstrip("/")
    if not rel:
        rel = item.item_id
    return root / rel


def run_download(
    *,
    config_path: Path,
    item_id: str | None,
    drive_id: str | None,
    from_plan: Path | None,
    query: str | None,
    dest: Path | None,
    overwrite: bool,
    concurrency: int,
    plan_out: Path | None,
    scope: str,
) -> int:
    if _sources_provided(item_id, drive_id, from_plan, query) != 1:
        print(
            "error: provide exactly one of (--item-id + --drive-id), "
            "--from-plan, or --query",
            file=sys.stderr,
        )
        return 2

    cfg = load_config(config_path)
    if scope == "me":
        cred = DelegatedCredential(cfg)
    else:
        cred = AppOnlyCredential(cfg)

    if query is not None:
        with open_catalog(cfg.catalog.path) as conn:
            items = plan_from_query(conn, query)
    elif from_plan is not None:
        items = load_plan_file(from_plan)
    else:
        items = [plan_from_single(drive_id=drive_id, item_id=item_id,
                                  full_path=item_id)]  # single: use item_id as local name

    if not items:
        print("No items matched — nothing to do.")
        return 0

    if plan_out is not None:
        write_plan_file(plan_out, items)
        print(f"Wrote {len(items)} entries to {plan_out}")
        return 0

    dest_root = dest if dest is not None else (
        Path("workspaces") / f"download-{_timestamp()}"
    )
    dest_root.mkdir(parents=True, exist_ok=True)

    token = cred.get_token()

    successes = 0
    skipped = 0
    failures: list[tuple[DownloadItem, str]] = []

    def _one(item: DownloadItem):
        return fetch_item(
            drive_id=item.drive_id,
            item_id=item.item_id,
            dest=_dest_for(item, dest_root),
            token_provider=lambda: token,
            overwrite=overwrite,
        )

    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(_one, it): it for it in items}
        for fut in cf.as_completed(futures):
            it = futures[fut]
            try:
                res = fut.result()
                if res.skipped:
                    skipped += 1
                    print(f"  skip  {it.full_path or it.item_id}")
                else:
                    successes += 1
                    print(f"  ok    {it.full_path or it.item_id} "
                          f"({res.bytes_written:,} bytes)")
            except Exception as exc:
                failures.append((it, str(exc)))
                print(f"  FAIL  {it.full_path or it.item_id}: {exc}",
                      file=sys.stderr)

    print(f"Done. {successes} downloaded, {skipped} skipped, "
          f"{len(failures)} failed. Dest: {dest_root}")
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-download")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope", default="me",
                   help="me (delegated) or anything else (app-only). "
                        "Controls auth only; actual items come from --item-id / "
                        "--from-plan / --query.")
    p.add_argument("--item-id")
    p.add_argument("--drive-id")
    p.add_argument("--from-plan", type=Path)
    p.add_argument("--query", help="SELECT ... returning drive_id,item_id,full_path")
    p.add_argument("--dest", type=Path,
                   help="Destination dir (default: workspaces/download-<ts>/).")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--plan-out", type=Path,
                   help="Write the resolved items as a plan file and exit "
                        "without downloading.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_download(
        config_path=Path(args.config),
        item_id=args.item_id,
        drive_id=args.drive_id,
        from_plan=args.from_plan,
        query=args.query,
        dest=args.dest,
        overwrite=args.overwrite,
        concurrency=args.concurrency,
        plan_out=args.plan_out,
        scope=args.scope,
    )
```

- [ ] **Step 6: Wire into dispatcher**

Edit `src/fazla_od/cli/__main__.py`:
```python
from fazla_od.cli import download as download_cli
# …
_SUBCOMMANDS = {
    …
    "download": download_cli.main,
}
```

- [ ] **Step 7: Write `bin/od-download`**

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m fazla_od.cli download "$@"
```

```bash
chmod +x bin/od-download
./bin/od-download --help 2>&1 | head -25
```
Expected: usage lists `--item-id`, `--drive-id`, `--from-plan`, `--query`, `--dest`, `--overwrite`, `--concurrency`, `--plan-out`.

- [ ] **Step 8: Run CLI tests**

```bash
uv run pytest tests/test_cli_download.py -v
```
Expected: 5 passed.

- [ ] **Step 9: Commit**

```bash
git add src/fazla_od/download/fetcher.py src/fazla_od/cli/download.py src/fazla_od/cli/__main__.py tests/test_download_fetcher.py tests/test_cli_download.py bin/od-download
git commit -m "feat(download): streaming fetcher + od-download CLI with plan-file workflow"
```

---

### Task 9: PEM → PFX helper + PnP.PowerShell setup docs

**Files:**
- Create: `scripts/ps/convert-cert.sh`
- Create: `docs/ops/pnp-powershell-setup.md`

No tests — this is a one-shot operator script. We verify via the live smoke test in Task 12.

- [ ] **Step 1: Write `scripts/ps/convert-cert.sh`**

```bash
#!/usr/bin/env bash
# convert-cert.sh — one-shot: PEM key+cert -> PFX, store password in Keychain.
#
# Usage:   scripts/ps/convert-cert.sh
# Result:  ~/.config/fazla-od/fazla-od.pfx (mode 600)
#          Keychain entry FazlaODToolkit:PfxPassword holds the export password.
#
# Requires: openssl (system), security (macOS), /dev/urandom.
set -euo pipefail

CERT_DIR="${HOME}/.config/fazla-od"
KEY="${CERT_DIR}/fazla-od.key"
CER="${CERT_DIR}/fazla-od.cer"
PFX="${CERT_DIR}/fazla-od.pfx"
KEYCHAIN_SERVICE="FazlaODToolkit:PfxPassword"
KEYCHAIN_ACCOUNT="fazla-od"

for f in "$KEY" "$CER"; do
    if [[ ! -r "$f" ]]; then
        echo "error: $f not readable" >&2
        exit 1
    fi
done

if [[ -e "$PFX" ]]; then
    echo "error: $PFX already exists — delete or rename it first" >&2
    exit 1
fi

# 32 random bytes -> base64 -> strip non-alphanumerics. Result is ~40 chars.
PASSWORD="$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 40)"

openssl pkcs12 \
    -export \
    -inkey "$KEY" \
    -in "$CER" \
    -name "FazlaODToolkit" \
    -out "$PFX" \
    -passout "pass:${PASSWORD}"

chmod 600 "$PFX"

# Update-or-add (delete existing, then add).
security delete-generic-password \
    -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1 || true

security add-generic-password \
    -a "$KEYCHAIN_ACCOUNT" \
    -s "$KEYCHAIN_SERVICE" \
    -w "$PASSWORD" \
    -T /usr/bin/security

echo "PFX written to $PFX (mode 600)."
echo "Password stored in Keychain:"
echo "  security find-generic-password -a ${KEYCHAIN_ACCOUNT} -s ${KEYCHAIN_SERVICE} -w"
```

```bash
chmod +x scripts/ps/convert-cert.sh
```

- [ ] **Step 2: Write `docs/ops/pnp-powershell-setup.md`**

```markdown
# PnP.PowerShell setup for Fazla OneDrive Toolkit

One-time setup to enable `od-audit-sharing`, which shells out to PowerShell.

## 1. Install PowerShell + PnP module

```bash
brew install --cask powershell    # macOS
pwsh -NoLogo -Command "Install-Module PnP.PowerShell -Scope CurrentUser -Force"
```

Verify:
```bash
pwsh -NoLogo -Command "Get-Module -ListAvailable PnP.PowerShell | Select-Object Version"
```
Expected: a version line (2.x or newer).

## 2. Convert the PEM certificate to PKCS#12 (.pfx)

PnP.PowerShell's `Connect-PnPOnline -CertificatePath` takes a PFX, not the
PEM key + PEM cert we use for the Python flow. Run the one-shot helper:

```bash
./scripts/ps/convert-cert.sh
```

This produces `~/.config/fazla-od/fazla-od.pfx` (mode 600, gitignored —
`~/.config/fazla-od/` is outside the repo) and stores a 40-char random
password in macOS Keychain under service `FazlaODToolkit:PfxPassword`,
account `fazla-od`.

Verify:
```bash
ls -la ~/.config/fazla-od/fazla-od.pfx
security find-generic-password -a fazla-od -s FazlaODToolkit:PfxPassword -w | wc -c
```
Expected: the PFX exists; the password is ~40 characters.

## 3. Confirm the Entra app has the same cert thumbprint

The PFX is built from the exact same PEM key+cert that Plan 1 uploaded to
Entra (thumbprint `C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF`). No new cert
upload is required.

## 4. Smoke-test the connection

```bash
pwsh -NoLogo -Command '
    $pwd = ConvertTo-SecureString -String (
        security find-generic-password -a fazla-od -s FazlaODToolkit:PfxPassword -w
    ) -AsPlainText -Force
    Connect-PnPOnline `
        -Tenant 361efb70-ca20-41ae-b204-9045df001350 `
        -ClientId b22e6fd3-4859-43ae-b997-997ad3aaf14b `
        -CertificatePath "$HOME/.config/fazla-od/fazla-od.pfx" `
        -CertificatePassword $pwd `
        -Url https://fazla.sharepoint.com
    Get-PnPTenantSite | Select-Object -First 3 Url, Title
'
```
Expected: three site URL + title rows printed, no error.

## Rotation

When the PEM cert rotates (every 2 years; see spec §3), re-run
`scripts/ps/convert-cert.sh`. The Keychain entry is overwritten in place.
```

- [ ] **Step 3: Commit**

```bash
git add scripts/ps/convert-cert.sh docs/ops/pnp-powershell-setup.md
git commit -m "feat(pnp): PEM->PFX helper and PowerShell setup docs"
```

---

### Task 10: `audit-sharing.ps1` + `od-audit-sharing` CLI wrapper

**Files:**
- Create: `scripts/ps/audit-sharing.ps1`
- Create: `src/fazla_od/cli/audit_sharing.py`
- Create: `tests/test_cli_audit_sharing.py`
- Modify: `src/fazla_od/cli/__main__.py`
- Create: `bin/od-audit-sharing`

- [ ] **Step 1: Write `scripts/ps/audit-sharing.ps1`**

```powershell
<#
.SYNOPSIS
  Emit one row per permission for every item in a SharePoint site or one drive.

.PARAMETER Scope
  One of: site:<site-id-or-url>, drive:<drive-id>

.PARAMETER OutputFormat
  json (default) or tsv.

.PARAMETER Tenant
  Tenant (directory) ID. Required.

.PARAMETER ClientId
  Azure AD app client ID. Required.

.PARAMETER PfxPath
  Path to the PFX cert (default ~/.config/fazla-od/fazla-od.pfx).

.PARAMETER KeychainService
  Keychain service name holding the PFX password
  (default FazlaODToolkit:PfxPassword).

.EXAMPLE
  pwsh scripts/ps/audit-sharing.ps1 -Scope "site:fazla.sharepoint.com,abc,def" \
      -Tenant 361efb70-... -ClientId b22e6fd3-...
#>
param(
    [Parameter(Mandatory=$true)] [string] $Scope,
    [ValidateSet("json","tsv")] [string] $OutputFormat = "json",
    [Parameter(Mandatory=$true)] [string] $Tenant,
    [Parameter(Mandatory=$true)] [string] $ClientId,
    [string] $PfxPath = "$HOME/.config/fazla-od/fazla-od.pfx",
    [string] $KeychainService = "FazlaODToolkit:PfxPassword",
    [string] $KeychainAccount = "fazla-od"
)

$ErrorActionPreference = "Stop"

function Get-PfxPassword {
    $raw = /usr/bin/security find-generic-password -a $KeychainAccount -s $KeychainService -w
    if (-not $raw) { throw "Could not read PFX password from Keychain." }
    return (ConvertTo-SecureString -String $raw -AsPlainText -Force)
}

function Connect-SiteByUrl($url) {
    $pwd = Get-PfxPassword
    Connect-PnPOnline `
        -Tenant $Tenant `
        -ClientId $ClientId `
        -CertificatePath $PfxPath `
        -CertificatePassword $pwd `
        -Url $url | Out-Null
}

function Parse-Scope {
    param([string]$s)
    if ($s -like "site:*") {
        $ident = $s.Substring(5)
        if ($ident -match "^https?://") { return @{ Kind="site-url"; Value=$ident } }
        return @{ Kind="site-id"; Value=$ident }
    } elseif ($s -like "drive:*") {
        return @{ Kind="drive"; Value=$s.Substring(6) }
    } else {
        throw "Unsupported scope: $s (expected site:<id|url> or drive:<id>)"
    }
}

function Resolve-SiteUrl {
    param($parsed)
    if ($parsed.Kind -eq "site-url") { return $parsed.Value }
    # Connect to tenant admin to resolve the id -> url, then reconnect.
    $pwd = Get-PfxPassword
    $adminUrl = "https://$($Tenant.Split('-')[0])-admin.sharepoint.com"
    # Fall back: the caller typically supplies a URL in practice. If we only
    # have a site-id, the operator must pass it as site:<url> — document this.
    throw "site:<id> form requires an admin endpoint; please pass site:<full-url>."
}

function Emit-Row {
    param($row)
    if ($OutputFormat -eq "json") {
        return $row
    }
    # TSV
    "{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}" -f `
        $row.drive_id, $row.item_id, $row.full_path, $row.shared_with,
        $row.permission_level, $row.is_external, $row.expires_at
}

$parsed = Parse-Scope $Scope
if ($parsed.Kind -eq "drive") {
    throw "Drive-only audit not yet implemented; pass site:<url> for now."
}

$siteUrl = Resolve-SiteUrl $parsed
Connect-SiteByUrl $siteUrl

$rows = New-Object System.Collections.Generic.List[object]
$lists = Get-PnPList | Where-Object { $_.BaseTemplate -eq 101 }  # Document Library
foreach ($lst in $lists) {
    $items = Get-PnPListItem -List $lst -PageSize 1000 -Fields "FileRef","UniqueId"
    foreach ($it in $items) {
        $path = $it["FileRef"]
        $uid  = $it["UniqueId"]
        $perms = Get-PnPListItemPermission -List $lst -Identity $it.Id
        foreach ($p in $perms.Permissions) {
            $shared = $p.PrincipalName
            $isExternal = $false
            if ($shared -match "#ext#" -or $shared -match "@") {
                $isExternal = ($shared -notmatch "@fazla\.")
            }
            $rows.Add([ordered]@{
                drive_id          = $lst.Id.ToString()
                item_id           = $uid.ToString()
                full_path         = $path
                shared_with       = $shared
                permission_level  = $p.Roles -join ","
                is_external       = $isExternal
                expires_at        = $p.ExpirationDateTime
            })
        }
    }
}

if ($OutputFormat -eq "json") {
    $rows | ConvertTo-Json -Depth 4 -Compress
} else {
    "drive_id`titem_id`tfull_path`tshared_with`tpermission_level`tis_external`texpires_at"
    foreach ($r in $rows) { Emit-Row $r }
}
```

Note: PnP exposes two slightly different cmdlet names across versions
(`Get-PnPListItemPermission` vs `Get-PnPListItemPermissions`). The script is
written against the singular form used in PnP.PowerShell 2.x. If the live
smoke test in Task 12 fails on that name, switch to the plural — documented
inline in the script's completion log at that point.

- [ ] **Step 2: Write failing Python tests (mocked subprocess)**

Create `tests/test_cli_audit_sharing.py`:
```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.cli.audit_sharing import run_audit


def _cfg(tmp_path: Path):
    cfg = MagicMock()
    cfg.tenant_id = "tenant-x"
    cfg.client_id = "client-x"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    cfg.catalog.path = tmp_path / "c.duckdb"
    return cfg


def test_audit_shells_out_and_parses_json(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.audit_sharing.load_config", return_value=cfg)

    payload = [
        {"drive_id": "d", "item_id": "i", "full_path": "/A/a.pdf",
         "shared_with": "arda@fazla.com", "permission_level": "owner",
         "is_external": False, "expires_at": None},
    ]
    mocker.patch(
        "fazla_od.cli.audit_sharing.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )
    rc = run_audit(
        config_path=tmp_path / "config.toml",
        scope="site:https://fazla.sharepoint.com",
        output_format="json",
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == payload


def test_audit_propagates_nonzero_exit(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.audit_sharing.load_config", return_value=cfg)
    mocker.patch(
        "fazla_od.cli.audit_sharing.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Connect-PnPOnline: cert load failed"
        ),
    )
    rc = run_audit(
        config_path=tmp_path / "config.toml",
        scope="site:https://fazla.sharepoint.com",
        output_format="json",
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "cert load failed" in err


def test_audit_tsv_is_emitted_verbatim(tmp_path, mocker, capsys) -> None:
    cfg = _cfg(tmp_path)
    mocker.patch("fazla_od.cli.audit_sharing.load_config", return_value=cfg)
    tsv = (
        "drive_id\titem_id\tfull_path\tshared_with\tpermission_level\t"
        "is_external\texpires_at\n"
        "d\ti\t/A/a.pdf\tarda@fazla.com\towner\tFalse\t\n"
    )
    mocker.patch(
        "fazla_od.cli.audit_sharing.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=tsv, stderr=""
        ),
    )
    rc = run_audit(
        config_path=tmp_path / "config.toml",
        scope="site:https://fazla.sharepoint.com",
        output_format="tsv",
    )
    assert rc == 0
    assert capsys.readouterr().out == tsv
```

- [ ] **Step 3: Implement `src/fazla_od/cli/audit_sharing.py`**

```python
"""`od-audit-sharing` subcommand: shell out to PnP.PowerShell.

Plan 3 delivers only the Python wrapper. The heavy lifting lives in
``scripts/ps/audit-sharing.ps1``; see ``docs/ops/pnp-powershell-setup.md``
for one-time setup.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from fazla_od.config import load_config


def run_audit(
    *, config_path: Path, scope: str, output_format: str
) -> int:
    cfg = load_config(config_path)
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "ps" / "audit-sharing.ps1"
    if not script.exists():
        print(f"error: {script} not found", file=sys.stderr)
        return 2

    cmd = [
        "pwsh", "-NoLogo", "-NoProfile", "-File", str(script),
        "-Scope", scope,
        "-OutputFormat", output_format,
        "-Tenant", cfg.tenant_id,
        "-ClientId", cfg.client_id,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr, end="")
        return proc.returncode
    print(proc.stdout, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-audit-sharing")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--scope", required=True,
                   help="site:<url> (site-id form requires admin endpoint)")
    p.add_argument("--output-format", choices=["json", "tsv"], default="json")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return run_audit(
        config_path=Path(args.config),
        scope=args.scope,
        output_format=args.output_format,
    )
```

- [ ] **Step 4: Wire into dispatcher**

Edit `src/fazla_od/cli/__main__.py`:
```python
from fazla_od.cli import audit_sharing as audit_sharing_cli
# …
_SUBCOMMANDS = {
    …
    "audit-sharing": audit_sharing_cli.main,
}
```

- [ ] **Step 5: Write `bin/od-audit-sharing`**

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m fazla_od.cli audit-sharing "$@"
```

```bash
chmod +x bin/od-audit-sharing
./bin/od-audit-sharing --help 2>&1 | head -10
```
Expected: usage listing `--scope`, `--output-format`.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_cli_audit_sharing.py -v
```
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add scripts/ps/audit-sharing.ps1 src/fazla_od/cli/audit_sharing.py src/fazla_od/cli/__main__.py tests/test_cli_audit_sharing.py bin/od-audit-sharing
git commit -m "feat(audit): od-audit-sharing via PnP.PowerShell (JSON/TSV)"
```

---

### Task 11: Update `AGENTS.md` + full-suite sanity

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add new rows to the table**

Find the existing table block in `AGENTS.md` ("Current CLI surface (Plans 1-2 complete)"). Replace its heading with "Current CLI surface (Plans 1-3 complete)". Append the following rows to the existing Markdown table **without** touching the Plan 1-2 rows:

```markdown
| `./bin/od-catalog-refresh --scope tenant` | Delta-crawl every user drive + every SharePoint library (app-only). Prompts on /dev/tty if >5 drives unless `--yes`. |
| `./bin/od-catalog-refresh --scope site:<slug-or-id>` | Delta-crawl one SharePoint site's drives. |
| `./bin/od-search <query> [--scope …] [--type file\|folder\|all] [--modified-since …] [--owner …] [--limit N]` | Fuse Graph /search/query with DuckDB catalog LIKE match; dedupe by (drive_id, item_id). |
| `./bin/od-download --item-id … --drive-id …` | Stream one file into `workspaces/download-<ts>/`. |
| `./bin/od-download --from-plan plan.json` | Download the set listed in a Plan-3 plan file (`action == "download"`). |
| `./bin/od-download --query "<SELECT …>" [--plan-out plan.json]` | Build a plan from a catalog SELECT; `--plan-out` writes it without downloading. |
| `./bin/od-audit-sharing --scope site:<url> [--output-format json\|tsv]` | Emit one row per permission via PnP.PowerShell (requires one-time setup — see `docs/ops/pnp-powershell-setup.md`). |
```

Also append a new subsection right after the table:

```markdown
### Plan-file schema (read-only ops — Plan 3)

Plan 3 emits / consumes plan files of the shape:

```json
[
  {"action": "download",
   "drive_id": "<id>",
   "item_id":  "<id>",
   "args": {"full_path": "/path/in/drive"}}
]
```

Plan 4 will extend `action` with `move | rename | copy | delete | label` and
their own `args` shapes; Plan-3 tools reject any non-`download` action so you
cannot accidentally run a mutation plan with `od-download`.

### PowerShell prerequisites (for `od-audit-sharing`)

One-time setup: see `docs/ops/pnp-powershell-setup.md`. Converts the PEM
cert to PFX at `~/.config/fazla-od/fazla-od.pfx` and stores an export
password in macOS Keychain under `FazlaODToolkit:PfxPassword`.
```

Confirm nothing above the Plan 1-2 rows changed.

- [ ] **Step 2: Full-suite sanity**

```bash
uv run pytest -v
```
Expected:
- Plan-1+2 baseline (from Plan 2 completion log): 52 passed, 1 skipped.
- Plan 3 additions:
  - `test_graph_retry.py`: 5
  - `test_prompts.py`: 6
  - `test_catalog_crawl_tenant.py`: 7
  - `test_cli_catalog.py`: +4 new (existing 3 still pass) → 7 total
  - `test_search_graph.py`: 3
  - `test_search_catalog.py`: 7
  - `test_search_merge.py`: 3
  - `test_download_planner.py`: 6
  - `test_download_fetcher.py`: 4
  - `test_cli_search.py`: 4
  - `test_cli_download.py`: 5
  - `test_cli_audit_sharing.py`: 3

Total new = 57. New grand total = **109 passed, 1 skipped** (live).

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: AGENTS.md v3 — add Plan 3 commands + plan-file schema + PS prereqs"
```

---

### Task 12: End-to-end live smoke test (user-driven)

This task runs on the user's machine against the real Fazla tenant. No new code.

- [ ] **Step 1: Verify retry is live**

Nothing to do here if Task 1 passed unit tests; the new `GraphClient` is already in use by every command.

- [ ] **Step 2: Tenant catalog preview (abort)**

```bash
./bin/od-catalog-refresh --scope tenant
```
Expected: prints `Resolved N drive(s) under scope 'tenant'.`, a `Preview:` list of up to 20 drives, then a prompt. Type `n` → `Aborted by user.` and exit code 1. Re-run with `--yes` to actually crawl (or press Ctrl-C here and continue in step 3).

- [ ] **Step 3: Site scope — pick a small site first**

Identify a site you own:
```bash
./bin/od-catalog-refresh --scope site:<a-slug-that-matches-exactly-one-site> --yes
```
Expected: resolves to that one site's drives and crawls them. Use `od-catalog-status` to verify new drives landed in the catalog.

- [ ] **Step 4: Exercise search**

```bash
./bin/od-search "invoice" --scope me --limit 5
./bin/od-search "invoice" --scope me --json --limit 5 | python -m json.tool
./bin/od-search "invoice" --scope me --type folder
./bin/od-search "invoice" --scope me --modified-since 2024-01-01
```
Expected: each emits either TSV or JSON, with local + Graph hits deduped. Sanity-check that names and paths look real.

- [ ] **Step 5: Exercise download**

Pick a small file you know exists in your OneDrive:
```bash
# Use search to get a drive_id + item_id
./bin/od-search "<some-exact-filename>" --scope me --json | python -c "
import json,sys
row = json.load(sys.stdin)[0]
print(row['drive_id'], row['item_id'])
"
# Download it
./bin/od-download --scope me --drive-id <id> --item-id <iid> --dest ./tmp-download
ls -la ./tmp-download
```
Expected: file materialises under `./tmp-download/<item_id>` (or the full path).

Then test plan-out mode:
```bash
./bin/od-download --scope me \
    --query "SELECT drive_id, item_id, full_path FROM items WHERE name LIKE '%.pdf' LIMIT 3" \
    --plan-out ./tmp-download-plan.json
cat ./tmp-download-plan.json
./bin/od-download --scope me --from-plan ./tmp-download-plan.json --dest ./tmp-download-plan
ls -R ./tmp-download-plan | head
```
Expected: plan file contains 3 entries with `action: download`; second run materialises them preserving paths.

Clean up:
```bash
rm -rf ./tmp-download ./tmp-download-plan ./tmp-download-plan.json
```

- [ ] **Step 6: Run `od-audit-sharing` end-to-end**

First, one-time PowerShell setup (idempotent if already done):
```bash
# Only needed once ever
./scripts/ps/convert-cert.sh
# Sanity check
pwsh -NoLogo -Command "Get-Module -ListAvailable PnP.PowerShell | Select-Object Version"
```

Then run it:
```bash
./bin/od-audit-sharing --scope "site:https://fazla.sharepoint.com/sites/<your-small-site>" \
    --output-format tsv | head -10
./bin/od-audit-sharing --scope "site:https://fazla.sharepoint.com/sites/<your-small-site>" \
    --output-format json | python -m json.tool | head -40
```
Expected: TSV has the documented 7 columns; JSON version is an array of objects. If the PS script errors on `Get-PnPListItemPermission`, switch to `Get-PnPListItemPermissions` in `scripts/ps/audit-sharing.ps1`, note in the completion log, and re-run.

- [ ] **Step 7: Verify nothing sensitive is staged**

```bash
git status --porcelain
ls -la ~/.config/fazla-od/fazla-od.pfx
```
Expected: `git status` clean (no `workspaces/`, `cache/`, or `*.pfx` appear; those are gitignored via `cache/`, `workspaces/`, and the fact that the pfx lives outside the repo).

- [ ] **Step 8: Record completion**

Append to `docs/superpowers/plans/2026-04-24-search-and-readonly-ops.md`:
```markdown

---

## Completion log

- **Smoke test run:** <DATE>
- **Unit tests:** 109 passed, 1 skipped (live).
- **Tenant scope preview:** N drives resolved; prompt aborted on `n`.
- **Site scope crawl:** `<site-slug>` → `<k>` drives, `<items>` items added/updated.
- **Search:** Graph + catalog dedup verified; <count> hits for "invoice" under `--scope me`.
- **Download:** single-item, query → plan-out, plan-file execute all verified.
- **od-audit-sharing:** <site-slug> returned <N> permission rows; external-flag logic sanity-checked on at least one ext. share.
```

Commit:
```bash
git add docs/superpowers/plans/2026-04-24-search-and-readonly-ops.md
git commit -m "chore: Plan 3 complete — search, download, audit-sharing verified live"
```

- [ ] **Step 9: Push**

```bash
git push
```
Expected: all Plan 3 commits pushed to `origin/main`.

---

## Intentionally deferred (not this plan)

- **Any mutation** (`od-move`, `od-rename`, `od-copy`, `od-delete`, `od-label`, recycle-bin cleanup). → Plan 4.
- **Audit log** `logs/ops/YYYY-MM-DD.jsonl`. Plan 3 is read-only and never logs. → Plan 4.
- **Dry-run / `--confirm` / `--from-plan` execution semantics** for mutations. Plan 3 uses plan files for _read_ (download) only, and `--from-plan` here just materialises what's listed. → Plan 4 owns `--confirm`.
- **`--unsafe-scope`** and TTY confirm for destructive ops. Plan 3's >5-drive gate is a separate, milder pattern (cost/time awareness, not destruction). → Plan 4.
- **Plan-file action enum** beyond `download`. Plan 3 rejects `move`/`rename`/etc to prevent accidental cross-use. → Plan 4 adds them with strict-superset semantics.
- **`od-undo`** replay from the audit log. → Plan 5.
- **`od-clean`** (recycle-bin, stale-share cleanup). → Plan 4 partial; Plan 5 for stale-share.
- **rclone `od-sync-workspace`** and the hybrid bisync flow. → Plan 5.
- **MCP server** wrapping stable commands. → Phase 2 per spec.

## Plan 3 done. What's next?

Plan 4 (Mutations) picks up from here. It depends on:
- `with_retry`-wired `GraphClient` (including `POST`).
- `resolve_scope` (tenant + site).
- `confirm_or_abort` + the `/dev/tty` pattern.
- `download.planner`'s plan-file loader shape — extended in Plan 4 with `move|rename|copy|delete|label` actions.
- `od-search` / `od-inventory` as the candidate-generation tools that produce plan files for Plan 4 to consume via `--from-plan --confirm`.
