"""Read-only inbox rules list + single-fetch.

Phase 8 adds YAML translator helpers ``rule_to_yaml`` /
``rule_from_yaml`` for round-trippable export/import.
"""
from __future__ import annotations

from typing import Any, Callable

from m365ctl.common.graph import GraphClient
from m365ctl.mail.endpoints import AuthMode, user_base
from m365ctl.mail.models import Rule


def list_rules(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
) -> list[Rule]:
    """List inbox rules sorted by Graph's ``sequence`` (evaluation order)."""
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    resp = graph.get(f"{ub}/mailFolders/inbox/messageRules")
    rules = [Rule.from_graph_json(raw) for raw in resp.get("value", [])]
    rules.sort(key=lambda r: r.sequence)
    return rules


def get_rule(
    graph: GraphClient,
    *,
    mailbox_spec: str,
    auth_mode: AuthMode,
    rule_id: str,
) -> Rule:
    ub = user_base(mailbox_spec, auth_mode=auth_mode)
    raw = graph.get(f"{ub}/mailFolders/inbox/messageRules/{rule_id}")
    return Rule.from_graph_json(raw)


class RuleYamlError(ValueError):
    """Raised when a YAML rule document is malformed."""


# Snake_case YAML keys ↔ camelCase Graph keys for conditions/exceptions.
_CONDITION_BIDI: dict[str, str] = {
    "from_addresses": "fromAddresses",
    "subject_contains": "subjectContains",
    "body_contains": "bodyContains",
    "body_or_subject_contains": "bodyOrSubjectContains",
    "sender_contains": "senderContains",
    "recipient_contains": "recipientContains",
    "header_contains": "headerContains",
    "importance": "importance",
    "has_attachments": "hasAttachments",
    "sent_to_me": "sentToMe",
    "sent_only_to_me": "sentOnlyToMe",
    "sent_cc_me": "sentCcMe",
    "sent_to_or_cc_me": "sentToOrCcMe",
    "not_sent_to_me": "notSentToMe",
}

_ACTION_BIDI: dict[str, str] = {
    "move_to_folder": "moveToFolder",
    "copy_to_folder": "copyToFolder",
    "delete": "delete",
    "mark_as_read": "markAsRead",
    "mark_importance": "markImportance",
    "assign_categories": "assignCategories",
    "forward_to": "forwardTo",
    "redirect_to": "redirectTo",
    "stop_processing_rules": "stopProcessingRules",
}

_CONDITION_BIDI_REVERSE = {v: k for k, v in _CONDITION_BIDI.items()}
_ACTION_BIDI_REVERSE = {v: k for k, v in _ACTION_BIDI.items()}

_EMAIL_LIST_FIELDS_CONDITION = {"fromAddresses"}
_EMAIL_LIST_FIELDS_ACTION = {"forwardTo", "redirectTo"}
_FOLDER_ID_FIELDS_ACTION = {"moveToFolder", "copyToFolder"}


def rule_to_yaml(rule: Rule, *, folder_id_to_path: Callable[[str], str]) -> dict[str, Any]:
    """Render a Rule into a YAML-friendly dict (snake_case, paths, unwrapped emails)."""
    return {
        "display_name": rule.display_name,
        "sequence": rule.sequence,
        "enabled": rule.is_enabled,
        "conditions": _block_to_yaml(rule.conditions, kind="condition", folder_id_to_path=folder_id_to_path),
        "actions": _block_to_yaml(rule.actions, kind="action", folder_id_to_path=folder_id_to_path),
        "exceptions": _block_to_yaml(rule.exceptions, kind="condition", folder_id_to_path=folder_id_to_path),
    }


def rule_from_yaml(doc: dict[str, Any], *, folder_path_to_id: Callable[[str], str]) -> dict[str, Any]:
    """Translate a YAML rule doc into a Graph messageRule body dict."""
    if not isinstance(doc, dict):
        raise RuleYamlError(f"top-level must be a mapping, got {type(doc).__name__}")
    known_top = {"display_name", "sequence", "enabled", "conditions", "actions", "exceptions"}
    unknown = set(doc.keys()) - known_top
    if unknown:
        raise RuleYamlError(f"unknown key(s) at top level: {sorted(unknown)}")
    if "display_name" not in doc:
        raise RuleYamlError("missing required field 'display_name'")
    body: dict[str, Any] = {
        "displayName": doc["display_name"],
        "sequence": doc.get("sequence", 100),
        "isEnabled": bool(doc.get("enabled", True)),
        "conditions": _block_from_yaml(doc.get("conditions") or {}, kind="condition", folder_path_to_id=folder_path_to_id),
        "actions": _block_from_yaml(doc.get("actions") or {}, kind="action", folder_path_to_id=folder_path_to_id),
        "exceptions": _block_from_yaml(doc.get("exceptions") or {}, kind="condition", folder_path_to_id=folder_path_to_id),
    }
    return body


def _block_to_yaml(
    block: dict[str, Any],
    *,
    kind: str,
    folder_id_to_path: Callable[[str], str],
) -> dict[str, Any]:
    if not block:
        return {}
    reverse = _CONDITION_BIDI_REVERSE if kind == "condition" else _ACTION_BIDI_REVERSE
    out: dict[str, Any] = {}
    for graph_key, val in block.items():
        if graph_key not in reverse:
            # Unknown Graph key — keep verbatim under a debug namespace so a
            # round trip doesn't silently drop server-only fields. Surfaces
            # any unmapped Graph fields the user can decide to route through.
            out[f"_unknown_{graph_key}"] = val
            continue
        yaml_key = reverse[graph_key]
        if graph_key in _EMAIL_LIST_FIELDS_CONDITION or graph_key in _EMAIL_LIST_FIELDS_ACTION:
            out[yaml_key] = [_unwrap_email(x) for x in val or []]
        elif graph_key in _FOLDER_ID_FIELDS_ACTION:
            out[yaml_key] = folder_id_to_path(val) if val else val
        else:
            out[yaml_key] = val
    return out


def _block_from_yaml(
    block: dict[str, Any],
    *,
    kind: str,
    folder_path_to_id: Callable[[str], str],
) -> dict[str, Any]:
    if not block:
        return {}
    forward = _CONDITION_BIDI if kind == "condition" else _ACTION_BIDI
    out: dict[str, Any] = {}
    for yaml_key, val in block.items():
        if yaml_key.startswith("_unknown_"):
            # Verbatim pass-through for unmapped fields seen at export time.
            out[yaml_key[len("_unknown_"):]] = val
            continue
        if yaml_key not in forward:
            raise RuleYamlError(f"unknown {kind} key: {yaml_key!r}")
        graph_key = forward[yaml_key]
        if graph_key in _EMAIL_LIST_FIELDS_CONDITION or graph_key in _EMAIL_LIST_FIELDS_ACTION:
            out[graph_key] = [_wrap_email(x) for x in val or []]
        elif graph_key in _FOLDER_ID_FIELDS_ACTION:
            out[graph_key] = folder_path_to_id(val) if val else val
        else:
            out[graph_key] = val
    return out


def _unwrap_email(item: dict[str, Any]) -> dict[str, Any]:
    inner = item.get("emailAddress") or {}
    out: dict[str, Any] = {}
    if "name" in inner and inner["name"]:
        out["name"] = inner["name"]
    if "address" in inner:
        out["address"] = inner["address"]
    return out


def _wrap_email(item: dict[str, Any]) -> dict[str, Any]:
    inner: dict[str, Any] = {}
    if "name" in item and item["name"]:
        inner["name"] = item["name"]
    if "address" in item:
        inner["address"] = item["address"]
    return {"emailAddress": inner}
