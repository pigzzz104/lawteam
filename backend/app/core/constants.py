"""Single-source-of-truth domain enums, labels, and field sanitizers."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from app.models.entities import RiskLevel

RISK_LEVEL_LABELS: dict[RiskLevel, str] = {
    RiskLevel.CRITICAL: "紧急",
    RiskLevel.HIGH: "重要",
    RiskLevel.NORMAL: "常规",
    RiskLevel.LOW: "备忘",
}

ALLOWED_CRIME_TYPES: tuple[str, ...] = ("一般犯罪", "流窜/多次/结伙作案")

ALLOWED_NOTIFY_CHANNELS: tuple[str, ...] = (
    "wecom",
    "serverchan",
    "wxpusher",
    "pushplus",
    "webhook",
)


class AuditAction(str, Enum):
    """Canonical audit action identifiers shared across backend and OpenAPI contract."""

    CASE_CREATE = "case.create"
    CASE_AUTO_CREATE = "case.auto_create"
    CASE_UPDATE = "case.update"
    CASE_STAGE_CHANGE = "case.stage_change"
    CASE_MEMBERS_REPLACE = "case.members_replace"
    CASE_EVENT_CREATE = "case.event_create"
    CASE_EVENT_RECALCULATE = "case.event_recalculate"
    CASE_FILE_UPLOAD = "case.file_upload"

    DEADLINE_CREATE = "deadline.create"
    DEADLINE_OVERRIDE = "deadline.override"
    DEADLINE_COMPLETE = "deadline.complete"
    DEADLINE_REOPEN = "deadline.reopen"
    DEADLINE_DELETE = "deadline.delete"

    SCHEDULE_CREATE = "schedule.create"
    SCHEDULE_UPDATE = "schedule.update"
    SCHEDULE_DELETE = "schedule.delete"

    INBOX_AUTO_ARCHIVE = "inbox.auto_archive"
    INBOX_CONFIRM = "inbox.confirm"
    INBOX_REOPEN = "inbox.reopen"
    INBOX_IGNORE = "inbox.ignore"
    INBOX_AUTO_IGNORE = "inbox.auto_ignore"

    RULE_CREATE = "rule.create"
    RULE_UPDATE = "rule.update"
    RULE_DELETE = "rule.delete"
    RULE_RECALCULATE = "rule.recalculate"
    TRIAGE_POLICY_UPDATE = "triage_policy.update"

    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"


def clean_optional_str(value: Any, *, max_len: Optional[int] = None) -> Optional[str]:
    """Normalize nullable string input from PATCH/POST payloads to stripped text or None."""
    if value is None:
        return None
    text = str(value).strip()
    if max_len is not None:
        text = text[:max_len]
    return text or None
