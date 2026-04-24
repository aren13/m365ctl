from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.common.audit import AuditLogger, log_mutation_end, log_mutation_start
from m365ctl.onedrive.cli.undo import run_undo


def _stub_cfg(tmp_path: Path):
    from m365ctl.common.config import CatalogConfig, Config, LoggingConfig, ScopeConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=tmp_path / "k", cert_public=tmp_path / "c",
        default_auth="app-only",
        scope=ScopeConfig(allow_drives=["d"], allow_users=["*"],
                          deny_paths=[], unsafe_requires_flag=True),
        catalog=CatalogConfig(path=tmp_path / "catalog.duckdb"),
        logging=LoggingConfig(ops_dir=tmp_path / "logs/ops"),
    )


def _seed_rename_op(logger: AuditLogger) -> None:
    log_mutation_start(logger, op_id="R1", cmd="od-rename",
                       args={"new_name": "new.txt"},
                       drive_id="d", item_id="i",
                       before={"parent_path": "/", "name": "old.txt"})
    log_mutation_end(logger, op_id="R1",
                     after={"parent_path": "/", "name": "new.txt"},
                     result="ok")


def test_dry_run_prints_reverse_op_without_executing(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.undo.load_config", return_value=cfg)
    _seed_rename_op(AuditLogger(ops_dir=cfg.logging.ops_dir))

    # Assert no execute_* is called under dry-run.
    ex_rename = mocker.patch("m365ctl.onedrive.cli.undo.execute_rename",
                             side_effect=AssertionError("must not run"))

    rc = run_undo(config_path=tmp_path / "config.toml", op_id="R1",
                  confirm=False, unsafe_scope=False)
    assert rc == 0
    ex_rename.assert_not_called()
    out = capsys.readouterr().out
    assert "Reverse op: rename" in out
    assert "DRY-RUN" in out


def test_confirm_dispatches_to_execute_rename(tmp_path, mocker):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.undo.load_config", return_value=cfg)
    _seed_rename_op(AuditLogger(ops_dir=cfg.logging.ops_dir))

    mocker.patch("m365ctl.onedrive.cli.undo.build_graph_client", return_value=MagicMock())
    mocker.patch("m365ctl.onedrive.cli.undo._lookup_item",
                 return_value={"parent_path": "/", "name": "new.txt"})
    fake_result = MagicMock()
    fake_result.status = "ok"
    fake_result.op_id = "rev-uid"
    ex_rename = mocker.patch("m365ctl.onedrive.cli.undo.execute_rename",
                             return_value=fake_result)

    rc = run_undo(config_path=tmp_path / "config.toml", op_id="R1",
                  confirm=True, unsafe_scope=False)
    assert rc == 0
    ex_rename.assert_called_once()


def test_irreversible_op_exits_2(tmp_path, mocker, capsys):
    cfg = _stub_cfg(tmp_path)
    mocker.patch("m365ctl.onedrive.cli.undo.load_config", return_value=cfg)
    # Seed a purge (irreversible)
    logger = AuditLogger(ops_dir=cfg.logging.ops_dir)
    log_mutation_start(logger, op_id="P1", cmd="od-clean(recycle-bin)",
                       args={}, drive_id="d", item_id="i",
                       before={"parent_path": "(recycle bin)", "name": "x"})
    log_mutation_end(logger, op_id="P1",
                     after={"parent_path": "(permanently deleted)",
                            "name": "x", "irreversible": True},
                     result="ok")

    rc = run_undo(config_path=tmp_path / "config.toml", op_id="P1",
                  confirm=True, unsafe_scope=False)
    assert rc == 2
    err = capsys.readouterr().err
    assert "irreversible" in err.lower()
    assert "permanently" in err.lower()


def test_normalize_legacy_bare_action_in_dispatcher():
    """A bare legacy action like 'move' must resolve via the registered `od.move` inverse."""
    from m365ctl.common.undo import Dispatcher, normalize_legacy_action
    from m365ctl.onedrive.mutate.undo import register_od_inverses

    d = Dispatcher()
    register_od_inverses(d)

    assert normalize_legacy_action("move") == "od.move"
    assert d.is_registered("move")     # via normalization
    assert d.is_registered("od.move")  # direct
