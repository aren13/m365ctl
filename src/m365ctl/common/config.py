"""TOML-backed configuration loader for m365ctl.

The config file is usually at the repo root (`config.toml`); all paths
inside are expanded with `~` -> `$HOME` but are not resolved against the
filesystem (callers decide whether the cert actually has to exist).
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

AuthMode = Literal["delegated", "app-only"]
_VALID_AUTH: tuple[AuthMode, ...] = ("delegated", "app-only")


class ConfigError(ValueError):
    """Raised when config.toml is missing required fields or has invalid values."""


@dataclass(frozen=True)
class ScopeConfig:
    allow_drives: list[str]
    allow_mailboxes: list[str] = field(default_factory=lambda: ["me"])
    allow_users: list[str] = field(default_factory=lambda: ["*"])
    deny_paths: list[str] = field(default_factory=list)
    deny_folders: list[str] = field(default_factory=list)
    unsafe_requires_flag: bool = True
    internal_domain_pattern: str | None = None


@dataclass(frozen=True)
class CatalogConfig:
    path: Path
    refresh_on_start: bool = False


@dataclass(frozen=True)
class MailConfig:
    catalog_path: Path
    default_deleted_folder: str = "Deleted Items"
    default_junk_folder: str = "Junk Email"
    default_drafts_folder: str = "Drafts"
    default_sent_folder: str = "Sent Items"
    default_triage_root: str = "Inbox/Triage"
    categories_master: list[str] = field(default_factory=list)
    signature_path: Path | None = None
    drafts_before_send: bool = True
    schedule_send_enabled: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    ops_dir: Path
    purged_dir: Path = field(default_factory=lambda: Path("logs/purged"))
    retention_days: int = 30


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
    mail: MailConfig = field(
        default_factory=lambda: MailConfig(catalog_path=Path("cache/mail.duckdb"))
    )


def _require(mapping: dict[str, Any], key: str, source: str) -> Any:
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

    idp_raw = scope_raw.get("internal_domain_pattern")
    internal_domain_pattern = str(idp_raw) if idp_raw else None
    scope = ScopeConfig(
        allow_drives=list(allow_drives),
        allow_mailboxes=list(scope_raw.get("allow_mailboxes", ["me"])),
        allow_users=list(scope_raw.get("allow_users", ["*"])),
        deny_paths=list(scope_raw.get("deny_paths", [])),
        deny_folders=list(scope_raw.get("deny_folders", [])),
        unsafe_requires_flag=bool(scope_raw.get("unsafe_requires_flag", True)),
        internal_domain_pattern=internal_domain_pattern,
    )

    catalog_raw = data.get("catalog", {})
    catalog = CatalogConfig(
        path=_expand(catalog_raw.get("path", "cache/catalog.duckdb")),
        refresh_on_start=bool(catalog_raw.get("refresh_on_start", False)),
    )

    mail_raw = data.get("mail", {})
    sig_raw = mail_raw.get("signature_path", "")
    signature_path: Path | None = _expand(sig_raw) if sig_raw else None
    mail = MailConfig(
        catalog_path=_expand(mail_raw.get("catalog_path", "cache/mail.duckdb")),
        default_deleted_folder=str(mail_raw.get("default_deleted_folder", "Deleted Items")),
        default_junk_folder=str(mail_raw.get("default_junk_folder", "Junk Email")),
        default_drafts_folder=str(mail_raw.get("default_drafts_folder", "Drafts")),
        default_sent_folder=str(mail_raw.get("default_sent_folder", "Sent Items")),
        default_triage_root=str(mail_raw.get("default_triage_root", "Inbox/Triage")),
        categories_master=list(mail_raw.get("categories_master", [])),
        signature_path=signature_path,
        drafts_before_send=bool(mail_raw.get("drafts_before_send", True)),
        schedule_send_enabled=bool(mail_raw.get("schedule_send_enabled", False)),
    )

    logging_raw = data.get("logging", {})
    logging_cfg = LoggingConfig(
        ops_dir=_expand(logging_raw.get("ops_dir", "logs/ops")),
        purged_dir=_expand(logging_raw.get("purged_dir", "logs/purged")),
        retention_days=int(logging_raw.get("retention_days", 30)),
    )

    return Config(
        tenant_id=str(tenant_id),
        client_id=str(client_id),
        cert_path=_expand(str(cert_path)),
        cert_public=_expand(str(cert_public)),
        default_auth=default_auth,
        scope=scope,
        catalog=catalog,
        mail=mail,
        logging=logging_cfg,
    )
