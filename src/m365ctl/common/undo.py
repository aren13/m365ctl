"""Domain-agnostic undo dispatcher for m365ctl.

Every reversible mutation registers an inverse builder keyed on
``<domain>.<verb>`` (e.g. ``od.move``, ``mail.flag``). Irreversible verbs
register a sentinel with an operator-facing explanation.

Legacy bare actions from pre-refactor audit entries (e.g. ``move``,
``rename``) are normalized to their ``od.*`` equivalents on read so undo
still works on op-log lines written before Phase 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

InverseBuilder = Callable[[dict, dict], dict]
"""A function ``(before, after) -> inverse_op_spec`` where
``inverse_op_spec`` is a dict with at minimum
``{"action": "<domain>.<verb>", "args": {...}}`` suitable for
re-feeding into the executor."""


_LEGACY_OD_ACTIONS = frozenset({
    "move", "rename", "copy", "delete", "restore",
    "label-apply", "label-remove", "download",
    "version-delete", "share-revoke", "recycle-purge",
})


class UnknownAction(KeyError):
    """Raised when asked to invert an action with no registered builder."""


class IrreversibleOp(RuntimeError):
    """Raised when the registered entry for an action is a sentinel."""


@dataclass(frozen=True)
class _Irreversible:
    reason: str


def normalize_legacy_action(action: str) -> str:
    """Prefix bare legacy OneDrive actions with ``od.``; leave namespaced actions untouched."""
    if "." in action:
        return action
    if action in _LEGACY_OD_ACTIONS:
        return f"od.{action}"
    return action


class Dispatcher:
    """Registry mapping ``<domain>.<verb>`` to inverse builders."""

    def __init__(self) -> None:
        self._registry: dict[str, InverseBuilder | _Irreversible] = {}

    def register(self, action: str, builder: InverseBuilder) -> None:
        if action in self._registry:
            raise ValueError(f"action {action!r} already registered")
        self._registry[action] = builder

    def register_irreversible(self, action: str, reason: str) -> None:
        if action in self._registry:
            raise ValueError(f"action {action!r} already registered")
        self._registry[action] = _Irreversible(reason=reason)

    def build_inverse(self, action: str, *, before: dict, after: dict) -> dict:
        """Return an inverse op spec, or raise."""
        normalized = normalize_legacy_action(action)
        entry = self._registry.get(normalized)
        if entry is None:
            raise UnknownAction(
                f"no inverse builder registered for action {normalized!r}"
            )
        if isinstance(entry, _Irreversible):
            raise IrreversibleOp(entry.reason)
        return entry(before, after)

    def is_registered(self, action: str) -> bool:
        return normalize_legacy_action(action) in self._registry

    def actions(self) -> list[str]:
        return sorted(self._registry)
