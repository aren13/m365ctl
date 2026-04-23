# Foundation & Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the Fazla OneDrive Toolkit repo and ship a working `od-auth` command that authenticates against the Fazla M365 tenant via both delegated (device-code) and app-only (certificate) flows, providing the foundation every subsequent plan will build on.

**Architecture:** Python 3.11+ package (`fazla_od`) managed by `uv`. MSAL-backed auth library with persistent delegated token cache. Thin `httpx`-based Graph client wrapper (no `msgraph-sdk` dependency yet — added in Plan 2). POSIX shell wrappers under `bin/` dispatch to a single Python entry point with subcommands. No mutating commands in Plan 1, so the audit-log and retry helpers are deferred to later plans.

**Tech Stack:** Python 3.11+, `uv`, `msal`, `msal-extensions`, `httpx`, `pytest`, `pytest-mock`, bash, Microsoft Graph v1.0.

**End-state (definition of done):**
- `./bin/od-auth login` completes device-code flow; token cached persistently.
- `./bin/od-auth whoami` prints: delegated identity + scopes, app-only app display name, cert subject + thumbprint + days-until-expiry, tenant id, catalog status.
- Both flows demonstrably hit `https://graph.microsoft.com/v1.0/` — no mocks in the smoke test.
- Git repo initialised; spec + plan committed; `config.toml` gitignored.

**Inputs the engineer will need (already gathered):**
- `tenant_id`: `361efb70-ca20-41ae-b204-9045df001350`
- `client_id`: `b22e6fd3-4859-43ae-b997-997ad3aaf14b`
- Cert private key: `~/.config/fazla-od/fazla-od.key` (PEM)
- Cert public cert: `~/.config/fazla-od/fazla-od.cer` (PEM)
- Cert SHA-1 thumbprint: `C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF`
- Admin consent granted in Entra for BOTH Delegated and Application permission sets.

**Domain primer (for engineers unfamiliar with M365):**
- **MSAL** = Microsoft Authentication Library; handles OAuth2 flows against Entra ID.
- **Delegated auth** = user signs in, app acts on their behalf. We use device-code flow (prints a URL + code; user opens browser, enters code).
- **App-only auth** = app authenticates as itself, no user present. We use `client_credentials` with a signed JWT assertion built from our certificate.
- **Graph** = `https://graph.microsoft.com/v1.0/`. `/me` returns signed-in user (delegated only). `/applications/{appId}` returns the app registration (works app-only).

## File structure (end of Plan 1)

```
Fazla-OneDrive/
├── .gitignore
├── AGENTS.md
├── README.md
├── config.toml.example             # tracked
├── config.toml                     # GITIGNORED — user copies from example
├── pyproject.toml
├── uv.lock
├── bin/
│   └── od-auth                     # bash wrapper → uv run python -m fazla_od.cli auth
├── src/
│   └── fazla_od/
│       ├── __init__.py
│       ├── __main__.py             # python -m fazla_od entry
│       ├── config.py               # TOML loader + Config dataclass
│       ├── auth.py                 # MSAL wrapper (delegated + app-only)
│       ├── graph.py                # httpx client with bearer-token helper
│       └── cli/
│           ├── __init__.py
│           ├── __main__.py         # argparse dispatcher
│           └── auth.py             # `auth login` / `auth whoami` subcommands
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # shared fixtures
│   ├── test_config.py
│   ├── test_auth.py                # mocked; plus live smoke behind env var
│   ├── test_graph.py
│   └── test_cli_auth.py
└── docs/
    ├── superpowers/
    │   ├── specs/
    │   │   └── 2026-04-24-fazla-onedrive-toolkit-design.md    # already exists
    │   └── plans/
    │       └── 2026-04-24-foundation-and-auth.md              # this file
```

---

### Task 1: Initialise git repo and .gitignore

**Files:**
- Create: `/Users/ae/Projects/Fazla-OneDrive/.gitignore`

- [ ] **Step 1: Initialise git**

Run:
```bash
cd /Users/ae/Projects/Fazla-OneDrive
git init -b main
```
Expected: `Initialized empty Git repository in .../Fazla-OneDrive/.git/`.

- [ ] **Step 2: Write `.gitignore`**

Create `.gitignore` with exactly this content:
```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.venv/
.mypy_cache/
.ruff_cache/

# uv
# (uv.lock IS tracked)

# Secrets / config
config.toml
rclone/rclone.conf
.env
.env.*
!.env.example

# Runtime state (gitignored for now; future plans will add)
cache/
workspaces/
logs/

# OS / editor
.DS_Store
.idea/
.vscode/
*.swp
```

- [ ] **Step 3: Verify nothing sensitive is staged**

Run:
```bash
git status
```
Expected: `Untracked files:` list; no file path mentions `config.toml`, `rclone.conf`, `~/.config/fazla-od`, or any `.key`/`.cer`.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: initial .gitignore"
```

---

### Task 2: Commit the approved spec and this plan

**Files:**
- Already exist: `docs/superpowers/specs/2026-04-24-fazla-onedrive-toolkit-design.md`, `docs/superpowers/plans/2026-04-24-foundation-and-auth.md`

- [ ] **Step 1: Stage and commit**

```bash
git add docs/
git commit -m "docs: approved design spec and Plan 1 (foundation & auth)"
```

---

### Task 3: Scaffold `pyproject.toml` and run `uv sync`

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`

- [ ] **Step 1: Verify `uv` is installed**

Run:
```bash
uv --version
```
Expected: `uv 0.5.x` or newer. If missing, install with `brew install uv`.

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "fazla-od"
version = "0.1.0"
description = "CLI toolkit for admin-scoped control of the Fazla M365 OneDrive + SharePoint tenant via Microsoft Graph."
requires-python = ">=3.11"
dependencies = [
    "msal>=1.28",
    "msal-extensions>=1.2",
    "httpx>=0.27",
    "cryptography>=42",   # for cert thumbprint computation
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-mock>=3.12",
    "ruff>=0.5",
]

[project.scripts]
fazla-od = "fazla_od.cli.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/fazla_od"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra -q"
markers = [
    "live: hits real Microsoft Graph; requires FAZLA_OD_LIVE_TESTS=1",
]

[tool.ruff]
line-length = 100
target-version = "py311"
```

- [ ] **Step 3: Write minimal `README.md`**

```markdown
# Fazla OneDrive Toolkit

CLI for admin-scoped control of the Fazla M365 OneDrive + SharePoint tenant.

See `docs/superpowers/specs/2026-04-24-fazla-onedrive-toolkit-design.md` for the full design.

## Quick start (after Plan 1)

1. Copy `config.toml.example` to `config.toml` and fill in.
2. `./bin/od-auth login` — device-code sign-in (once per token lifetime).
3. `./bin/od-auth whoami` — verify both auth flows work.

## Layout

See spec §9 for the full layout. After Plan 1 only `bin/od-auth` exists.
```

- [ ] **Step 4: Create the `src/fazla_od/` package with stub files**

```bash
mkdir -p src/fazla_od/cli tests
touch src/fazla_od/__init__.py src/fazla_od/cli/__init__.py tests/__init__.py
```

Write `src/fazla_od/__main__.py`:
```python
from fazla_od.cli.__main__ import main

if __name__ == "__main__":
    main()
```

Write `src/fazla_od/cli/__main__.py` (stub — will be expanded in Task 9):
```python
import sys


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: fazla-od <subcommand> [args...]", file=sys.stderr)
        return 2
    print(f"unknown subcommand: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Sync the environment**

Run:
```bash
uv sync --extra dev
```
Expected: `Resolved N packages`, `Installed N packages`, and a `.venv/` directory + `uv.lock` file appear.

- [ ] **Step 6: Verify the package imports**

Run:
```bash
uv run python -c "import fazla_od; print(fazla_od.__name__)"
```
Expected: `fazla_od`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock README.md src/
git commit -m "chore: scaffold fazla_od Python package with uv"
```

---

### Task 4: Config module — tests first

**Files:**
- Create: `tests/test_config.py`
- Create: `src/fazla_od/config.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config.py`:
```python
from pathlib import Path

import pytest

from fazla_od.config import Config, ConfigError, load_config


def _valid_toml(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        """
tenant_id    = "361efb70-ca20-41ae-b204-9045df001350"
client_id    = "b22e6fd3-4859-43ae-b997-997ad3aaf14b"
cert_path    = "~/.config/fazla-od/fazla-od.key"
cert_public  = "~/.config/fazla-od/fazla-od.cer"
default_auth = "delegated"

[scope]
allow_drives         = ["me"]
allow_users          = ["*"]
deny_paths           = []
unsafe_requires_flag = true

[catalog]
path             = "cache/catalog.duckdb"
refresh_on_start = false

[logging]
ops_dir = "logs/ops"
"""
    )
    return p


def test_load_returns_config_with_parsed_fields(tmp_path: Path) -> None:
    cfg = load_config(_valid_toml(tmp_path))
    assert isinstance(cfg, Config)
    assert cfg.tenant_id == "361efb70-ca20-41ae-b204-9045df001350"
    assert cfg.client_id == "b22e6fd3-4859-43ae-b997-997ad3aaf14b"
    assert cfg.default_auth == "delegated"
    assert cfg.scope.allow_drives == ["me"]


def test_load_expands_user_home_in_paths(tmp_path: Path) -> None:
    cfg = load_config(_valid_toml(tmp_path))
    assert str(cfg.cert_path).startswith(str(Path.home()))
    assert str(cfg.cert_public).startswith(str(Path.home()))


def test_missing_tenant_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.toml"
    p.write_text('client_id = "x"\n[scope]\nallow_drives=["me"]\n')
    with pytest.raises(ConfigError, match="tenant_id"):
        load_config(p)


def test_empty_allow_drives_raises(tmp_path: Path) -> None:
    toml = _valid_toml(tmp_path).read_text().replace('["me"]', "[]")
    p = tmp_path / "empty.toml"
    p.write_text(toml)
    with pytest.raises(ConfigError, match="allow_drives"):
        load_config(p)


def test_invalid_default_auth_raises(tmp_path: Path) -> None:
    toml = _valid_toml(tmp_path).read_text().replace('"delegated"', '"bogus"')
    p = tmp_path / "bad_auth.toml"
    p.write_text(toml)
    with pytest.raises(ConfigError, match="default_auth"):
        load_config(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_config.py -v
```
Expected: all 5 tests FAIL with `ModuleNotFoundError: No module named 'fazla_od.config'` or `ImportError`.

- [ ] **Step 3: Implement `config.py`**

Create `src/fazla_od/config.py`:
```python
"""TOML-backed configuration loader for fazla_od.

The config file is usually at the repo root (`config.toml`); all paths
inside are expanded with `~` → `$HOME` but are not resolved against the
filesystem (callers decide whether the cert actually has to exist).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

AuthMode = Literal["delegated", "app-only"]
_VALID_AUTH: tuple[AuthMode, ...] = ("delegated", "app-only")


class ConfigError(ValueError):
    """Raised when config.toml is missing required fields or has invalid values."""


@dataclass(frozen=True)
class ScopeConfig:
    allow_drives: list[str]
    allow_users: list[str] = field(default_factory=lambda: ["*"])
    deny_paths: list[str] = field(default_factory=list)
    unsafe_requires_flag: bool = True


@dataclass(frozen=True)
class CatalogConfig:
    path: Path
    refresh_on_start: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    ops_dir: Path


@dataclass(frozen=True)
class Config:
    tenant_id: str
    client_id: str
    cert_path: Path
    cert_public: Path
    default_auth: AuthMode
    scope: ScopeConfig
    catalog: CatalogConfig
    logging: LoggingConfig


def _require(mapping: dict, key: str, source: str) -> object:
    if key not in mapping:
        raise ConfigError(f"{source}: missing required field '{key}'")
    return mapping[key]


def _expand(p: str) -> Path:
    return Path(p).expanduser()


def load_config(path: Path | str) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e

    tenant_id = _require(data, "tenant_id", str(path))
    client_id = _require(data, "client_id", str(path))
    cert_path = _require(data, "cert_path", str(path))
    cert_public = _require(data, "cert_public", str(path))
    default_auth = data.get("default_auth", "delegated")
    if default_auth not in _VALID_AUTH:
        raise ConfigError(
            f"default_auth must be one of {_VALID_AUTH}, got {default_auth!r}"
        )

    scope_raw = _require(data, "scope", str(path))
    allow_drives = _require(scope_raw, "allow_drives", f"{path}:[scope]")
    if not isinstance(allow_drives, list) or not allow_drives:
        raise ConfigError(f"{path}:[scope].allow_drives must be a non-empty list")

    scope = ScopeConfig(
        allow_drives=list(allow_drives),
        allow_users=list(scope_raw.get("allow_users", ["*"])),
        deny_paths=list(scope_raw.get("deny_paths", [])),
        unsafe_requires_flag=bool(scope_raw.get("unsafe_requires_flag", True)),
    )

    catalog_raw = data.get("catalog", {})
    catalog = CatalogConfig(
        path=_expand(catalog_raw.get("path", "cache/catalog.duckdb")),
        refresh_on_start=bool(catalog_raw.get("refresh_on_start", False)),
    )

    logging_raw = data.get("logging", {})
    logging_cfg = LoggingConfig(
        ops_dir=_expand(logging_raw.get("ops_dir", "logs/ops")),
    )

    return Config(
        tenant_id=str(tenant_id),
        client_id=str(client_id),
        cert_path=_expand(str(cert_path)),
        cert_public=_expand(str(cert_public)),
        default_auth=default_auth,
        scope=scope,
        catalog=catalog,
        logging=logging_cfg,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run pytest tests/test_config.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/config.py tests/test_config.py
git commit -m "feat(config): TOML-backed Config loader with validation"
```

---

### Task 5: Write `config.toml.example`

**Files:**
- Create: `config.toml.example`

- [ ] **Step 1: Write the example**

```toml
# Fazla OneDrive Toolkit — configuration template.
# Copy to `config.toml` (gitignored) and fill in.

tenant_id    = "361efb70-ca20-41ae-b204-9045df001350"
client_id    = "b22e6fd3-4859-43ae-b997-997ad3aaf14b"
cert_path    = "~/.config/fazla-od/fazla-od.key"   # PEM, private key, mode 600
cert_public  = "~/.config/fazla-od/fazla-od.cer"   # PEM, public cert (also uploaded to Entra)
default_auth = "delegated"                          # or "app-only"

[scope]
# Drives the toolkit is allowed to touch.
# Examples: "me", "site:Finance", "site:Legal", "drive:b!abcdef..."
# Tenant-wide mutations require items to be inside this list (or --unsafe-scope).
allow_drives         = ["me"]
allow_users          = ["*"]
deny_paths           = ["/Confidential/**", "/HR/**"]
unsafe_requires_flag = true

[catalog]
path             = "cache/catalog.duckdb"
refresh_on_start = false

[logging]
ops_dir = "logs/ops"
```

- [ ] **Step 2: Create the real `config.toml` locally (gitignored)**

```bash
cp config.toml.example config.toml
```

- [ ] **Step 3: Verify `config.toml` is not tracked by git**

Run:
```bash
git check-ignore -v config.toml
```
Expected: one line confirming `.gitignore` is matching it (e.g. `.gitignore:14:config.toml ...`).

- [ ] **Step 4: Commit the example**

```bash
git add config.toml.example
git commit -m "feat(config): config.toml.example template"
```

---

### Task 6: Cert app-only auth — tests first

**Files:**
- Create: `tests/test_auth.py`
- Create: `src/fazla_od/auth.py`

- [ ] **Step 1: Write failing tests for app-only auth**

Create `tests/test_auth.py`:
```python
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.auth import (
    AppOnlyCredential,
    AuthError,
    CertInfo,
    DelegatedCredential,
    get_cert_info,
)
from fazla_od.config import Config, load_config

LIVE = pytest.mark.skipif(
    os.environ.get("FAZLA_OD_LIVE_TESTS") != "1",
    reason="live Graph test; set FAZLA_OD_LIVE_TESTS=1 to run",
)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    """Minimal valid config pointing at a fake cert file."""
    cert = tmp_path / "fake.key"
    cert.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    pub = tmp_path / "fake.cer"
    pub.write_text("-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n")
    toml = tmp_path / "config.toml"
    toml.write_text(
        f"""
tenant_id    = "tenant-uuid"
client_id    = "client-uuid"
cert_path    = "{cert}"
cert_public  = "{pub}"
default_auth = "app-only"

[scope]
allow_drives = ["me"]
"""
    )
    return load_config(toml)


def test_app_only_acquires_token_using_cert(
    cfg: Config, tmp_path: Path, mocker
) -> None:
    # Stub the cert thumbprint helper — we don't want to parse a fake cert here.
    mocker.patch(
        "fazla_od.auth.get_cert_info",
        return_value=CertInfo(
            subject="CN=Test",
            thumbprint="ABCDEF",
            not_after_utc="2028-04-22T22:12:10Z",
            days_until_expiry=999,
        ),
    )
    mock_app = MagicMock()
    mock_app.acquire_token_for_client.return_value = {"access_token": "t0k3n"}
    mocker.patch("msal.ConfidentialClientApplication", return_value=mock_app)

    cred = AppOnlyCredential(cfg)
    token = cred.get_token()

    assert token == "t0k3n"
    mock_app.acquire_token_for_client.assert_called_once_with(
        scopes=["https://graph.microsoft.com/.default"]
    )


def test_app_only_raises_on_msal_error(cfg: Config, mocker) -> None:
    mocker.patch(
        "fazla_od.auth.get_cert_info",
        return_value=CertInfo("CN=x", "AB", "2028-01-01T00:00:00Z", 900),
    )
    mock_app = MagicMock()
    mock_app.acquire_token_for_client.return_value = {
        "error": "invalid_client",
        "error_description": "cert not uploaded",
    }
    mocker.patch("msal.ConfidentialClientApplication", return_value=mock_app)

    cred = AppOnlyCredential(cfg)
    with pytest.raises(AuthError, match="invalid_client"):
        cred.get_token()


@LIVE
def test_live_app_only_against_fazla_tenant() -> None:
    """Smoke test: real cert, real Entra. Requires config.toml + cert on disk."""
    cfg = load_config(Path("config.toml"))
    cred = AppOnlyCredential(cfg)
    token = cred.get_token()
    assert isinstance(token, str) and len(token) > 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_auth.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'fazla_od.auth'`.

- [ ] **Step 3: Implement app-only half of `auth.py`**

Create `src/fazla_od/auth.py`:
```python
"""MSAL-backed authentication for fazla_od.

Two flows, both using the same Azure AD app registration:

- ``AppOnlyCredential``: certificate-based client_credentials. Used for
  tenant-wide unattended operations.
- ``DelegatedCredential``: device-code flow with persistent token cache.
  Used when operations should be attributed to the signed-in user.

Delegated credential is added in a later step; this file is split by
concern so the mock-based unit tests can land independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import msal
from cryptography import x509
from cryptography.hazmat.primitives import hashes

from fazla_od.config import Config

GRAPH_SCOPES_APP_ONLY = ["https://graph.microsoft.com/.default"]


class AuthError(RuntimeError):
    """Raised when token acquisition fails."""


@dataclass(frozen=True)
class CertInfo:
    subject: str
    thumbprint: str  # SHA-1, uppercase hex, no colons
    not_after_utc: str  # ISO-8601
    days_until_expiry: int


def get_cert_info(cert_public: Path) -> CertInfo:
    """Parse a PEM cert and return its identifying metadata."""
    pem = cert_public.read_bytes()
    cert = x509.load_pem_x509_certificate(pem)
    thumb = cert.fingerprint(hashes.SHA1()).hex().upper()
    not_after = cert.not_valid_after_utc
    days = (not_after - datetime.now(timezone.utc)).days
    return CertInfo(
        subject=cert.subject.rfc4514_string(),
        thumbprint=thumb,
        not_after_utc=not_after.isoformat(),
        days_until_expiry=days,
    )


class AppOnlyCredential:
    """Certificate-based client_credentials flow for tenant-wide ops."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._info = get_cert_info(cfg.cert_public)

    @property
    def cert_info(self) -> CertInfo:
        return self._info

    def _build_app(self) -> msal.ConfidentialClientApplication:
        private_key = self._cfg.cert_path.read_text()
        client_credential = {
            "private_key": private_key,
            "thumbprint": self._info.thumbprint,
            "public_certificate": self._cfg.cert_public.read_text(),
        }
        authority = f"https://login.microsoftonline.com/{self._cfg.tenant_id}"
        return msal.ConfidentialClientApplication(
            client_id=self._cfg.client_id,
            authority=authority,
            client_credential=client_credential,
        )

    def get_token(self) -> str:
        app = self._build_app()
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPES_APP_ONLY)
        if "access_token" not in result:
            err = result.get("error", "unknown")
            desc = result.get("error_description", "")
            raise AuthError(f"app-only auth failed: {err}: {desc}")
        return result["access_token"]


# DelegatedCredential is added in Task 7.
class DelegatedCredential:  # placeholder — implemented in Task 7
    def __init__(self, cfg: Config) -> None:
        raise NotImplementedError("DelegatedCredential lands in Task 7")
```

- [ ] **Step 4: Run tests to verify app-only tests pass**

Run:
```bash
uv run pytest tests/test_auth.py -k "app_only" -v
```
Expected: the two mocked `test_app_only_*` tests PASS. The live test is SKIPPED.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/auth.py tests/test_auth.py
git commit -m "feat(auth): cert-based app-only credential with MSAL"
```

---

### Task 7: Delegated (device-code) auth with persistent cache

**Files:**
- Modify: `src/fazla_od/auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: Add failing tests for delegated credential**

Append to `tests/test_auth.py`:
```python
def test_delegated_login_uses_device_code_flow(cfg: Config, mocker) -> None:
    mock_app = MagicMock()
    mock_app.initiate_device_flow.return_value = {
        "user_code": "ABCD-1234",
        "device_code": "dev-code",
        "verification_uri": "https://microsoft.com/devicelogin",
        "message": "Go to https://microsoft.com/devicelogin and enter ABCD-1234",
        "expires_in": 900,
        "interval": 5,
    }
    mock_app.acquire_token_by_device_flow.return_value = {
        "access_token": "delegated-t0k3n",
        "id_token_claims": {"preferred_username": "test@fazla.com"},
    }
    mock_app.get_accounts.return_value = []
    mocker.patch("msal.PublicClientApplication", return_value=mock_app)
    mocker.patch("fazla_od.auth._load_persistent_cache", return_value=None)

    printed: list[str] = []
    cred = DelegatedCredential(cfg, prompt=lambda msg: printed.append(msg))
    token = cred.login()

    assert token == "delegated-t0k3n"
    assert any("ABCD-1234" in m for m in printed)
    mock_app.acquire_token_by_device_flow.assert_called_once()


def test_delegated_get_token_uses_cached_account(cfg: Config, mocker) -> None:
    mock_app = MagicMock()
    mock_app.get_accounts.return_value = [{"username": "cached@fazla.com"}]
    mock_app.acquire_token_silent.return_value = {"access_token": "cached-t0k3n"}
    mocker.patch("msal.PublicClientApplication", return_value=mock_app)
    mocker.patch("fazla_od.auth._load_persistent_cache", return_value=None)

    cred = DelegatedCredential(cfg)
    token = cred.get_token()

    assert token == "cached-t0k3n"
    mock_app.initiate_device_flow.assert_not_called()


def test_delegated_get_token_raises_when_not_logged_in(cfg: Config, mocker) -> None:
    mock_app = MagicMock()
    mock_app.get_accounts.return_value = []
    mocker.patch("msal.PublicClientApplication", return_value=mock_app)
    mocker.patch("fazla_od.auth._load_persistent_cache", return_value=None)

    cred = DelegatedCredential(cfg)
    with pytest.raises(AuthError, match="not logged in"):
        cred.get_token()
```

- [ ] **Step 2: Run to confirm they fail**

Run:
```bash
uv run pytest tests/test_auth.py -k "delegated" -v
```
Expected: 3 tests FAIL — the first two with `NotImplementedError`, the third same.

- [ ] **Step 3: Replace the `DelegatedCredential` placeholder**

Two edits to `src/fazla_od/auth.py`:

**(a)** Add these imports at the top of the file, next to the existing imports (not inside the class below):
```python
import os
from typing import Callable
```

**(b)** Delete the `DelegatedCredential` placeholder at the bottom (the `raise NotImplementedError(...)` one), and in its place add the following block of constants, helpers, and class:

```python

GRAPH_SCOPES_DELEGATED = [
    "Files.ReadWrite.All",
    "Sites.ReadWrite.All",
    "User.Read",
]

_CACHE_DIR = Path.home() / ".config" / "fazla-od"
_CACHE_FILE = _CACHE_DIR / "token_cache.bin"


def _load_persistent_cache() -> msal.SerializableTokenCache | None:
    """Load the MSAL token cache from disk, if present.

    We use a plain file at mode 600 rather than msal-extensions' Keychain
    integration because the latter has historical stability issues on
    macOS. The file sits in ~/.config/fazla-od/ alongside the cert and
    inherits the directory's 700 permissions.
    """
    cache = msal.SerializableTokenCache()
    if _CACHE_FILE.exists():
        cache.deserialize(_CACHE_FILE.read_text())
    return cache


def _persist_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    _CACHE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    _CACHE_FILE.write_text(cache.serialize())
    os.chmod(_CACHE_FILE, 0o600)


class DelegatedCredential:
    """Device-code flow with persistent token cache."""

    def __init__(
        self,
        cfg: Config,
        prompt: Callable[[str], None] = print,
    ) -> None:
        self._cfg = cfg
        self._prompt = prompt
        self._cache = _load_persistent_cache() or msal.SerializableTokenCache()
        authority = f"https://login.microsoftonline.com/{cfg.tenant_id}"
        self._app = msal.PublicClientApplication(
            client_id=cfg.client_id,
            authority=authority,
            token_cache=self._cache,
        )

    def login(self) -> str:
        flow = self._app.initiate_device_flow(scopes=GRAPH_SCOPES_DELEGATED)
        if "user_code" not in flow:
            raise AuthError(f"could not start device flow: {flow!r}")
        self._prompt(flow["message"])
        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            err = result.get("error", "unknown")
            desc = result.get("error_description", "")
            raise AuthError(f"device-code auth failed: {err}: {desc}")
        _persist_cache(self._cache)
        return result["access_token"]

    def get_token(self) -> str:
        accounts = self._app.get_accounts()
        if not accounts:
            raise AuthError("not logged in; run `od-auth login` first")
        result = self._app.acquire_token_silent(
            scopes=GRAPH_SCOPES_DELEGATED, account=accounts[0]
        )
        if not result or "access_token" not in result:
            raise AuthError(
                "cached token could not be refreshed; run `od-auth login` again"
            )
        _persist_cache(self._cache)
        return result["access_token"]

    def logout(self) -> None:
        for acc in self._app.get_accounts():
            self._app.remove_account(acc)
        _persist_cache(self._cache)
```

Remove the `class DelegatedCredential:  # placeholder ...` block at the bottom of the file.

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_auth.py -v
```
Expected: 5 PASS (2 app-only + 3 delegated), 1 SKIP (live).

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/auth.py tests/test_auth.py
git commit -m "feat(auth): delegated device-code credential with persistent cache"
```

---

### Task 8: Graph client wrapper

**Files:**
- Create: `src/fazla_od/graph.py`
- Create: `tests/test_graph.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_graph.py`:
```python
from __future__ import annotations

import httpx
import pytest

from fazla_od.graph import GraphClient, GraphError


def test_get_attaches_bearer_token() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = GraphClient(token_provider=lambda: "abc123", transport=transport)

    result = client.get("/me")

    assert result == {"ok": True}
    assert captured["auth"] == "Bearer abc123"


def test_get_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"code": "InvalidAuthenticationToken", "message": "bad"}},
        )

    client = GraphClient(
        token_provider=lambda: "x", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GraphError, match="InvalidAuthenticationToken"):
        client.get("/me")
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_graph.py -v
```
Expected: `ModuleNotFoundError: No module named 'fazla_od.graph'`.

- [ ] **Step 3: Implement `graph.py`**

Create `src/fazla_od/graph.py`:
```python
"""Thin httpx-backed Microsoft Graph client.

Intentionally minimal in Plan 1 — just enough to call /me and /applications
for whoami. Plan 2 will either extend this or swap to msgraph-sdk; the
interface here (a single ``get`` returning parsed JSON) is chosen so either
path is straightforward.
"""
from __future__ import annotations

from typing import Callable

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    """Raised when Graph returns a non-2xx response."""


class GraphClient:
    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._token_provider = token_provider
        self._client = httpx.Client(
            base_url=GRAPH_BASE,
            transport=transport,
            timeout=timeout,
        )

    def get(self, path: str, *, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token_provider()}"}
        resp = self._client.get(path, headers=headers, params=params)
        if resp.status_code >= 400:
            body = resp.json() if resp.content else {}
            err = body.get("error", {})
            code = err.get("code", f"HTTP{resp.status_code}")
            msg = err.get("message", resp.text[:200])
            raise GraphError(f"{code}: {msg}")
        return resp.json()

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_graph.py -v
```
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/fazla_od/graph.py tests/test_graph.py
git commit -m "feat(graph): minimal httpx Graph client with bearer-token helper"
```

---

### Task 9: `auth login` / `auth whoami` subcommands

**Files:**
- Create: `src/fazla_od/cli/auth.py`
- Modify: `src/fazla_od/cli/__main__.py`
- Create: `tests/test_cli_auth.py`

- [ ] **Step 1: Write failing tests for `whoami`**

Create `tests/test_cli_auth.py`:
```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from fazla_od.cli.auth import run_whoami


def test_whoami_prints_both_flows(tmp_path: Path, mocker, capsys) -> None:
    # Patch the high-level moving parts: config load, credentials, Graph.
    cfg = MagicMock()
    cfg.tenant_id = "tenant-uuid"
    cfg.client_id = "client-uuid"
    cfg.cert_path = tmp_path / "k"
    cfg.cert_public = tmp_path / "c"
    mocker.patch("fazla_od.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.return_value = "deleg"
    mocker.patch("fazla_od.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=FazlaODToolkit"
    app_only.cert_info.thumbprint = "ABCDEF"
    app_only.cert_info.days_until_expiry = 728
    app_only.cert_info.not_after_utc = "2028-04-22T22:12:10+00:00"
    app_only.get_token.return_value = "app"
    mocker.patch("fazla_od.cli.auth.AppOnlyCredential", return_value=app_only)

    graph = MagicMock()
    graph.get.side_effect = [
        {"displayName": "Arda Eren", "userPrincipalName": "arda@fazla.com"},
        {"displayName": "FazlaODToolkit"},
    ]
    mocker.patch("fazla_od.cli.auth.GraphClient", return_value=graph)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "Arda Eren" in out
    assert "arda@fazla.com" in out
    assert "FazlaODToolkit" in out
    assert "ABCDEF" in out
    assert "728" in out
    assert "tenant-uuid" in out


def test_whoami_reports_not_logged_in(tmp_path: Path, mocker, capsys) -> None:
    from fazla_od.auth import AuthError

    cfg = MagicMock()
    cfg.tenant_id = "t"
    cfg.client_id = "c"
    cfg.cert_public = tmp_path / "c"
    cfg.cert_path = tmp_path / "k"
    mocker.patch("fazla_od.cli.auth.load_config", return_value=cfg)

    delegated = MagicMock()
    delegated.get_token.side_effect = AuthError("not logged in; run `od-auth login` first")
    mocker.patch("fazla_od.cli.auth.DelegatedCredential", return_value=delegated)

    app_only = MagicMock()
    app_only.cert_info.subject = "CN=x"
    app_only.cert_info.thumbprint = "AB"
    app_only.cert_info.days_until_expiry = 700
    app_only.cert_info.not_after_utc = "2028-01-01T00:00:00+00:00"
    app_only.get_token.return_value = "app"
    mocker.patch("fazla_od.cli.auth.AppOnlyCredential", return_value=app_only)

    graph = MagicMock()
    graph.get.return_value = {"displayName": "FazlaODToolkit"}
    mocker.patch("fazla_od.cli.auth.GraphClient", return_value=graph)

    rc = run_whoami(config_path=tmp_path / "config.toml")
    out = capsys.readouterr().out

    assert rc == 0
    assert "not logged in" in out.lower()
    assert "FazlaODToolkit" in out  # app-only still works
```

- [ ] **Step 2: Verify they fail**

Run:
```bash
uv run pytest tests/test_cli_auth.py -v
```
Expected: `ModuleNotFoundError: No module named 'fazla_od.cli.auth'`.

- [ ] **Step 3: Implement `cli/auth.py`**

Create `src/fazla_od/cli/auth.py`:
```python
"""`od-auth` subcommands: login and whoami."""
from __future__ import annotations

import argparse
from pathlib import Path

from fazla_od.auth import (
    AppOnlyCredential,
    AuthError,
    DelegatedCredential,
)
from fazla_od.config import load_config
from fazla_od.graph import GraphClient


def run_login(config_path: Path) -> int:
    cfg = load_config(config_path)
    cred = DelegatedCredential(cfg)
    token = cred.login()
    print(f"Logged in. Token length: {len(token)}. Cache persisted.")
    return 0


def run_whoami(config_path: Path) -> int:
    cfg = load_config(config_path)

    print("Fazla OneDrive Toolkit")
    print("======================")
    print(f"Tenant:                {cfg.tenant_id}")

    # --- Delegated flow --------------------------------------------------
    delegated = DelegatedCredential(cfg)
    try:
        token = delegated.get_token()
        graph = GraphClient(token_provider=lambda: token)
        me = graph.get("/me")
        display = me.get("displayName", "?")
        upn = me.get("userPrincipalName", "?")
        print(f"Delegated identity:    {display} <{upn}>")
    except AuthError as e:
        print(f"Delegated identity:    (not available — {e})")

    # --- App-only flow ---------------------------------------------------
    app_only = AppOnlyCredential(cfg)
    info = app_only.cert_info
    try:
        token = app_only.get_token()
        graph = GraphClient(token_provider=lambda: token)
        app = graph.get(f"/applications(appId='{cfg.client_id}')")
        app_name = app.get("displayName", "?")
        print(f"App-only identity:     {app_name} (appId {cfg.client_id})")
    except AuthError as e:
        print(f"App-only identity:     (not available — {e})")

    print(
        f"App-only cert:         {info.subject}, "
        f"thumbprint {info.thumbprint}, "
        f"expires {info.not_after_utc} ({info.days_until_expiry} days)"
    )

    if info.days_until_expiry < 60:
        print(
            f"  ⚠️  Cert expires in {info.days_until_expiry} days — rotate soon."
        )

    print(f"Catalog:               not yet built (Plan 2)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="od-auth")
    p.add_argument(
        "--config",
        default="config.toml",
        help="Path to config.toml (default: config.toml in current dir)",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("login", help="Device-code sign-in; caches token.")
    sub.add_parser("whoami", help="Print identity, scopes, cert expiry.")
    return p


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    config_path = Path(args.config)
    if args.subcommand == "login":
        return run_login(config_path)
    if args.subcommand == "whoami":
        return run_whoami(config_path)
    return 2
```

- [ ] **Step 4: Wire the subcommand into `cli/__main__.py`**

Replace the contents of `src/fazla_od/cli/__main__.py`:
```python
"""fazla-od command dispatcher.

The single Python entry point is ``fazla-od``; individual ``od-*`` names
are produced by POSIX shell wrappers in ``bin/`` that translate e.g.
``od-auth whoami`` into ``fazla-od auth whoami``.
"""
from __future__ import annotations

import sys

from fazla_od.cli import auth as auth_cli

_SUBCOMMANDS = {
    "auth": auth_cli.main,
}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in {"-h", "--help"}:
        print("usage: fazla-od <subcommand> [args...]")
        print(f"  subcommands: {', '.join(_SUBCOMMANDS)}")
        return 0 if argv else 2
    sub = argv[0]
    if sub not in _SUBCOMMANDS:
        print(f"unknown subcommand: {sub}", file=sys.stderr)
        return 2
    return _SUBCOMMANDS[sub](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_cli_auth.py -v
```
Expected: 2 tests PASS.

- [ ] **Step 6: Full test-suite sanity pass**

Run:
```bash
uv run pytest -v
```
Expected: 14 passed, 1 skipped (the live test).

Breakdown:
- `tests/test_config.py`: 5 passed
- `tests/test_auth.py`: 5 passed + 1 skipped (live)
- `tests/test_graph.py`: 2 passed
- `tests/test_cli_auth.py`: 2 passed

- [ ] **Step 7: Commit**

```bash
git add src/fazla_od/cli/ tests/test_cli_auth.py
git commit -m "feat(cli): od-auth login / whoami subcommands"
```

---

### Task 10: `bin/od-auth` shell wrapper

**Files:**
- Create: `bin/od-auth`

- [ ] **Step 1: Write the wrapper**

Create `bin/od-auth` with exactly:
```bash
#!/usr/bin/env bash
# od-auth — dispatch to fazla-od auth subcommand.
# The wrapper lives here rather than in pyproject [project.scripts] so
# users can invoke `./bin/od-auth` directly from a repo clone without
# installing the package, and so future tools added under `bin/` use a
# consistent pattern.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec uv run --project "$REPO" python -m fazla_od.cli auth "$@"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x bin/od-auth
```

- [ ] **Step 3: Smoke test (no auth required)**

Run:
```bash
./bin/od-auth --help
```
Expected output contains `usage: od-auth` and lists `login` and `whoami` subcommands.

- [ ] **Step 4: Commit**

```bash
git add bin/od-auth
git commit -m "feat(cli): bin/od-auth bash wrapper"
```

---

### Task 11: Write `AGENTS.md` v1

**Files:**
- Create: `AGENTS.md`

- [ ] **Step 1: Write it**

```markdown
# AGENTS.md — Fazla OneDrive Toolkit

Notes for Claude Code (and any agentic assistant) operating this repo.

## What this is

A CLI for admin-scoped control of the Fazla M365 tenant's OneDrive + SharePoint content via Microsoft Graph. The full design is in `docs/superpowers/specs/2026-04-24-fazla-onedrive-toolkit-design.md`. Plans are under `docs/superpowers/plans/`.

## Current CLI surface (Plan 1 complete)

Only one command exists yet:

| Command | Purpose |
|---|---|
| `./bin/od-auth login` | Device-code delegated sign-in; caches token. |
| `./bin/od-auth whoami` | Identity (delegated + app-only), cert expiry, tenant. |

All other commands from the spec (`od-search`, `od-inventory`, `od-move`, …) are delivered in later plans.

## Safety model (already in effect)

- `config.toml` is **gitignored**. Never `git add` it. The tracked template is `config.toml.example`.
- Cert private key is at `~/.config/fazla-od/fazla-od.key` (mode 600) — outside this repo. Never read, cat, or commit it.
- `cache/`, `workspaces/`, `logs/` are gitignored runtime dirs.

When mutating commands ship (Plan 4):
- `--dry-run` is always the default; `--confirm` is required to execute.
- Bulk ops require the plan-file workflow (`--plan-out` → review → `--from-plan`).
- See spec §7 for the full model. Follow it.

## Authentication at a glance

- **Delegated** (`./bin/od-auth login`): device-code; user signs in once, token cached in `~/.config/fazla-od/token_cache.bin`.
- **App-only**: certificate-based, zero user interaction per run. Used automatically by commands that need tenant-wide access.

Both flows run against the same Entra app; admin consent is granted for both.

## Running tests

```bash
uv sync --extra dev
uv run pytest          # unit + mocked
FAZLA_OD_LIVE_TESTS=1 uv run pytest -m live    # hits real Graph
```
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: AGENTS.md v1 (Plan 1 surface)"
```

---

### Task 12: End-to-end live smoke test

This task has **no code** — it verifies the real system works against the Fazla tenant. Cannot be done on CI; must be done on the user's machine with the cert present.

- [ ] **Step 1: Pre-flight checks**

Run:
```bash
ls -la ~/.config/fazla-od/
test -r ~/.config/fazla-od/fazla-od.key && echo "key readable"
test -r ~/.config/fazla-od/fazla-od.cer && echo "cer readable"
```
Expected: `fazla-od.key` (mode -rw-------), `fazla-od.cer` (mode -rw-r--r--), both "readable" printed.

- [ ] **Step 2: Confirm `config.toml` exists and points at real values**

Run:
```bash
grep -E "^(tenant_id|client_id|cert_path|cert_public)" config.toml
```
Expected: tenant_id=`361efb70-...`, client_id=`b22e6fd3-...`, both cert paths resolving under `~/.config/fazla-od/`.

- [ ] **Step 3: App-only flow (no browser needed)**

Run:
```bash
FAZLA_OD_LIVE_TESTS=1 uv run pytest tests/test_auth.py -m live -v
```
Expected: `test_live_app_only_against_fazla_tenant PASSED`.

If it fails with `invalid_client` or `AADSTS700016`:
- Confirm cert thumbprint in Entra matches `C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF`.
- Confirm admin consent is still granted (green checks on all permissions).

- [ ] **Step 4: Delegated login flow**

Run:
```bash
./bin/od-auth login
```
Expected: prints a message like `To sign in, use a web browser to open the page https://microsoft.com/devicelogin and enter the code XXXX-YYYY to authenticate.`
Open the URL, enter the code, complete sign-in with your Fazla tenant account.
Final line: `Logged in. Token length: NNNN. Cache persisted.`

- [ ] **Step 5: Whoami**

Run:
```bash
./bin/od-auth whoami
```
Expected output shape:
```
Fazla OneDrive Toolkit
======================
Tenant:                361efb70-ca20-41ae-b204-9045df001350
Delegated identity:    <Your Name> <you@fazla.com>
App-only identity:     FazlaODToolkit (appId b22e6fd3-...)
App-only cert:         CN=FazlaODToolkit, thumbprint C38CC9B49D5E4D326B4A79ECAF33CD65B008BCBF, expires 2028-04-22T22:12:10+00:00 (NNN days)
Catalog:               not yet built (Plan 2)
```

- [ ] **Step 6: Verify token cache exists and is locked down**

Run:
```bash
ls -la ~/.config/fazla-od/token_cache.bin
```
Expected: mode `-rw-------` (0600), owner = you.

- [ ] **Step 7: Verify the token cache is *not* in the repo**

Run:
```bash
git status --porcelain
```
Expected: empty output (everything committed; nothing new under version control).

- [ ] **Step 8: Mark Plan 1 done**

Append to `docs/superpowers/plans/2026-04-24-foundation-and-auth.md`:
```markdown

---

## Completion log

- Smoke test run: 2026-04-24
- Delegated identity confirmed: ✅
- App-only identity confirmed: ✅
- Cert expiry: NNN days (>60, healthy)
```

Commit:
```bash
git add docs/superpowers/plans/2026-04-24-foundation-and-auth.md
git commit -m "chore: Plan 1 complete — auth smoke tests pass"
```

---

## Plan 1 done. What's next?

Plan 2 (Catalog) picks up from here: it depends on `load_config`, `AppOnlyCredential`, `GraphClient`, the `bin/` wrapper pattern, and the AGENTS.md. Nothing else.
