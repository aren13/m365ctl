from unittest.mock import MagicMock

from m365ctl.mail.models import AutomaticRepliesSetting, MailboxSettings
from m365ctl.mail.settings import get_auto_reply, get_settings


_SETTINGS_RAW = {
    "timeZone": "Europe/Istanbul",
    "language": {"locale": "en-US", "displayName": "English (United States)"},
    "workingHours": {
        "daysOfWeek": ["monday"],
        "startTime": "09:00:00.0000000",
        "endTime": "17:00:00.0000000",
        "timeZone": {"name": "Europe/Istanbul"},
    },
    "automaticRepliesSetting": {
        "status": "disabled",
        "externalAudience": "none",
        "scheduledStartDateTime": {"dateTime": "2026-04-24T00:00:00.0000000", "timeZone": "UTC"},
        "scheduledEndDateTime":   {"dateTime": "2026-04-24T23:59:59.0000000", "timeZone": "UTC"},
        "internalReplyMessage": "",
        "externalReplyMessage": "",
    },
    "delegateMeetingMessageDeliveryOptions": "sendToDelegateOnly",
    "dateFormat": "yyyy-MM-dd",
    "timeFormat": "HH:mm",
}


def test_get_settings():
    graph = MagicMock()
    graph.get.return_value = _SETTINGS_RAW
    s = get_settings(graph, mailbox_spec="me", auth_mode="delegated")
    assert isinstance(s, MailboxSettings)
    assert s.timezone == "Europe/Istanbul"
    assert graph.get.call_args.args[0] == "/me/mailboxSettings"


def test_get_auto_reply():
    graph = MagicMock()
    graph.get.return_value = _SETTINGS_RAW["automaticRepliesSetting"]
    ar = get_auto_reply(graph, mailbox_spec="me", auth_mode="delegated")
    assert isinstance(ar, AutomaticRepliesSetting)
    assert ar.status == "disabled"
    assert graph.get.call_args.args[0] == "/me/mailboxSettings/automaticRepliesSetting"


def test_get_settings_app_only_routing():
    graph = MagicMock()
    graph.get.return_value = _SETTINGS_RAW
    get_settings(graph, mailbox_spec="upn:alice@example.com", auth_mode="app-only")
    assert graph.get.call_args.args[0] == "/users/alice@example.com/mailboxSettings"
