"""CLI tests for `mail delegate {list, grant, revoke}` (Phase 12 G3)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import delegate as cli_delegate
from m365ctl.mail.mutate.delegate import DelegateEntry


def _config(tmp_path: Path) -> Path:
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

[logging]
ops_dir = "{tmp_path / 'logs'}"
""".lstrip()
    )
    return cfg


def _entry(
    *,
    kind: str = "FullAccess",
    delegate: str = "alice@example.com",
    access_rights: str = "FullAccess",
    deny: bool = False,
) -> DelegateEntry:
    return DelegateEntry(
        kind=kind,
        mailbox="team@example.com",
        delegate=delegate,
        access_rights=access_rights,
        deny=deny,
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_calls_list_delegates_and_prints_table(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    entries = [
        _entry(kind="FullAccess", delegate="alice@example.com",
               access_rights="FullAccess"),
        _entry(kind="SendAs", delegate="bob@example.com",
               access_rights="SendAs"),
    ]
    with patch(
        "m365ctl.mail.cli.delegate.list_delegates", return_value=entries
    ) as mock_list:
        rc = cli_delegate.main(
            ["--config", str(cfg), "list", "team@example.com"]
        )
    assert rc == 0
    mock_list.assert_called_once_with("team@example.com")
    out = capsys.readouterr().out
    assert "alice@example.com" in out
    assert "bob@example.com" in out
    assert "FullAccess" in out
    assert "SendAs" in out


def test_list_json_emits_ndjson(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    entries = [
        _entry(kind="FullAccess", delegate="alice@example.com",
               access_rights="FullAccess"),
        _entry(kind="SendOnBehalf", delegate="carol@example.com",
               access_rights="SendOnBehalf"),
    ]
    with patch(
        "m365ctl.mail.cli.delegate.list_delegates", return_value=entries
    ):
        rc = cli_delegate.main(
            ["--config", str(cfg), "list", "team@example.com", "--json"]
        )
    assert rc == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["delegate"] == "alice@example.com"
    assert parsed[0]["kind"] == "FullAccess"
    assert parsed[1]["delegate"] == "carol@example.com"
    assert parsed[1]["access_rights"] == "SendOnBehalf"


# ---------------------------------------------------------------------------
# grant
# ---------------------------------------------------------------------------

def test_grant_with_confirm_calls_execute_grant(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    fake = MagicMock()
    fake.status = "ok"
    fake.error = None
    fake.after = {}
    with patch(
        "m365ctl.mail.cli.delegate.execute_grant", return_value=fake
    ) as mock_grant:
        rc = cli_delegate.main([
            "--config", str(cfg),
            "grant", "team@example.com",
            "--to", "alice@example.com",
            "--rights", "FullAccess",
            "--confirm",
        ])
    assert rc == 0
    mock_grant.assert_called_once()
    op_arg = mock_grant.call_args[0][0]
    assert op_arg.action == "mail.delegate.grant"
    assert op_arg.args["mailbox"] == "team@example.com"
    assert op_arg.args["delegate"] == "alice@example.com"
    assert op_arg.args["access_rights"] == "FullAccess"


def test_grant_without_confirm_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    with patch(
        "m365ctl.mail.cli.delegate.execute_grant"
    ) as mock_grant:
        rc = cli_delegate.main([
            "--config", str(cfg),
            "grant", "team@example.com",
            "--to", "alice@example.com",
        ])
    assert rc == 2
    mock_grant.assert_not_called()
    err = capsys.readouterr().err
    assert "--confirm" in err


def test_grant_send_on_behalf_passes_access_rights(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    fake = MagicMock()
    fake.status = "ok"
    fake.error = None
    fake.after = {}
    with patch(
        "m365ctl.mail.cli.delegate.execute_grant", return_value=fake
    ) as mock_grant:
        rc = cli_delegate.main([
            "--config", str(cfg),
            "grant", "team@example.com",
            "--to", "alice@example.com",
            "--rights", "SendOnBehalf",
            "--confirm",
        ])
    assert rc == 0
    op_arg = mock_grant.call_args[0][0]
    assert op_arg.args["access_rights"] == "SendOnBehalf"


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------

def test_revoke_with_confirm_calls_execute_revoke(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    fake = MagicMock()
    fake.status = "ok"
    fake.error = None
    fake.after = {}
    with patch(
        "m365ctl.mail.cli.delegate.execute_revoke", return_value=fake
    ) as mock_revoke:
        rc = cli_delegate.main([
            "--config", str(cfg),
            "revoke", "team@example.com",
            "--to", "alice@example.com",
            "--confirm",
        ])
    assert rc == 0
    mock_revoke.assert_called_once()
    op_arg = mock_revoke.call_args[0][0]
    assert op_arg.action == "mail.delegate.revoke"
    assert op_arg.args["mailbox"] == "team@example.com"
    assert op_arg.args["delegate"] == "alice@example.com"
    assert op_arg.args["access_rights"] == "FullAccess"
