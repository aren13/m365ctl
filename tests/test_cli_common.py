"""Tests for cli/_common.py helpers.

Covers the "me" → drive-id expansion in allow_drives — a config-UX bug
where the literal string "me" in cfg.scope.allow_drives never matched
any real drive_id (safety.py does string-in comparison).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from m365ctl.cli._common import _expand_me_in_allow_drives


def _cfg(allow: list[str]) -> object:
    """Build a minimal config-shaped stub. We only touch .scope.allow_drives."""
    from m365ctl.common.config import (
        CatalogConfig, Config, LoggingConfig, ScopeConfig,
    )
    return Config(
        tenant_id="t", client_id="c",
        cert_path=Path("/tmp/k"), cert_public=Path("/tmp/c"),
        default_auth="delegated",
        scope=ScopeConfig(
            allow_drives=list(allow), allow_users=["*"],
            deny_paths=[], unsafe_requires_flag=True,
        ),
        catalog=CatalogConfig(path=Path("/tmp/c.duckdb")),
        logging=LoggingConfig(ops_dir=Path("/tmp/logs")),
    )


def test_expand_replaces_me_with_delegated_drive_id() -> None:
    cfg = _cfg(["me", "drv-other"])
    graph = MagicMock()
    graph.get.return_value = {"id": "drv-mine", "name": "OneDrive"}
    _expand_me_in_allow_drives(cfg, graph, scope="me")
    assert cfg.scope.allow_drives == ["drv-mine", "drv-other"]
    graph.get.assert_called_once_with("/me/drive")


def test_expand_is_noop_when_me_not_in_list() -> None:
    cfg = _cfg(["drv-a", "drv-b"])
    graph = MagicMock()
    _expand_me_in_allow_drives(cfg, graph, scope="me")
    assert cfg.scope.allow_drives == ["drv-a", "drv-b"]
    graph.get.assert_not_called()


def test_expand_is_noop_for_app_only_scope() -> None:
    """App-only has no /me/drive — leave 'me' as sentinel."""
    cfg = _cfg(["me"])
    graph = MagicMock()
    _expand_me_in_allow_drives(cfg, graph, scope="tenant")
    assert cfg.scope.allow_drives == ["me"]
    graph.get.assert_not_called()


def test_expand_leaves_me_in_place_when_graph_errors() -> None:
    """Delegated token but /me/drive failed — safe fallback: keep sentinel."""
    from m365ctl.common.graph import GraphError
    cfg = _cfg(["me"])
    graph = MagicMock()
    graph.get.side_effect = GraphError("HTTP500: oops")
    _expand_me_in_allow_drives(cfg, graph, scope="me")
    assert cfg.scope.allow_drives == ["me"]  # unchanged; item match still fails


def test_expand_idempotent_after_first_call() -> None:
    cfg = _cfg(["me"])
    graph = MagicMock()
    graph.get.return_value = {"id": "drv-mine"}
    _expand_me_in_allow_drives(cfg, graph, scope="me")
    _expand_me_in_allow_drives(cfg, graph, scope="me")
    assert cfg.scope.allow_drives == ["drv-mine"]
    # Second call was a no-op because 'me' is gone.
    assert graph.get.call_count == 1
