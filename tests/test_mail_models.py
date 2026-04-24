"""Unit tests for m365ctl.mail.models dataclasses + Graph-JSON parsers."""
from __future__ import annotations

from datetime import datetime, timezone

from m365ctl.mail.models import (
    Attachment,
    AutomaticRepliesSetting,
    Body,
    Category,
    EmailAddress,
    Flag,
    Folder,
    LocaleInfo,
    MailboxSettings,
    Message,
    Rule,
    WorkingHours,
)


# ---- EmailAddress ----------------------------------------------------------

def test_email_address_from_graph_json_full():
    raw = {"emailAddress": {"name": "Alice Example", "address": "alice@example.com"}}
    addr = EmailAddress.from_graph_json(raw)
    assert addr == EmailAddress(name="Alice Example", address="alice@example.com")


def test_email_address_from_graph_json_missing_name():
    raw = {"emailAddress": {"address": "bot@example.com"}}
    addr = EmailAddress.from_graph_json(raw)
    assert addr.name == ""
    assert addr.address == "bot@example.com"


def test_email_address_from_graph_json_accepts_flat_shape():
    raw = {"name": "Bob", "address": "bob@example.com"}
    addr = EmailAddress.from_graph_json(raw)
    assert addr == EmailAddress(name="Bob", address="bob@example.com")


def test_email_address_from_graph_json_none_input():
    addr = EmailAddress.from_graph_json(None)
    assert addr == EmailAddress(name="", address="")


# ---- Body ------------------------------------------------------------------

def test_body_from_graph_json_text():
    raw = {"contentType": "text", "content": "hello"}
    body = Body.from_graph_json(raw)
    assert body == Body(content_type="text", content="hello")


def test_body_from_graph_json_html_stripped():
    raw = {"contentType": "html", "content": "<p>hi</p>"}
    body = Body.from_graph_json(raw)
    assert body.content_type == "html"
    assert body.content == "<p>hi</p>"


# ---- Flag ------------------------------------------------------------------

def test_flag_from_graph_json_not_flagged():
    raw = {"flagStatus": "notFlagged"}
    flag = Flag.from_graph_json(raw)
    assert flag.status == "notFlagged"
    assert flag.start_at is None
    assert flag.due_at is None
    assert flag.completed_at is None


def test_flag_from_graph_json_flagged_with_dates():
    raw = {
        "flagStatus": "flagged",
        "startDateTime": {"dateTime": "2026-04-24T09:00:00.0000000", "timeZone": "UTC"},
        "dueDateTime": {"dateTime": "2026-04-30T17:00:00.0000000", "timeZone": "UTC"},
    }
    flag = Flag.from_graph_json(raw)
    assert flag.status == "flagged"
    assert flag.start_at == datetime(2026, 4, 24, 9, 0, tzinfo=timezone.utc)
    assert flag.due_at == datetime(2026, 4, 30, 17, 0, tzinfo=timezone.utc)


# ---- Folder ----------------------------------------------------------------

def test_folder_from_graph_json():
    raw = {
        "id": "AAMkAD...=",
        "displayName": "Inbox",
        "parentFolderId": "AAMkAD...parent=",
        "totalItemCount": 42,
        "unreadItemCount": 3,
        "childFolderCount": 5,
        "wellKnownName": "inbox",
    }
    f = Folder.from_graph_json(raw, mailbox_upn="me", path="/Inbox")
    assert f.id == "AAMkAD...="
    assert f.display_name == "Inbox"
    assert f.parent_id == "AAMkAD...parent="
    assert f.path == "/Inbox"
    assert f.total_items == 42
    assert f.unread_items == 3
    assert f.child_folder_count == 5
    assert f.well_known_name == "inbox"
    assert f.mailbox_upn == "me"


def test_folder_from_graph_json_defaults():
    raw = {"id": "1", "displayName": "X"}
    f = Folder.from_graph_json(raw, mailbox_upn="me", path="/X")
    assert f.parent_id is None
    assert f.total_items == 0
    assert f.unread_items == 0
    assert f.child_folder_count == 0
    assert f.well_known_name is None


# ---- Category --------------------------------------------------------------

def test_category_from_graph_json():
    raw = {"id": "cat-id", "displayName": "Follow up", "color": "preset0"}
    assert Category.from_graph_json(raw) == Category(
        id="cat-id", display_name="Follow up", color="preset0"
    )


# ---- Rule ------------------------------------------------------------------

def test_rule_from_graph_json():
    raw = {
        "id": "rule-id",
        "displayName": "Archive newsletters",
        "sequence": 10,
        "isEnabled": True,
        "hasError": False,
        "isReadOnly": False,
        "conditions": {"senderContains": ["@news.example.com"]},
        "actions": {"moveToFolder": "AAMkAD...=="},
        "exceptions": {},
    }
    r = Rule.from_graph_json(raw)
    assert r.id == "rule-id"
    assert r.display_name == "Archive newsletters"
    assert r.sequence == 10
    assert r.is_enabled is True
    assert r.has_error is False
    assert r.is_read_only is False
    assert r.conditions == {"senderContains": ["@news.example.com"]}
    assert r.actions == {"moveToFolder": "AAMkAD...=="}
    assert r.exceptions == {}


# ---- Attachment ------------------------------------------------------------

def test_attachment_from_graph_json_file():
    raw = {
        "id": "att-id",
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "report.pdf",
        "contentType": "application/pdf",
        "size": 12345,
        "isInline": False,
        "contentId": None,
    }
    a = Attachment.from_graph_json(raw, message_id="msg-1")
    assert a.id == "att-id"
    assert a.kind == "file"
    assert a.name == "report.pdf"
    assert a.content_type == "application/pdf"
    assert a.size == 12345
    assert a.is_inline is False
    assert a.content_id is None
    assert a.message_id == "msg-1"


def test_attachment_from_graph_json_item():
    raw = {
        "id": "att2",
        "@odata.type": "#microsoft.graph.itemAttachment",
        "name": "Meeting.ics",
        "contentType": "application/octet-stream",
        "size": 2048,
        "isInline": False,
    }
    a = Attachment.from_graph_json(raw, message_id="msg-2")
    assert a.kind == "item"


def test_attachment_from_graph_json_reference():
    raw = {
        "id": "att3",
        "@odata.type": "#microsoft.graph.referenceAttachment",
        "name": "link.url",
        "contentType": "application/octet-stream",
        "size": 0,
        "isInline": False,
    }
    a = Attachment.from_graph_json(raw, message_id="msg-3")
    assert a.kind == "reference"


# ---- Message ---------------------------------------------------------------

def test_message_from_graph_json_minimal():
    raw = {
        "id": "msg-id",
        "internetMessageId": "<abc@example.com>",
        "conversationId": "conv-id",
        "conversationIndex": "AQ==",
        "parentFolderId": "folder-id",
        "subject": "Hello",
        "sender": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "from": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
        "toRecipients": [
            {"emailAddress": {"name": "Bob", "address": "bob@example.com"}}
        ],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
        "receivedDateTime": "2026-04-24T10:00:00Z",
        "sentDateTime": "2026-04-24T09:59:55Z",
        "isRead": False,
        "isDraft": False,
        "hasAttachments": False,
        "importance": "normal",
        "flag": {"flagStatus": "notFlagged"},
        "categories": [],
        "inferenceClassification": "focused",
        "bodyPreview": "Hi...",
        "webLink": "https://outlook.office.com/?ItemID=AAMk...",
        "changeKey": "CQAAABYA...",
    }
    m = Message.from_graph_json(raw, mailbox_upn="me", parent_folder_path="/Inbox")
    assert m.id == "msg-id"
    assert m.internet_message_id == "<abc@example.com>"
    assert m.conversation_id == "conv-id"
    assert m.conversation_index == b"\x01"
    assert m.parent_folder_id == "folder-id"
    assert m.parent_folder_path == "/Inbox"
    assert m.subject == "Hello"
    assert m.sender == EmailAddress(name="Alice", address="alice@example.com")
    assert m.to == [EmailAddress(name="Bob", address="bob@example.com")]
    assert m.cc == []
    assert m.received_at == datetime(2026, 4, 24, 10, 0, tzinfo=timezone.utc)
    assert m.sent_at == datetime(2026, 4, 24, 9, 59, 55, tzinfo=timezone.utc)
    assert m.is_read is False
    assert m.is_draft is False
    assert m.has_attachments is False
    assert m.importance == "normal"
    assert m.flag.status == "notFlagged"
    assert m.categories == []
    assert m.inference_classification == "focused"
    assert m.body_preview == "Hi..."
    assert m.body is None
    assert m.web_link.startswith("https://outlook.office.com/")
    assert m.change_key == "CQAAABYA..."
    assert m.mailbox_upn == "me"


def test_message_from_graph_json_with_body_and_attachments():
    raw = {
        "id": "msg",
        "internetMessageId": "<x>",
        "conversationId": "c",
        "conversationIndex": "AQ==",
        "parentFolderId": "f",
        "subject": "Body test",
        "sender": {"emailAddress": {"name": "A", "address": "a@x"}},
        "from": {"emailAddress": {"name": "A", "address": "a@x"}},
        "toRecipients": [],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
        "receivedDateTime": "2026-04-24T10:00:00Z",
        "sentDateTime": None,
        "isRead": True,
        "isDraft": False,
        "hasAttachments": True,
        "importance": "high",
        "flag": {"flagStatus": "notFlagged"},
        "categories": ["Followup"],
        "inferenceClassification": "other",
        "bodyPreview": "p",
        "body": {"contentType": "html", "content": "<p>hi</p>"},
        "webLink": "https://x",
        "changeKey": "ck",
    }
    m = Message.from_graph_json(raw, mailbox_upn="me", parent_folder_path="/Inbox")
    assert m.sent_at is None
    assert m.is_read is True
    assert m.has_attachments is True
    assert m.importance == "high"
    assert m.categories == ["Followup"]
    assert m.inference_classification == "other"
    assert m.body == Body(content_type="html", content="<p>hi</p>")


# ---- MailboxSettings + AutomaticRepliesSetting -----------------------------

def test_auto_reply_from_graph_json_disabled():
    raw = {
        "status": "disabled",
        "externalAudience": "none",
        "scheduledStartDateTime": {"dateTime": "2026-04-24T00:00:00.0000000", "timeZone": "UTC"},
        "scheduledEndDateTime": {"dateTime": "2026-04-24T23:59:59.0000000", "timeZone": "UTC"},
        "internalReplyMessage": "",
        "externalReplyMessage": "",
    }
    ar = AutomaticRepliesSetting.from_graph_json(raw)
    assert ar.status == "disabled"
    assert ar.external_audience == "none"
    assert ar.scheduled_start == datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)


def test_mailbox_settings_from_graph_json_minimal():
    raw = {
        "timeZone": "Europe/Istanbul",
        "language": {"locale": "en-US", "displayName": "English (United States)"},
        "workingHours": {
            "daysOfWeek": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "startTime": "09:00:00.0000000",
            "endTime": "17:00:00.0000000",
            "timeZone": {"name": "Europe/Istanbul"},
        },
        "automaticRepliesSetting": {
            "status": "disabled",
            "externalAudience": "none",
            "scheduledStartDateTime": {"dateTime": "2026-04-24T00:00:00.0000000", "timeZone": "UTC"},
            "scheduledEndDateTime": {"dateTime": "2026-04-24T23:59:59.0000000", "timeZone": "UTC"},
            "internalReplyMessage": "",
            "externalReplyMessage": "",
        },
        "delegateMeetingMessageDeliveryOptions": "sendToDelegateAndInformationToPrincipal",
        "dateFormat": "yyyy-MM-dd",
        "timeFormat": "HH:mm",
    }
    s = MailboxSettings.from_graph_json(raw)
    assert s.timezone == "Europe/Istanbul"
    assert s.language == LocaleInfo(locale="en-US", display_name="English (United States)")
    assert s.working_hours.days == ["monday", "tuesday", "wednesday", "thursday", "friday"]
    assert s.working_hours.start_time == "09:00:00"
    assert s.working_hours.end_time == "17:00:00"
    assert s.auto_reply.status == "disabled"
    assert s.delegate_meeting_message_delivery == "sendToDelegateAndInformationToPrincipal"
    assert s.date_format == "yyyy-MM-dd"
    assert s.time_format == "HH:mm"
