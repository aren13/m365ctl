"""CLI tests for `mail settings {timezone,working-hours}` setters (Phase 9 G3.1)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import settings as cli_settings


def _write_config(tmp_path: Path) -> Path:
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


class _Patched:
    """Patch credential, graph, and the three settings executors."""

    def __enter__(self):
        self._patches = [
            patch("m365ctl.mail.cli._common.DelegatedCredential"),
            patch("m365ctl.mail.cli._common.AppOnlyCredential"),
            patch("m365ctl.mail.cli.settings.GraphClient"),
            patch("m365ctl.mail.cli.settings.execute_set_timezone"),
            patch("m365ctl.mail.cli.settings.execute_set_working_hours"),
        ]
        self.cred_cls = self._patches[0].__enter__()
        self.app_cred_cls = self._patches[1].__enter__()
        self.graph_cls = self._patches[2].__enter__()
        self.execute_set_timezone = self._patches[3].__enter__()
        self.execute_set_working_hours = self._patches[4].__enter__()

        self.cred_cls.return_value.get_token.return_value = "tok"
        self.app_cred_cls.return_value.get_token.return_value = "tok"
        self.graph_cls.return_value = MagicMock()

        ok = MagicMock()
        ok.status = "ok"
        ok.error = None
        ok.after = {}
        self.execute_set_timezone.return_value = ok
        self.execute_set_working_hours.return_value = ok
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


def test_timezone_confirm_calls_executor(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_settings.main([
            "--config", str(cfg),
            "timezone", "Europe/Istanbul", "--confirm",
        ])
    assert rc == 0
    p.execute_set_timezone.assert_called_once()
    op = p.execute_set_timezone.call_args.args[0]
    assert op.action == "mail.settings.timezone"
    assert op.args["timezone"] == "Europe/Istanbul"
    assert op.args["mailbox_spec"] == "me"
    assert op.args["auth_mode"] == "delegated"


def test_working_hours_yaml_translated_and_executor_called(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    wh_yaml = tmp_path / "wh.yaml"
    wh_yaml.write_text(
        """
days_of_week: [monday, tuesday, wednesday, thursday, friday]
start_time: "09:00:00"
end_time: "17:00:00"
time_zone: "Europe/Istanbul"
""".lstrip()
    )
    with _Patched() as p:
        rc = cli_settings.main([
            "--config", str(cfg),
            "working-hours", "--from-file", str(wh_yaml), "--confirm",
        ])
    assert rc == 0
    p.execute_set_working_hours.assert_called_once()
    op = p.execute_set_working_hours.call_args.args[0]
    assert op.action == "mail.settings.working-hours"
    body = op.args["working_hours"]
    assert body == {
        "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
        "startTime": "09:00:00",
        "endTime": "17:00:00",
        "timeZone": {"name": "Europe/Istanbul"},
    }


def test_timezone_without_confirm_dry_runs(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_settings.main([
            "--config", str(cfg),
            "timezone", "Europe/Istanbul",
        ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "dry-run" in err
    assert "Europe/Istanbul" in err
    p.execute_set_timezone.assert_not_called()


def test_working_hours_malformed_yaml_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    bad = tmp_path / "bad.yaml"
    # Missing required start_time field.
    bad.write_text(
        """
days_of_week: [monday]
end_time: "17:00:00"
time_zone: "Europe/Istanbul"
""".lstrip()
    )
    with _Patched() as p:
        rc = cli_settings.main([
            "--config", str(cfg),
            "working-hours", "--from-file", str(bad), "--confirm",
        ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "start_time" in err
    p.execute_set_working_hours.assert_not_called()


def test_existing_show_subcommand_still_parses() -> None:
    args = cli_settings.build_parser().parse_args(["show"])
    assert args.subcommand == "show"


def test_existing_ooo_subcommand_still_parses() -> None:
    args = cli_settings.build_parser().parse_args(["ooo"])
    assert args.subcommand == "ooo"
