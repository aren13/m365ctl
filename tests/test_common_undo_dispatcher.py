"""Unit tests for the domain-agnostic undo dispatcher."""
from __future__ import annotations

import pytest

from m365ctl.common.undo import (
    Dispatcher,
    IrreversibleOp,
    UnknownAction,
    normalize_legacy_action,
)


def test_register_and_invoke_reversible_action():
    d = Dispatcher()
    calls: list[dict] = []
    def rename_inverse(before: dict, after: dict) -> dict:
        calls.append({"before": before, "after": after})
        return {"action": "od.rename", "args": {"new_name": before["old_name"]}}
    d.register("od.rename", rename_inverse)
    inv = d.build_inverse("od.rename", before={"old_name": "A"}, after={"new_name": "B"})
    assert inv == {"action": "od.rename", "args": {"new_name": "A"}}
    assert calls == [{"before": {"old_name": "A"}, "after": {"new_name": "B"}}]


def test_irreversible_registration_raises_on_build():
    d = Dispatcher()
    d.register_irreversible("mail.send", "Sent mail cannot be recalled programmatically.")
    with pytest.raises(IrreversibleOp) as excinfo:
        d.build_inverse("mail.send", before={}, after={})
    assert "Sent mail cannot be recalled" in str(excinfo.value)


def test_unknown_action_raises():
    d = Dispatcher()
    with pytest.raises(UnknownAction):
        d.build_inverse("teams.chat.send", before={}, after={})


def test_double_register_raises():
    d = Dispatcher()
    d.register("od.move", lambda b, a: {})
    with pytest.raises(ValueError):
        d.register("od.move", lambda b, a: {})


def test_is_registered_and_actions_list():
    d = Dispatcher()
    assert d.actions() == []
    d.register("od.move", lambda b, a: {})
    d.register_irreversible("mail.send", "irreversible")
    assert d.is_registered("od.move")
    assert d.is_registered("mail.send")
    # Legacy normalization also reports registered.
    assert d.is_registered("move")
    assert sorted(d.actions()) == ["mail.send", "od.move"]


@pytest.mark.parametrize("legacy,normalized", [
    ("move", "od.move"),
    ("rename", "od.rename"),
    ("copy", "od.copy"),
    ("delete", "od.delete"),
    ("restore", "od.restore"),
    ("label-apply", "od.label-apply"),
    ("label-remove", "od.label-remove"),
    ("download", "od.download"),
    ("version-delete", "od.version-delete"),
    ("share-revoke", "od.share-revoke"),
    ("recycle-purge", "od.recycle-purge"),
    ("od.move", "od.move"),
    ("mail.move", "mail.move"),
    ("teams.send", "teams.send"),
])
def test_normalize_legacy_action(legacy: str, normalized: str):
    assert normalize_legacy_action(legacy) == normalized


def test_build_inverse_uses_normalized_action():
    """Calling build_inverse with a bare legacy action must dispatch to the od.* registrant."""
    d = Dispatcher()
    d.register("od.rename", lambda b, a: {"normalized": True})
    # Call with bare "rename" — should work via normalization.
    inv = d.build_inverse("rename", before={}, after={})
    assert inv == {"normalized": True}
