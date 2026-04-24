from pathlib import Path

import pytest

from m365ctl.config import Config, ConfigError, load_config


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
