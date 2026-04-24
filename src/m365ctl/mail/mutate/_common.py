"""Shared helpers for mail mutation executors.

The three exports are:

- ``MailResult`` — standard return type for every ``execute_*`` mail
  mutation. Shape mirrors ``onedrive.mutate.rename.RenameResult`` so CLI
  handlers can treat OneDrive + Mail results uniformly.
- ``assert_mail_target_allowed`` — hardens the CLI layer. Runs the two
  mailbox + folder gates that every mail mutation must pass BEFORE any
  Graph call.
- ``derive_mailbox_upn`` — canonicalize ``--mailbox`` spec to the
  ``mailbox_upn`` stored in audit records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from m365ctl.common.config import Config
from m365ctl.common.safety import (
    ScopeViolation,
    assert_mailbox_allowed,
    is_folder_denied,
)


@dataclass(frozen=True)
class MailResult:
    op_id: str
    status: str
    error: str | None = None
    after: dict[str, Any] | None = None


def derive_mailbox_upn(mailbox_spec: str) -> str:
    """Return the address-or-keyword stored as ``drive_id`` in audit records."""
    if mailbox_spec == "me":
        return "me"
    if mailbox_spec.startswith("upn:") or mailbox_spec.startswith("shared:"):
        return mailbox_spec.split(":", 1)[1]
    return mailbox_spec


def assert_mail_target_allowed(
    cfg: Config,
    *,
    mailbox_spec: str,
    auth_mode: str,
    unsafe_scope: bool,
    folder_path: str | None = None,
) -> None:
    """Combined mailbox + folder gate for mail mutations.

    Order matters: folder deny check runs first (absolute, never overridable),
    then mailbox scope. The CLI layer calls this before any Graph call.

    Raises ``ScopeViolation`` on any violation.
    """
    if folder_path is not None and is_folder_denied(folder_path, cfg):
        raise ScopeViolation(
            f"folder {folder_path!r} matches a deny pattern "
            f"(compliance or scope.deny_folders); mutation blocked"
        )
    assert_mailbox_allowed(
        mailbox_spec, cfg, auth_mode=auth_mode, unsafe_scope=unsafe_scope,
    )
