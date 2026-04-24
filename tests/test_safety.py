from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from fazla_od.config import Config, ScopeConfig
from fazla_od.safety import (
    ScopeViolation,
    assert_scope_allowed,
    filter_by_scope,
)


@dataclass(frozen=True)
class _Item:
    drive_id: str
    item_id: str
    full_path: str
    name: str = ""


def _cfg(
    *,
    allow: list[str] = None,
    deny: list[str] = None,
    unsafe_requires_flag: bool = True,
    tmp_path: Path = None,
) -> Config:
    scope = ScopeConfig(
        allow_drives=allow or ["d1"],
        allow_users=["*"],
        deny_paths=deny or [],
        unsafe_requires_flag=unsafe_requires_flag,
    )
    # Only the .scope field matters here; stub the rest.
    from fazla_od.config import CatalogConfig, LoggingConfig
    return Config(
        tenant_id="t", client_id="c",
        cert_path=(tmp_path or Path("/tmp")) / "k",
        cert_public=(tmp_path or Path("/tmp")) / "c",
        default_auth="app-only",
        scope=scope,
        catalog=CatalogConfig(path=(tmp_path or Path("/tmp")) / "x.duckdb"),
        logging=LoggingConfig(ops_dir=(tmp_path or Path("/tmp")) / "logs"),
    )


def test_allow_drives_permits_listed_drive(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="d1", item_id="i", full_path="/foo")
    assert_scope_allowed(item, cfg, unsafe_scope=False)  # no raise


def test_allow_drives_blocks_unlisted_drive(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with pytest.raises(ScopeViolation, match="drive"):
        assert_scope_allowed(item, cfg, unsafe_scope=False)


def test_deny_paths_block_matching_item(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], deny=["/Confidential/**"], tmp_path=tmp_path)
    item = _Item(drive_id="d1", item_id="i", full_path="/Confidential/secret.docx")
    with pytest.raises(ScopeViolation, match="deny"):
        assert_scope_allowed(item, cfg, unsafe_scope=False)


def test_filter_by_scope_drops_denied_items(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], deny=["/HR/**"], tmp_path=tmp_path)
    items = [
        _Item(drive_id="d1", item_id="a", full_path="/Public/report.pdf"),
        _Item(drive_id="d1", item_id="b", full_path="/HR/salaries.xlsx"),
        _Item(drive_id="d1", item_id="c", full_path="/HR"),  # exact match to parent
    ]
    kept = list(filter_by_scope(items, cfg, unsafe_scope=False))
    assert [i.item_id for i in kept] == ["a"]


def test_filter_by_scope_drops_items_outside_allow_drives(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    items = [
        _Item(drive_id="d1",    item_id="a", full_path="/p"),
        _Item(drive_id="OTHER", item_id="b", full_path="/p"),
    ]
    kept = list(filter_by_scope(items, cfg, unsafe_scope=False))
    assert [i.item_id for i in kept] == ["a"]


def test_unsafe_scope_bypasses_allow_list_with_tty_yes(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("fazla_od.safety._confirm_via_tty", return_value=True):
        assert_scope_allowed(item, cfg, unsafe_scope=True)  # no raise


def test_unsafe_scope_without_tty_yes_still_raises(tmp_path: Path) -> None:
    cfg = _cfg(allow=["d1"], tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("fazla_od.safety._confirm_via_tty", return_value=False):
        with pytest.raises(ScopeViolation, match="declined"):
            assert_scope_allowed(item, cfg, unsafe_scope=True)


def test_unsafe_scope_flag_required_per_config(tmp_path: Path) -> None:
    """If unsafe_requires_flag is True (default), passing unsafe_scope=False
    against an out-of-scope item always raises — no TTY prompt offered."""
    cfg = _cfg(allow=["d1"], unsafe_requires_flag=True, tmp_path=tmp_path)
    item = _Item(drive_id="OTHER", item_id="i", full_path="/foo")
    with patch("fazla_od.safety._confirm_via_tty") as m:
        with pytest.raises(ScopeViolation):
            assert_scope_allowed(item, cfg, unsafe_scope=False)
        m.assert_not_called()  # never prompted — flag required upfront
