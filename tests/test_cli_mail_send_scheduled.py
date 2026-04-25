"""CLI tests for `mail send --schedule-at` (Phase 5b).

Mocks at the CLI module boundary:
  - `DelegatedCredential` / `AppOnlyCredential` patched in `mail.cli._common`.
  - `GraphClient` patched in `mail.cli.send`.
  - `execute_send_scheduled` patched in `mail.cli.send`.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import send as cli_send


def _write_config(tmp_path: Path, *, schedule_send_enabled: bool = True) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "{tmp_path / 'c.pem'}"
cert_public  = "{tmp_path / 'p.cer'}"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"
drafts_before_send = true
schedule_send_enabled = {str(schedule_send_enabled).lower()}

[logging]
ops_dir = "{tmp_path / 'logs'}"
""".lstrip()
    )
    return cfg


class _Patched:
    def __init__(self):
        self._patches = [
            patch("m365ctl.mail.cli._common.DelegatedCredential"),
            patch("m365ctl.mail.cli._common.AppOnlyCredential"),
            patch("m365ctl.mail.cli.send.GraphClient"),
            patch("m365ctl.mail.cli.send.execute_send_scheduled"),
        ]

    def __enter__(self):
        self.cred_cls = self._patches[0].__enter__()
        self.app_cred_cls = self._patches[1].__enter__()
        self.graph_cls = self._patches[2].__enter__()
        self.execute_send_scheduled = self._patches[3].__enter__()

        self.cred_cls.return_value.get_token.return_value = "tok"
        self.app_cred_cls.return_value.get_token.return_value = "tok"
        self.graph_cls.return_value = MagicMock()

        ok = MagicMock()
        ok.status = "ok"
        ok.error = None
        ok.after = {"sent_at": "now", "schedule_at": "future"}
        self.execute_send_scheduled.return_value = ok
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


def test_schedule_at_confirm_calls_execute(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    future = "2099-01-01T00:00:00+00:00"
    with _Patched() as p:
        rc = cli_send.main([
            "--config", str(cfg),
            "d1", "--schedule-at", future, "--confirm",
        ])
    assert rc == 0
    p.execute_send_scheduled.assert_called_once()
    op = p.execute_send_scheduled.call_args.args[0]
    assert op.action == "mail.send.scheduled"
    assert op.item_id == "d1"
    assert op.args["schedule_at"] == future
    assert op.args["auth_mode"] == "delegated"


def test_schedule_at_without_confirm_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    future = "2099-01-01T00:00:00+00:00"
    with _Patched() as p:
        rc = cli_send.main([
            "--config", str(cfg),
            "d1", "--schedule-at", future,
        ])
    assert rc == 2
    p.execute_send_scheduled.assert_not_called()


def test_schedule_at_garbage_iso_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_send.main([
            "--config", str(cfg),
            "d1", "--schedule-at", "garbage", "--confirm",
        ])
    assert rc == 2
    p.execute_send_scheduled.assert_not_called()
    err = capsys.readouterr().err
    assert "schedule-at" in err.lower() or "iso" in err.lower() or "parse" in err.lower()


def test_schedule_at_past_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    past = "2020-01-01T00:00:00Z"
    with _Patched() as p:
        rc = cli_send.main([
            "--config", str(cfg),
            "d1", "--schedule-at", past, "--confirm",
        ])
    assert rc == 2
    p.execute_send_scheduled.assert_not_called()
    err = capsys.readouterr().err
    assert "future" in err.lower()


def test_schedule_at_disabled_in_config_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path, schedule_send_enabled=False)
    future = "2099-01-01T00:00:00+00:00"
    with _Patched() as p:
        rc = cli_send.main([
            "--config", str(cfg),
            "d1", "--schedule-at", future, "--confirm",
        ])
    assert rc == 2
    p.execute_send_scheduled.assert_not_called()
    err = capsys.readouterr().err
    assert "schedule_send_enabled" in err or "disabled" in err.lower()


def test_schedule_at_with_new_is_mutex_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    future = "2099-01-01T00:00:00+00:00"
    with _Patched() as p:
        rc = cli_send.main([
            "--config", str(cfg),
            "--new", "--schedule-at", future, "--confirm",
            "--subject", "hi", "--body", "x", "--to", "a@example.com",
        ])
    assert rc == 2
    p.execute_send_scheduled.assert_not_called()
