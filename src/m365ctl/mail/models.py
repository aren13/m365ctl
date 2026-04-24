"""Dataclass mirrors of Graph mail entities, plus ``from_graph_json`` parsers.

Every dataclass is ``frozen=True``. Parsers are defensive: missing optional
fields produce zero-values, but missing REQUIRED fields raise ``KeyError``
(catch at the call site if you need graceful degradation).

Spec reference: §8 (Data model).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


FlagStatus = Literal["notFlagged", "flagged", "complete"]
InferenceClassification = Literal["focused", "other"]
Importance = Literal["low", "normal", "high"]
BodyContentType = Literal["text", "html"]
ExternalAudience = Literal["none", "contactsOnly", "all"]
AutoReplyStatus = Literal["disabled", "alwaysEnabled", "scheduled"]
AttachmentKind = Literal["file", "item", "reference"]


def _parse_graph_datetime(raw: dict | str | None) -> datetime | None:
    """Parse Graph's ``dateTime`` / timeZone pair or ISO-8601 string.

    Graph uses two shapes interchangeably:
    - ``"2026-04-24T10:00:00Z"`` (ISO-8601 with Z)
    - ``{"dateTime": "2026-04-24T10:00:00.0000000", "timeZone": "UTC"}``

    Returns ``None`` for None-or-empty inputs.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw:
            return None
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    dt_str = raw.get("dateTime")
    tz_str = raw.get("timeZone") or "UTC"
    if not dt_str:
        return None
    # Graph's 7-digit microseconds exceed Python's 6-digit limit — trim.
    if "." in dt_str:
        head, frac = dt_str.split(".", 1)
        frac = frac[:6]
        dt_str = f"{head}.{frac}"
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None and tz_str.upper() == "UTC":
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class EmailAddress:
    name: str
    address: str

    @classmethod
    def from_graph_json(cls, raw: dict | None) -> "EmailAddress":
        if raw is None:
            return cls(name="", address="")
        inner = raw.get("emailAddress", raw)
        return cls(
            name=inner.get("name", "") or "",
            address=inner.get("address", "") or "",
        )


@dataclass(frozen=True)
class Body:
    content_type: BodyContentType
    content: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Body":
        return cls(content_type=raw["contentType"], content=raw.get("content", ""))


@dataclass(frozen=True)
class Flag:
    status: FlagStatus
    start_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Flag":
        return cls(
            status=raw.get("flagStatus", "notFlagged"),
            start_at=_parse_graph_datetime(raw.get("startDateTime")),
            due_at=_parse_graph_datetime(raw.get("dueDateTime")),
            completed_at=_parse_graph_datetime(raw.get("completedDateTime")),
        )


@dataclass(frozen=True)
class Folder:
    id: str
    mailbox_upn: str
    display_name: str
    parent_id: str | None
    path: str
    total_items: int
    unread_items: int
    child_folder_count: int
    well_known_name: str | None

    @classmethod
    def from_graph_json(cls, raw: dict, *, mailbox_upn: str, path: str) -> "Folder":
        return cls(
            id=raw["id"],
            mailbox_upn=mailbox_upn,
            display_name=raw.get("displayName", ""),
            parent_id=raw.get("parentFolderId"),
            path=path,
            total_items=raw.get("totalItemCount", 0),
            unread_items=raw.get("unreadItemCount", 0),
            child_folder_count=raw.get("childFolderCount", 0),
            well_known_name=raw.get("wellKnownName"),
        )


@dataclass(frozen=True)
class Category:
    id: str
    display_name: str
    color: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Category":
        return cls(
            id=raw["id"],
            display_name=raw.get("displayName", ""),
            color=raw.get("color", "preset0"),
        )


@dataclass(frozen=True)
class Rule:
    id: str
    display_name: str
    sequence: int
    is_enabled: bool
    has_error: bool
    is_read_only: bool
    conditions: dict
    actions: dict
    exceptions: dict

    @classmethod
    def from_graph_json(cls, raw: dict) -> "Rule":
        return cls(
            id=raw["id"],
            display_name=raw.get("displayName", ""),
            sequence=raw.get("sequence", 0),
            is_enabled=raw.get("isEnabled", False),
            has_error=raw.get("hasError", False),
            is_read_only=raw.get("isReadOnly", False),
            conditions=raw.get("conditions", {}) or {},
            actions=raw.get("actions", {}) or {},
            exceptions=raw.get("exceptions", {}) or {},
        )


_ATTACHMENT_KIND_BY_ODATA_TYPE = {
    "#microsoft.graph.fileAttachment": "file",
    "#microsoft.graph.itemAttachment": "item",
    "#microsoft.graph.referenceAttachment": "reference",
}


@dataclass(frozen=True)
class Attachment:
    id: str
    message_id: str
    kind: AttachmentKind
    name: str
    content_type: str
    size: int
    is_inline: bool
    content_id: str | None

    @classmethod
    def from_graph_json(cls, raw: dict, *, message_id: str) -> "Attachment":
        odata_type = raw.get("@odata.type", "")
        kind = _ATTACHMENT_KIND_BY_ODATA_TYPE.get(odata_type, "file")
        return cls(
            id=raw["id"],
            message_id=message_id,
            kind=kind,  # type: ignore[arg-type]
            name=raw.get("name", ""),
            content_type=raw.get("contentType", ""),
            size=raw.get("size", 0),
            is_inline=raw.get("isInline", False),
            content_id=raw.get("contentId"),
        )


@dataclass(frozen=True)
class LocaleInfo:
    locale: str
    display_name: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "LocaleInfo":
        return cls(
            locale=raw.get("locale", ""),
            display_name=raw.get("displayName", ""),
        )


@dataclass(frozen=True)
class WorkingHours:
    days: list[str]
    start_time: str
    end_time: str
    time_zone: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "WorkingHours":
        tz_block = raw.get("timeZone", {}) or {}
        def _trim(t: str) -> str:
            return t.split(".", 1)[0] if t else ""
        return cls(
            days=list(raw.get("daysOfWeek", [])),
            start_time=_trim(raw.get("startTime", "")),
            end_time=_trim(raw.get("endTime", "")),
            time_zone=tz_block.get("name", ""),
        )


@dataclass(frozen=True)
class AutomaticRepliesSetting:
    status: AutoReplyStatus
    external_audience: ExternalAudience
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    internal_reply_message: str
    external_reply_message: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "AutomaticRepliesSetting":
        return cls(
            status=raw.get("status", "disabled"),
            external_audience=raw.get("externalAudience", "none"),
            scheduled_start=_parse_graph_datetime(raw.get("scheduledStartDateTime")),
            scheduled_end=_parse_graph_datetime(raw.get("scheduledEndDateTime")),
            internal_reply_message=raw.get("internalReplyMessage", ""),
            external_reply_message=raw.get("externalReplyMessage", ""),
        )


@dataclass(frozen=True)
class MailboxSettings:
    timezone: str
    language: LocaleInfo
    working_hours: WorkingHours
    auto_reply: AutomaticRepliesSetting
    delegate_meeting_message_delivery: str
    date_format: str
    time_format: str

    @classmethod
    def from_graph_json(cls, raw: dict) -> "MailboxSettings":
        return cls(
            timezone=raw.get("timeZone", ""),
            language=LocaleInfo.from_graph_json(raw.get("language", {}) or {}),
            working_hours=WorkingHours.from_graph_json(raw.get("workingHours", {}) or {}),
            auto_reply=AutomaticRepliesSetting.from_graph_json(
                raw.get("automaticRepliesSetting", {}) or {}
            ),
            delegate_meeting_message_delivery=raw.get("delegateMeetingMessageDeliveryOptions", ""),
            date_format=raw.get("dateFormat", ""),
            time_format=raw.get("timeFormat", ""),
        )


@dataclass(frozen=True)
class Message:
    id: str
    mailbox_upn: str
    internet_message_id: str
    conversation_id: str
    conversation_index: bytes
    parent_folder_id: str
    parent_folder_path: str
    subject: str
    sender: EmailAddress
    from_addr: EmailAddress
    to: list[EmailAddress]
    cc: list[EmailAddress]
    bcc: list[EmailAddress]
    reply_to: list[EmailAddress]
    received_at: datetime
    sent_at: datetime | None
    is_read: bool
    is_draft: bool
    has_attachments: bool
    importance: Importance
    flag: Flag
    categories: list[str]
    inference_classification: InferenceClassification
    body_preview: str
    body: Body | None
    web_link: str
    change_key: str

    @classmethod
    def from_graph_json(
        cls,
        raw: dict,
        *,
        mailbox_upn: str,
        parent_folder_path: str,
    ) -> "Message":
        def _addrs(key: str) -> list[EmailAddress]:
            return [EmailAddress.from_graph_json(x) for x in raw.get(key, []) or []]

        conv_idx_b64 = raw.get("conversationIndex", "") or ""
        conv_idx = base64.b64decode(conv_idx_b64) if conv_idx_b64 else b""

        received = _parse_graph_datetime(raw.get("receivedDateTime"))
        if received is None:
            raise ValueError("receivedDateTime missing from Graph message payload")

        body_raw = raw.get("body")
        body = Body.from_graph_json(body_raw) if body_raw else None

        return cls(
            id=raw["id"],
            mailbox_upn=mailbox_upn,
            internet_message_id=raw.get("internetMessageId", ""),
            conversation_id=raw.get("conversationId", ""),
            conversation_index=conv_idx,
            parent_folder_id=raw.get("parentFolderId", ""),
            parent_folder_path=parent_folder_path,
            subject=raw.get("subject", ""),
            sender=EmailAddress.from_graph_json(raw.get("sender")),
            from_addr=EmailAddress.from_graph_json(raw.get("from")),
            to=_addrs("toRecipients"),
            cc=_addrs("ccRecipients"),
            bcc=_addrs("bccRecipients"),
            reply_to=_addrs("replyTo"),
            received_at=received,
            sent_at=_parse_graph_datetime(raw.get("sentDateTime")),
            is_read=raw.get("isRead", False),
            is_draft=raw.get("isDraft", False),
            has_attachments=raw.get("hasAttachments", False),
            importance=raw.get("importance", "normal"),
            flag=Flag.from_graph_json(raw.get("flag", {}) or {}),
            categories=list(raw.get("categories", []) or []),
            inference_classification=raw.get("inferenceClassification", "focused"),
            body_preview=raw.get("bodyPreview", ""),
            body=body,
            web_link=raw.get("webLink", ""),
            change_key=raw.get("changeKey", ""),
        )
