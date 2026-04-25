"""CLI tests for `mail sendas` (Phase 13)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import sendas as cli_sendas
from m365ctl.mail.mutate._common import MailResult


def _config(tmp_path: Path, *, allow_mailboxes: list[str] | None = None) -> Path:
    if allow_mailboxes is None:
        allow_mailboxes = ["upn:bob@example.com"]
    cfg = tmp_path / "config.toml"
    am = ", ".join(f'"{a}"' for a in allow_mailboxes)
    cfg.write_text(
        f"""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "{tmp_path / 'c.pem'}"
cert_public  = "{tmp_path / 'p.cer'}"
default_auth = "app-only"

[scope]
allow_drives    = ["me"]
allow_mailboxes = [{am}]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"

[logging]
ops_dir = "{tmp_path / 'logs'}"
""".lstrip()
    )
    # Touch cert files (loaders may stat them).
    (tmp_path / "c.pem").write_text("")
    (tmp_path / "p.cer").write_text("")
    return cfg


def _fake_ok() -> MailResult:
    return MailResult(op_id="op-test", status="ok",
                      after={"sent_at": "2026-04-25T00:00:00+00:00",
                             "effective_sender": "bob@example.com",
                             "authenticated_principal": "11111111"})


def _patch_cred_and_graph():
    """Common patches: AppOnlyCredential.get_token + GraphClient + executor."""
    cred = MagicMock()
    cred.get_token.return_value = "fake-token"
    return (
        patch("m365ctl.mail.cli.sendas.AppOnlyCredential", return_value=cred),
        patch("m365ctl.mail.cli.sendas.GraphClient", return_value=MagicMock()),
    )


def test_sendas_with_confirm_calls_executor_with_right_op(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cred_p, graph_p = _patch_cred_and_graph()
    with cred_p, graph_p, patch(
        "m365ctl.mail.cli.sendas.execute_send_as", return_value=_fake_ok()
    ) as mock_exec:
        rc = cli_sendas.main([
            "--config", str(cfg),
            "bob@example.com",
            "--to", "alice@example.com",
            "--subject", "s",
            "--body", "b",
            "--confirm",
        ])
    assert rc == 0
    mock_exec.assert_called_once()
    op = mock_exec.call_args.args[0]
    assert op.action == "mail.send.as"
    assert op.drive_id == "bob@example.com"
    assert op.args["from_upn"] == "bob@example.com"
    assert op.args["to"] == ["alice@example.com"]
    assert op.args["subject"] == "s"
    assert op.args["body"] == "b"
    assert op.args["authenticated_principal"] == (
        "11111111-1111-1111-1111-111111111111"
    )


def test_sendas_without_confirm_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    with patch("m365ctl.mail.cli.sendas.execute_send_as") as mock_exec:
        rc = cli_sendas.main([
            "--config", str(cfg),
            "bob@example.com",
            "--to", "alice@example.com",
            "--subject", "s",
            "--body", "b",
        ])
    assert rc == 2
    mock_exec.assert_not_called()
    err = capsys.readouterr().err
    assert "--confirm" in err


def test_sendas_in_scope_proceeds_without_unsafe_scope(tmp_path: Path) -> None:
    cfg = _config(tmp_path, allow_mailboxes=["upn:bob@example.com"])
    cred_p, graph_p = _patch_cred_and_graph()
    with cred_p, graph_p, patch(
        "m365ctl.mail.cli.sendas.execute_send_as", return_value=_fake_ok()
    ) as mock_exec:
        rc = cli_sendas.main([
            "--config", str(cfg),
            "bob@example.com",
            "--to", "alice@example.com",
            "--subject", "s",
            "--body", "b",
            "--confirm",
        ])
    assert rc == 0
    mock_exec.assert_called_once()


def test_sendas_out_of_scope_without_unsafe_returns_2(
    tmp_path: Path, capsys
) -> None:
    cfg = _config(tmp_path, allow_mailboxes=["upn:other@example.com"])
    with patch("m365ctl.mail.cli.sendas.execute_send_as") as mock_exec:
        rc = cli_sendas.main([
            "--config", str(cfg),
            "bob@example.com",
            "--to", "alice@example.com",
            "--subject", "s",
            "--body", "b",
            "--confirm",
        ])
    assert rc == 2
    mock_exec.assert_not_called()
    err = capsys.readouterr().err
    assert "scope" in err.lower() or "allow_mailboxes" in err


def test_sendas_out_of_scope_with_unsafe_prompts_tty_and_proceeds(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path, allow_mailboxes=["upn:other@example.com"])
    cred_p, graph_p = _patch_cred_and_graph()
    with cred_p, graph_p, patch(
        "m365ctl.common.safety._confirm_via_tty", return_value=True
    ) as mock_tty, patch(
        "m365ctl.mail.cli.sendas.execute_send_as", return_value=_fake_ok()
    ) as mock_exec:
        rc = cli_sendas.main([
            "--config", str(cfg),
            "bob@example.com",
            "--to", "alice@example.com",
            "--subject", "s",
            "--body", "b",
            "--unsafe-scope",
            "--confirm",
        ])
    assert rc == 0
    mock_tty.assert_called_once()
    mock_exec.assert_called_once()


def test_sendas_body_file_html(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    body_file = tmp_path / "msg.html"
    body_file.write_text("<p>html body</p>")
    cred_p, graph_p = _patch_cred_and_graph()
    with cred_p, graph_p, patch(
        "m365ctl.mail.cli.sendas.execute_send_as", return_value=_fake_ok()
    ) as mock_exec:
        rc = cli_sendas.main([
            "--config", str(cfg),
            "bob@example.com",
            "--to", "alice@example.com",
            "--subject", "s",
            "--body-file", str(body_file),
            "--body-type", "html",
            "--confirm",
        ])
    assert rc == 0
    op = mock_exec.call_args.args[0]
    assert op.args["body"] == "<p>html body</p>"
    assert op.args["body_type"] == "html"
