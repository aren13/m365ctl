from unittest.mock import MagicMock

from m365ctl.mail.models import Rule
from m365ctl.mail.rules import get_rule, list_rules


def test_list_rules_orders_by_sequence():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "r1", "displayName": "A", "sequence": 10, "isEnabled": True, "hasError": False, "isReadOnly": False, "conditions": {}, "actions": {}, "exceptions": {}},
            {"id": "r2", "displayName": "B", "sequence": 5,  "isEnabled": True, "hasError": False, "isReadOnly": False, "conditions": {}, "actions": {}, "exceptions": {}},
        ]
    }
    out = list_rules(graph, mailbox_spec="me", auth_mode="delegated")
    assert [r.id for r in out] == ["r2", "r1"]
    assert isinstance(out[0], Rule)
    assert graph.get.call_args.args[0] == "/me/mailFolders/inbox/messageRules"


def test_list_rules_app_only_routing():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    list_rules(graph, mailbox_spec="upn:bob@example.com", auth_mode="app-only")
    assert graph.get.call_args.args[0] == "/users/bob@example.com/mailFolders/inbox/messageRules"


def test_get_rule_single():
    graph = MagicMock()
    graph.get.return_value = {
        "id": "r1", "displayName": "X", "sequence": 1,
        "isEnabled": True, "hasError": False, "isReadOnly": False,
        "conditions": {}, "actions": {}, "exceptions": {},
    }
    r = get_rule(graph, mailbox_spec="me", auth_mode="delegated", rule_id="r1")
    assert r.id == "r1"
    assert graph.get.call_args.args[0] == "/me/mailFolders/inbox/messageRules/r1"
