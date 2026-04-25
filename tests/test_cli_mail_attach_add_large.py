"""CLI integration tests for `mail attach add` large (>=3MB) path.

Mocks at the CLI module boundary (credentials, GraphClient, executor) so
the test exercises argument parsing, dispatch, and op-construction logic
without needing real auth or HTTP.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from m365ctl.mail.cli import attach as cli_attach


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


def _make_large_file(tmp_path: Path, *, size: int = 4 * 1024 * 1024) -> Path:
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * size)
    return p


def _ok_result(after: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status = "ok"
    r.error = None
    r.after = after or {"id": "att-large-1"}
    return r


def test_add_large_with_confirm_calls_execute_large(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    big = _make_large_file(tmp_path, size=5 * 1024 * 1024)

    with (
        patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls,
        patch("m365ctl.mail.cli._common.AppOnlyCredential"),
        patch("m365ctl.mail.cli.attach.GraphClient") as graph_cls,
        patch("m365ctl.mail.cli.attach.execute_add_attachment_large") as exec_large,
        patch("m365ctl.mail.cli.attach.execute_add_attachment_small") as exec_small,
    ):
        cred_cls.return_value.get_token.return_value = "tok"
        graph_cls.return_value = MagicMock()
        exec_large.return_value = _ok_result({"id": "att-9", "name": "big.bin", "size": 5 * 1024 * 1024})

        rc = cli_attach.main([
            "--config", str(cfg),
            "add", "msg-1", "--file", str(big), "--confirm",
        ])
        assert rc == 0
        exec_small.assert_not_called()
        exec_large.assert_called_once()

        op = exec_large.call_args.args[0]
        # Streaming path: file_path is recorded, content_bytes_b64 is NOT.
        assert op.args["file_path"] == str(big.resolve())
        assert op.args["size"] == 5 * 1024 * 1024
        assert op.args["name"] == "big.bin"
        assert "content_bytes_b64" not in op.args
        assert op.args["auth_mode"] == "delegated"
        assert op.action == "mail.attach.add.large"
        assert op.item_id == "msg-1"


def test_add_large_dry_run_without_confirm_returns_zero(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    big = _make_large_file(tmp_path, size=4 * 1024 * 1024)

    with (
        patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls,
        patch("m365ctl.mail.cli._common.AppOnlyCredential"),
        patch("m365ctl.mail.cli.attach.GraphClient"),
        patch("m365ctl.mail.cli.attach.execute_add_attachment_large") as exec_large,
        patch("m365ctl.mail.cli.attach.execute_add_attachment_small") as exec_small,
    ):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_attach.main([
            "--config", str(cfg),
            "add", "msg-1", "--file", str(big),
        ])
        assert rc == 0
        exec_large.assert_not_called()
        exec_small.assert_not_called()
        err = capsys.readouterr().err
        assert "dry-run" in err
        assert str(4 * 1024 * 1024) in err


def test_add_large_missing_file_returns_two(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    missing = tmp_path / "ghost.bin"

    with (
        patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls,
        patch("m365ctl.mail.cli._common.AppOnlyCredential"),
        patch("m365ctl.mail.cli.attach.GraphClient"),
        patch("m365ctl.mail.cli.attach.execute_add_attachment_large") as exec_large,
    ):
        cred_cls.return_value.get_token.return_value = "tok"
        rc = cli_attach.main([
            "--config", str(cfg),
            "add", "msg-1", "--file", str(missing), "--confirm",
        ])
        assert rc == 2
        exec_large.assert_not_called()


def test_add_large_executor_error_returns_one(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    big = _make_large_file(tmp_path, size=4 * 1024 * 1024)

    with (
        patch("m365ctl.mail.cli._common.DelegatedCredential") as cred_cls,
        patch("m365ctl.mail.cli._common.AppOnlyCredential"),
        patch("m365ctl.mail.cli.attach.GraphClient"),
        patch("m365ctl.mail.cli.attach.execute_add_attachment_large") as exec_large,
    ):
        cred_cls.return_value.get_token.return_value = "tok"
        err_result = MagicMock()
        err_result.status = "error"
        err_result.error = "uploadSession fragment failed"
        err_result.after = None
        exec_large.return_value = err_result

        rc = cli_attach.main([
            "--config", str(cfg),
            "add", "msg-1", "--file", str(big), "--confirm",
        ])
        assert rc == 1
