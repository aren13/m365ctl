"""CLI tests for `mail signature {show, set}` (Phase 9 G3.3)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import signature as cli_signature


def _write_config(tmp_path: Path, *, with_sig_path: bool = True) -> Path:
    sig_line = ""
    if with_sig_path:
        sig_path = tmp_path / "sig.txt"
        sig_line = f'signature_path = "{sig_path}"\n'
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
{sig_line}
[logging]
ops_dir = "{tmp_path / 'logs'}"
""".lstrip()
    )
    return cfg


class _Patched:
    def __enter__(self):
        self._patches = [
            patch("m365ctl.mail.cli._common.DelegatedCredential"),
            patch("m365ctl.mail.cli._common.AppOnlyCredential"),
            patch("m365ctl.mail.cli.signature.execute_set_signature"),
        ]
        self.cred_cls = self._patches[0].__enter__()
        self.app_cred_cls = self._patches[1].__enter__()
        self.execute_set_signature = self._patches[2].__enter__()

        self.cred_cls.return_value.get_token.return_value = "tok"
        self.app_cred_cls.return_value.get_token.return_value = "tok"

        ok = MagicMock()
        ok.status = "ok"
        ok.error = None
        ok.after = {}
        self.execute_set_signature.return_value = ok
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


def test_signature_show_prints_content(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    sig_path = tmp_path / "sig.txt"
    sig_path.write_text("Best,\nA")
    with _Patched():
        rc = cli_signature.main(["--config", str(cfg), "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Best,\nA" in out
    assert "text" in out


def test_signature_show_unconfigured_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path, with_sig_path=False)
    with _Patched():
        rc = cli_signature.main(["--config", str(cfg), "show"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "signature_path" in err
    assert "config.toml" in err


def test_signature_set_from_file_calls_executor(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    src = tmp_path / "new_sig.html"
    src.write_text("<p>Best</p>")
    with _Patched() as p:
        rc = cli_signature.main([
            "--config", str(cfg),
            "set", "--from-file", str(src), "--confirm",
        ])
    assert rc == 0
    p.execute_set_signature.assert_called_once()
    op_arg = p.execute_set_signature.call_args.args[0]
    assert op_arg.action == "mail.settings.signature"
    assert op_arg.args["content"] == "<p>Best</p>"
    assert op_arg.args["signature_path"] == str(tmp_path / "sig.txt")


def test_signature_set_inline_content_calls_executor(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_signature.main([
            "--config", str(cfg),
            "set", "--content", "Hello sig", "--confirm",
        ])
    assert rc == 0
    op_arg = p.execute_set_signature.call_args.args[0]
    assert op_arg.args["content"] == "Hello sig"


def test_signature_set_without_confirm_dry_runs(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_signature.main([
            "--config", str(cfg),
            "set", "--content", "x",
        ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "dry-run" in err
    p.execute_set_signature.assert_not_called()


def test_signature_set_mutually_exclusive(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    src = tmp_path / "s.txt"
    src.write_text("x")
    with _Patched():
        # argparse exits with SystemExit(2) for mutual-exclusivity violation.
        try:
            cli_signature.main([
                "--config", str(cfg),
                "set", "--from-file", str(src), "--content", "y", "--confirm",
            ])
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert e.code == 2


def test_signature_set_records_prior_content_in_before(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    sig_path = tmp_path / "sig.txt"
    sig_path.write_text("OLD")
    with _Patched() as p:
        rc = cli_signature.main([
            "--config", str(cfg),
            "set", "--content", "NEW", "--confirm",
        ])
    assert rc == 0
    before = p.execute_set_signature.call_args.kwargs.get("before") or {}
    assert before.get("content") == "OLD"
    assert before.get("signature_path") == str(sig_path)
