from unittest.mock import MagicMock

from m365ctl.mail.categories import list_master_categories
from m365ctl.mail.models import Category


def test_list_master_categories():
    graph = MagicMock()
    graph.get.return_value = {
        "value": [
            {"id": "c1", "displayName": "Followup", "color": "preset0"},
            {"id": "c2", "displayName": "Waiting", "color": "preset4"},
        ]
    }
    out = list_master_categories(graph, mailbox_spec="me", auth_mode="delegated")
    assert out == [
        Category(id="c1", display_name="Followup", color="preset0"),
        Category(id="c2", display_name="Waiting", color="preset4"),
    ]
    assert graph.get.call_args.args[0] == "/me/outlook/masterCategories"


def test_list_master_categories_app_only_routing():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    list_master_categories(graph, mailbox_spec="upn:bob@example.com", auth_mode="app-only")
    assert graph.get.call_args.args[0] == "/users/bob@example.com/outlook/masterCategories"


def test_list_master_categories_empty():
    graph = MagicMock()
    graph.get.return_value = {"value": []}
    assert list_master_categories(graph, mailbox_spec="me", auth_mode="delegated") == []
