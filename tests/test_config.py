from pathlib import Path

import pytest

from m365ctl.common.config import Config, ConfigError, load_config


def _valid_toml(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        """
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
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
    assert cfg.tenant_id == "00000000-0000-0000-0000-000000000000"
    assert cfg.client_id == "11111111-1111-1111-1111-111111111111"
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


def test_config_loads_allow_mailboxes_and_deny_folders(tmp_path):
    from m365ctl.common.config import load_config
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me", "shared:ops@example.com"]
allow_users     = ["*"]
deny_paths      = ["/HR/**"]
deny_folders    = ["Archive/Legal/*"]
unsafe_requires_flag = true

[catalog]
path             = "cache/catalog.duckdb"
refresh_on_start = false

[mail]
catalog_path           = "cache/mail.duckdb"
default_deleted_folder = "Deleted Items"
default_junk_folder    = "Junk Email"
default_drafts_folder  = "Drafts"
default_sent_folder    = "Sent Items"
default_triage_root    = "Inbox/Triage"
categories_master      = ["Followup", "Waiting"]
signature_path         = ""
drafts_before_send     = true
schedule_send_enabled  = false

[logging]
ops_dir        = "logs/ops"
purged_dir     = "logs/purged"
retention_days = 30
""".lstrip())
    cfg = load_config(cfg_path)
    assert cfg.scope.allow_mailboxes == ["me", "shared:ops@example.com"]
    assert cfg.scope.deny_folders == ["Archive/Legal/*"]
    assert cfg.mail.catalog_path.name == "mail.duckdb"
    assert cfg.mail.default_triage_root == "Inbox/Triage"
    assert cfg.mail.categories_master == ["Followup", "Waiting"]
    assert cfg.mail.signature_path is None          # empty string -> None
    assert cfg.mail.drafts_before_send is True
    assert cfg.mail.schedule_send_enabled is False
    assert cfg.logging.purged_dir.name == "purged"
    assert cfg.logging.retention_days == 30


def test_scope_internal_domain_pattern_defaults_to_none(tmp_path):
    from m365ctl.common.config import load_config
    cfg = load_config(_valid_toml(tmp_path))
    assert cfg.scope.internal_domain_pattern is None


def test_scope_internal_domain_pattern_loads_from_toml(tmp_path):
    from m365ctl.common.config import load_config
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
default_auth = "delegated"

[scope]
allow_drives            = ["me"]
internal_domain_pattern = "@(contoso|contoso\\\\.onmicrosoft)\\\\."

[catalog]
path = "cache/catalog.duckdb"

[logging]
ops_dir = "logs/ops"
""".lstrip())
    cfg = load_config(cfg_path)
    assert cfg.scope.internal_domain_pattern == "@(contoso|contoso\\.onmicrosoft)\\."


def test_safety_section_defaults_when_omitted(tmp_path):
    cfg = load_config(_valid_toml(tmp_path))
    assert cfg.safety.allow_no_tty_confirm is False


def test_safety_section_loads_allow_no_tty_confirm(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
default_auth = "delegated"

[scope]
allow_drives = ["me"]

[safety]
allow_no_tty_confirm = true

[catalog]
path = "cache/catalog.duckdb"

[logging]
ops_dir = "logs/ops"
""".lstrip())
    cfg = load_config(cfg_path)
    assert cfg.safety.allow_no_tty_confirm is True


def test_config_mail_section_defaults_when_omitted(tmp_path):
    from m365ctl.common.config import load_config
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "~/.config/m365ctl/m365ctl.key"
cert_public  = "~/.config/m365ctl/m365ctl.cer"
default_auth = "delegated"

[scope]
allow_drives = ["me"]

[catalog]
path = "cache/catalog.duckdb"

[logging]
ops_dir = "logs/ops"
""".lstrip())
    cfg = load_config(cfg_path)
    # scope defaults
    assert cfg.scope.allow_mailboxes == ["me"]
    assert cfg.scope.deny_folders == []
    # mail defaults (spec 7.2)
    assert cfg.mail.default_deleted_folder == "Deleted Items"
    assert cfg.mail.default_junk_folder == "Junk Email"
    assert cfg.mail.default_drafts_folder == "Drafts"
    assert cfg.mail.default_sent_folder == "Sent Items"
    assert cfg.mail.default_triage_root == "Inbox/Triage"
    assert cfg.mail.categories_master == []
    assert cfg.mail.signature_path is None
    assert cfg.mail.drafts_before_send is True
    assert cfg.mail.schedule_send_enabled is False
    # mail catalog_path default: "cache/mail.duckdb"
    assert cfg.mail.catalog_path.as_posix().endswith("cache/mail.duckdb")
    # logging defaults
    assert cfg.logging.purged_dir.as_posix().endswith("logs/purged")
    assert cfg.logging.retention_days == 30
