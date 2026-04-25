"""CLI tests for `mail ooo {show, on, off}` (Phase 9 G3.2)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import ooo as cli_ooo
from m365ctl.mail.models import AutomaticRepliesSetting
from m365ctl.mail.mutate.settings import OOOTooLong


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


def _ar(status: str = "disabled") -> AutomaticRepliesSetting:
    return AutomaticRepliesSetting(
        status=status,  # type: ignore[arg-type]
        external_audience="all",
        scheduled_start=None,
        scheduled_end=None,
        internal_reply_message="hi",
        external_reply_message="hi-ext",
    )


class _Patched:
    def __init__(self, *, get_auto_reply_return=None, executor_side_effect=None):
        self.get_auto_reply_return = get_auto_reply_return or _ar()
        self.executor_side_effect = executor_side_effect

    def __enter__(self):
        self._patches = [
            patch("m365ctl.mail.cli._common.DelegatedCredential"),
            patch("m365ctl.mail.cli._common.AppOnlyCredential"),
            patch("m365ctl.mail.cli.ooo.GraphClient"),
            patch("m365ctl.mail.cli.ooo.get_auto_reply",
                  return_value=self.get_auto_reply_return),
            patch("m365ctl.mail.cli.ooo.execute_set_auto_reply"),
        ]
        self.cred_cls = self._patches[0].__enter__()
        self.app_cred_cls = self._patches[1].__enter__()
        self.graph_cls = self._patches[2].__enter__()
        self.get_auto_reply = self._patches[3].__enter__()
        self.execute_set_auto_reply = self._patches[4].__enter__()

        self.cred_cls.return_value.get_token.return_value = "tok"
        self.app_cred_cls.return_value.get_token.return_value = "tok"
        self.graph_cls.return_value = MagicMock()

        ok = MagicMock()
        ok.status = "ok"
        ok.error = None
        ok.after = {}
        if self.executor_side_effect is not None:
            self.execute_set_auto_reply.side_effect = self.executor_side_effect
        else:
            self.execute_set_auto_reply.return_value = ok
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


def test_ooo_show_prints_fields(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    ar = AutomaticRepliesSetting(
        status="alwaysEnabled",
        external_audience="contactsOnly",
        scheduled_start=None,
        scheduled_end=None,
        internal_reply_message="OOO internal",
        external_reply_message="OOO external",
    )
    with _Patched(get_auto_reply_return=ar) as p:
        rc = cli_ooo.main(["--config", str(cfg), "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alwaysEnabled" in out
    assert "contactsOnly" in out
    assert "OOO internal" in out
    p.get_auto_reply.assert_called_once()


def test_ooo_off_confirm_calls_executor_with_disabled(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_ooo.main(["--config", str(cfg), "off", "--confirm"])
    assert rc == 0
    p.execute_set_auto_reply.assert_called_once()
    op = p.execute_set_auto_reply.call_args.args[0]
    assert op.action == "mail.settings.auto-reply"
    assert op.args["auto_reply"] == {"status": "disabled"}


def test_ooo_on_always_enabled_message_only(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_ooo.main([
            "--config", str(cfg),
            "on", "--message", "Out today.",
            "--audience", "all",
            "--confirm",
        ])
    assert rc == 0
    p.execute_set_auto_reply.assert_called_once()
    op = p.execute_set_auto_reply.call_args.args[0]
    body = op.args["auto_reply"]
    assert body["status"] == "alwaysEnabled"
    assert body["externalAudience"] == "all"
    assert body["internalReplyMessage"] == "Out today."
    # External defaults to internal when not provided.
    assert body["externalReplyMessage"] == "Out today."
    # No scheduled fields.
    assert "scheduledStartDateTime" not in body
    assert "scheduledEndDateTime" not in body


def test_ooo_on_scheduled_with_start_end(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_ooo.main([
            "--config", str(cfg),
            "on", "--message", "Away.",
            "--audience", "contactsOnly",
            "--start", "2026-05-01T00:00:00Z",
            "--end", "2026-05-10T00:00:00Z",
            "--external-message", "Reply external",
            "--confirm",
        ])
    assert rc == 0
    op = p.execute_set_auto_reply.call_args.args[0]
    body = op.args["auto_reply"]
    assert body["status"] == "scheduled"
    assert body["scheduledStartDateTime"] == {
        "dateTime": "2026-05-01T00:00:00Z", "timeZone": "UTC",
    }
    assert body["scheduledEndDateTime"] == {
        "dateTime": "2026-05-10T00:00:00Z", "timeZone": "UTC",
    }
    assert body["externalAudience"] == "contactsOnly"
    assert body["internalReplyMessage"] == "Away."
    assert body["externalReplyMessage"] == "Reply external"
    # No --force forwarded by default.
    assert op.args.get("force") is not True


def test_ooo_on_force_forwards_force_flag(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_ooo.main([
            "--config", str(cfg),
            "on", "--message", "Long OOO",
            "--start", "2026-05-01T00:00:00Z",
            "--end", "2026-08-01T00:00:00Z",
            "--force", "--confirm",
        ])
    assert rc == 0
    op = p.execute_set_auto_reply.call_args.args[0]
    assert op.args.get("force") is True


def test_ooo_on_too_long_returns_1_with_stderr(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    err = OOOTooLong("OOO duration is 92 days (>60); set args['force'] = True to bypass")
    with _Patched(executor_side_effect=err):
        rc = cli_ooo.main([
            "--config", str(cfg),
            "on", "--message", "Long",
            "--start", "2026-05-01T00:00:00Z",
            "--end", "2026-08-01T00:00:00Z",
            "--confirm",
        ])
    assert rc == 1
    msg = capsys.readouterr().err
    assert "60-day safety gate" in msg
    assert "--force" in msg


def test_ooo_off_without_confirm_dry_runs(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_ooo.main(["--config", str(cfg), "off"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "dry-run" in err
    p.execute_set_auto_reply.assert_not_called()
