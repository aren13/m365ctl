"""TOML-backed configuration loader for m365ctl.

The config file is usually at the repo root (`config.toml`); all paths
inside are expanded with `~` -> `$HOME` but are not resolved against the
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
