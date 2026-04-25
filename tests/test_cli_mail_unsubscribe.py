"""Tests for `m365ctl mail unsubscribe` CLI."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "tenant"
client_id = "client"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"

[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"

[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def _patch_graph(message: dict):
    """Yield a context manager stack patching the GraphClient + auth."""
    cred_patch = patch("m365ctl.mail.cli._common.DelegatedCredential")
    graph_patch = patch("m365ctl.mail.cli.unsubscribe.GraphClient")
    return cred_patch, graph_patch, message


def test_unsubscribe_default_prints_methods(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import unsubscribe as cli_unsub
    msg = {
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe",
             "value": "<https://example.com/u?id=42>, <mailto:unsub@example.com>"},
        ],
    }
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.unsubscribe.GraphClient") as graph_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value.get.return_value = msg
        rc = cli_unsub.main(["--config", str(cfg), "MID-1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "https://example.com/u?id=42" in out
    assert "mailto:unsub@example.com" in out


def test_unsubscribe_http_dry_run(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import unsubscribe as cli_unsub
    msg = {
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe",
             "value": "<https://example.com/u?id=42>"},
        ],
    }
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.unsubscribe.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.unsubscribe.httpx") as httpx_mod:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value.get.return_value = msg
        rc = cli_unsub.main([
            "--config", str(cfg), "MID-1", "--method", "http", "--dry-run",
        ])
    assert rc == 0
    httpx_mod.get.assert_not_called()
    httpx_mod.post.assert_not_called()
    out = capsys.readouterr().out
    assert "would GET https://example.com/u?id=42" in out


def test_unsubscribe_http_confirm_calls_get(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import unsubscribe as cli_unsub
    msg = {
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe",
             "value": "<https://example.com/u?id=42>"},
        ],
    }
    fake_resp = MagicMock(status_code=200)
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.unsubscribe.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.unsubscribe.httpx") as httpx_mod:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value.get.return_value = msg
        httpx_mod.get.return_value = fake_resp
        rc = cli_unsub.main([
            "--config", str(cfg), "MID-1", "--method", "http", "--confirm",
        ])
    assert rc == 0
    httpx_mod.get.assert_called_once()
    httpx_mod.post.assert_not_called()
    args, _kwargs = httpx_mod.get.call_args
    assert args[0] == "https://example.com/u?id=42"


def test_unsubscribe_one_click_posts(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import unsubscribe as cli_unsub
    msg = {
        "internetMessageHeaders": [
            {"name": "List-Unsubscribe",
             "value": "<https://example.com/u?id=42>"},
            {"name": "List-Unsubscribe-Post",
             "value": "List-Unsubscribe=One-Click"},
        ],
    }
    fake_resp = MagicMock(status_code=200)
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.unsubscribe.GraphClient") as graph_cls, \
         patch("m365ctl.mail.cli.unsubscribe.httpx") as httpx_mod:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value.get.return_value = msg
        httpx_mod.post.return_value = fake_resp
        rc = cli_unsub.main([
            "--config", str(cfg), "MID-1", "--method", "http", "--confirm",
        ])
    assert rc == 0
    httpx_mod.post.assert_called_once()
    httpx_mod.get.assert_not_called()
    args, kwargs = httpx_mod.post.call_args
    assert args[0] == "https://example.com/u?id=42"
    assert kwargs["data"] == {"List-Unsubscribe": "One-Click"}


def test_unsubscribe_no_header_returns_0(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    from m365ctl.mail.cli import unsubscribe as cli_unsub
    msg = {"internetMessageHeaders": []}
    with patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls, \
         patch("m365ctl.mail.cli.unsubscribe.GraphClient") as graph_cls:
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value.get.return_value = msg
        rc = cli_unsub.main(["--config", str(cfg), "MID-1"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "(no unsubscribe header)" in err
