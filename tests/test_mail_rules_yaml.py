from __future__ import annotations

import pytest

from m365ctl.mail.rules import (
    RuleYamlError, rule_from_yaml, rule_to_yaml,
)
from m365ctl.mail.models import Rule


def _rule(*, conditions=None, actions=None, exceptions=None) -> Rule:
    return Rule(
        id="rule-1",
        display_name="r1",
        sequence=10,
        is_enabled=True,
        has_error=False,
        is_read_only=False,
        conditions=conditions or {},
        actions=actions or {},
        exceptions=exceptions or {},
    )


def _id_to_path(folder_id: str) -> str:
    return {"fld-arch": "Archive/Newsletters", "fld-todo": "Inbox/ToDo"}[folder_id]


def _path_to_id(path: str) -> str:
    return {"Archive/Newsletters": "fld-arch", "Inbox/ToDo": "fld-todo"}[path]


def test_to_yaml_basic_shape():
    r = _rule(
        conditions={"subjectContains": ["weekly digest"]},
        actions={"markAsRead": True, "stopProcessingRules": True},
    )
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc == {
        "display_name": "r1",
        "sequence": 10,
        "enabled": True,
        "conditions": {"subject_contains": ["weekly digest"]},
        "actions": {"mark_as_read": True, "stop_processing_rules": True},
        "exceptions": {},
    }


def test_to_yaml_translates_folder_id_to_path():
    r = _rule(actions={"moveToFolder": "fld-arch", "markAsRead": True})
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc["actions"] == {
        "move_to_folder": "Archive/Newsletters",
        "mark_as_read": True,
    }


def test_to_yaml_unwraps_email_addresses():
    r = _rule(
        conditions={
            "fromAddresses": [
                {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
                {"emailAddress": {"address": "bob@example.com"}},
            ]
        },
    )
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc["conditions"]["from_addresses"] == [
        {"name": "Alice", "address": "alice@example.com"},
        {"address": "bob@example.com"},
    ]


def test_from_yaml_round_trip_basic():
    doc = {
        "display_name": "r1",
        "sequence": 10,
        "enabled": True,
        "conditions": {"subject_contains": ["weekly digest"]},
        "actions": {"mark_as_read": True, "stop_processing_rules": True},
        "exceptions": {},
    }
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body == {
        "displayName": "r1",
        "sequence": 10,
        "isEnabled": True,
        "conditions": {"subjectContains": ["weekly digest"]},
        "actions": {"markAsRead": True, "stopProcessingRules": True},
        "exceptions": {},
    }


def test_from_yaml_translates_folder_path_to_id():
    doc = {
        "display_name": "r",
        "sequence": 1,
        "enabled": True,
        "actions": {"move_to_folder": "Archive/Newsletters"},
    }
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body["actions"]["moveToFolder"] == "fld-arch"


def test_from_yaml_wraps_email_addresses():
    doc = {
        "display_name": "r",
        "sequence": 1,
        "enabled": True,
        "conditions": {
            "from_addresses": [
                {"name": "Alice", "address": "alice@example.com"},
                {"address": "bob@example.com"},
            ],
        },
        "actions": {"delete": True},
    }
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body["conditions"]["fromAddresses"] == [
        {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        {"emailAddress": {"address": "bob@example.com"}},
    ]


def test_from_yaml_rejects_unknown_top_level_key():
    doc = {
        "display_name": "r", "sequence": 1, "enabled": True,
        "actions": {"delete": True},
        "secret_sauce": "yes",
    }
    with pytest.raises(RuleYamlError, match="unknown key.*secret_sauce"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)


def test_from_yaml_rejects_unknown_condition():
    doc = {
        "display_name": "r", "sequence": 1, "enabled": True,
        "conditions": {"vibes": "good"},
        "actions": {"delete": True},
    }
    with pytest.raises(RuleYamlError, match="unknown.*condition.*vibes"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)


def test_from_yaml_rejects_unknown_action():
    doc = {
        "display_name": "r", "sequence": 1, "enabled": True,
        "actions": {"teleport": True},
    }
    with pytest.raises(RuleYamlError, match="unknown.*action.*teleport"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)


def test_round_trip_full_coverage():
    """Take a Graph-shape Rule, to_yaml, from_yaml, expect Graph body equal
    to the original dict (modulo id-only fields)."""
    original_conditions = {
        "fromAddresses": [
            {"emailAddress": {"name": "X", "address": "x@example.com"}}
        ],
        "subjectContains": ["foo", "bar"],
        "senderContains": ["@example.com"],
        "hasAttachments": True,
        "importance": "high",
    }
    original_actions = {
        "moveToFolder": "fld-arch",
        "markAsRead": True,
        "assignCategories": ["Work"],
        "stopProcessingRules": True,
    }
    original_exceptions = {
        "fromAddresses": [{"emailAddress": {"address": "boss@example.com"}}]
    }
    r = _rule(
        conditions=original_conditions,
        actions=original_actions,
        exceptions=original_exceptions,
    )
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    body = rule_from_yaml(doc, folder_path_to_id=_path_to_id)
    assert body["conditions"] == original_conditions
    assert body["actions"] == original_actions
    assert body["exceptions"] == original_exceptions
    assert body["displayName"] == "r1"
    assert body["isEnabled"] is True
    assert body["sequence"] == 10


def test_to_yaml_omits_empty_sections():
    """If a Rule has no conditions/actions/exceptions, the YAML doc still
    has the keys (as empty dicts) so the round-trip is symmetric."""
    r = _rule()
    doc = rule_to_yaml(r, folder_id_to_path=_id_to_path)
    assert doc["conditions"] == {}
    assert doc["actions"] == {}
    assert doc["exceptions"] == {}


def test_from_yaml_missing_required_field_rejected():
    doc = {"sequence": 1, "enabled": True, "actions": {"delete": True}}
    with pytest.raises(RuleYamlError, match="missing.*display_name"):
        rule_from_yaml(doc, folder_path_to_id=_path_to_id)
