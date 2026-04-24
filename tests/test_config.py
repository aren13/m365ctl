from pathlib import Path

import pytest

from m365ctl.common.config import Config, ConfigError, load_config


def _valid_toml(tmp_path: Path) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(
        """
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
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
