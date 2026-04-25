from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import triage as cli_triage


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id = "t"
client_id = "c"
cert_path = "{tmp_path / 'c.pem'}"
cert_public = "{tmp_path / 'p.cer'}"
default_auth = "delegated"
[scope]
allow_drives = ["me"]
allow_mailboxes = ["me"]
[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"
[logging]
ops_dir = "{tmp_path / 'logs'}"
"""
    )
    return cfg


def test_validate_ok(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    rc = cli_triage.main(["validate", str(rules), "--config", str(cfg)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ok" in out.lower() or "valid" in out.lower()


def test_validate_bad(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: bad
    match: { unread: maybe }
    actions: [{ read: true }]
""")
    rc = cli_triage.main(["validate", str(rules), "--config", str(cfg)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unread" in err.lower()


def test_run_with_plan_out_does_not_execute(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: r
    match: { unread: true }
    actions: [{ read: true }]
""")
    plan_out = tmp_path / "plan.json"
    fake_plan = MagicMock()
    fake_plan.operations = []
    with patch(
        "m365ctl.mail.cli.triage.run_emit", return_value=fake_plan,
    ) as emit_mock, patch(
        "m365ctl.mail.cli.triage.GraphClient",
    ), patch(
        "m365ctl.mail.cli.triage.load_and_authorize",
        return_value=(MagicMock(), "delegated", MagicMock(get_token=lambda: "tok")),
    ), patch(
        "m365ctl.mail.cli.triage.make_header_fetcher",
    ):
        rc = cli_triage.main([
            "run", "--rules", str(rules),
            "--plan-out", str(plan_out),
            "--config", str(cfg),
        ])
    assert rc == 0
    emit_mock.assert_called_once()


def test_run_emit_builds_header_fetcher(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    rules = tmp_path / "r.yaml"
    rules.write_text("""
version: 1
mailbox: me
rules:
  - name: hdr
    match: { headers: { name: List-Unsubscribe } }
    actions: [{ move: { to_folder: Archive } }]
""")
    plan_out = tmp_path / "plan.json"
    fake_plan = MagicMock()
    fake_plan.operations = []
    fake_fetcher = MagicMock(name="fetcher")
    with patch(
        "m365ctl.mail.cli.triage.make_header_fetcher",
        return_value=fake_fetcher,
    ) as mhf, patch(
        "m365ctl.mail.cli.triage.GraphClient",
    ), patch(
        "m365ctl.mail.cli.triage.load_and_authorize",
        return_value=(MagicMock(), "delegated", MagicMock(get_token=lambda: "tok")),
    ), patch(
        "m365ctl.mail.cli.triage.run_emit",
        return_value=fake_plan,
    ) as emit_mock:
        rc = cli_triage.main([
            "run", "--rules", str(rules),
            "--plan-out", str(plan_out),
            "--config", str(cfg),
        ])
    assert rc == 0
    mhf.assert_called_once()
    emit_mock.assert_called_once()
    _, kwargs = emit_mock.call_args
    assert kwargs.get("header_fetcher") is fake_fetcher


def test_run_from_plan_requires_confirm(tmp_path: Path, capsys) -> None:
    cfg = _config(tmp_path)
    plan_in = tmp_path / "plan.json"
    plan_in.write_text("{}")  # contents irrelevant; CLI rejects missing --confirm first
    rc = cli_triage.main([
        "run", "--from-plan", str(plan_in),
        "--config", str(cfg),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--confirm" in err
