from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.models.entities import (
    Case,
    CaseMember,
    CaseMemberRole,
    Deadline,
    InboxTriageItem,
    NotifyTargetType,
    ReminderDelivery,
    RiskLevel,
    ScheduleItem,
    TriageStatus,
    User,
    utcnow,
)
from app.services.wecom_notifier import dispatch_notification_to_users

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DEADLINE_NOTIFY_HOUR = 9


@dataclass
class ReminderSweepSummary:
    evaluated_at: datetime
    sent_count: int = 0
    skipped_completed_count: int = 0
    failed_count: int = 0
    already_processed_count: int = 0
    delivery_keys: list[str] = field(default_factory=list)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI_TZ).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_datetime_utc(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _to_utc(parsed)


def _load_active_users_map(session: Session) -> dict[int, User]:
    users = session.exec(select(User).where(User.is_active == True)).all()  # noqa: E712
    return {u.id: u for u in users if u.id is not None}


def _load_case_members_by_role(session: Session, case_id: int) -> dict[CaseMemberRole, list[int]]:
    grouped: dict[CaseMemberRole, list[int]] = {
        CaseMemberRole.LEAD: [],
        CaseMemberRole.CO_COUNSEL: [],
        CaseMemberRole.SUPERVISOR: [],
    }
    rows = session.exec(select(CaseMember).where(CaseMember.case_id == case_id)).all()
    for row in rows:
        grouped.setdefault(row.role_in_case, []).append(row.user_id)
    return grouped


def resolve_deadline_recipients(
    session: Session,
    case: Case,
    deadline: Deadline,
    days_remaining: int,
) -> tuple[list[int], bool]:
    """Resolve recipient user IDs and whether supervisor escalation triggered."""
    members_by_role = _load_case_members_by_role(session, deadline.case_id)
    lead_and_cocounsel = set(members_by_role[CaseMemberRole.LEAD]) | set(
        members_by_role[CaseMemberRole.CO_COUNSEL]
    )
    supervisors = set(members_by_role[CaseMemberRole.SUPERVISOR])

    target_type = deadline.notify_target_type
    recipient_ids: set[int] = set()
    escalated = False

    if target_type == NotifyTargetType.SELF:
        recipient_ids.add(deadline.override_by or case.owner_id)
    elif target_type == NotifyTargetType.CUSTOM_USERS:
        recipient_ids.update(deadline.custom_notify_user_ids or [])
        if not recipient_ids:
            recipient_ids.add(case.owner_id)
    elif target_type in (NotifyTargetType.CASE_TEAM, NotifyTargetType.BY_ROLE):
        recipient_ids.update(lead_and_cocounsel)
        if not recipient_ids:
            recipient_ids.add(case.owner_id)
        if (
            not deadline.is_completed
            and deadline.escalate_to_supervisor
            and deadline.risk_level == RiskLevel.CRITICAL
            and days_remaining <= 1
            and supervisors
        ):
            recipient_ids.update(supervisors)
            escalated = True

    return sorted(recipient_ids), escalated


def resolve_schedule_recipients(
    session: Session,
    case: Optional[Case],
    sched: ScheduleItem,
) -> list[int]:
    """Resolve recipient user IDs for a calendar ScheduleItem."""
    target_type = sched.notify_target_type
    recipient_ids: set[int] = set()

    if target_type == NotifyTargetType.SELF:
        recipient_ids.add(sched.owner_id)
    elif target_type == NotifyTargetType.CUSTOM_USERS:
        recipient_ids.update(sched.custom_notify_user_ids or [])
        if not recipient_ids:
            recipient_ids.add(sched.owner_id)
    else:
        recipient_ids.add(sched.owner_id)
        if sched.case_id is not None:
            members_by_role = _load_case_members_by_role(session, sched.case_id)
            recipient_ids.update(members_by_role[CaseMemberRole.LEAD])
            recipient_ids.update(members_by_role[CaseMemberRole.CO_COUNSEL])
            if case is not None:
                recipient_ids.add(case.owner_id)

    return sorted(recipient_ids)


def _deliver_with_idempotency(
    *,
    session: Session,
    idempotency_key: str,
    source_type: str,
    source_id: int,
    recipient_ids: list[int],
    scheduled_at_utc: datetime,
    is_completed_skip: bool,
    title: str,
    markdown_body: str,
    users_by_id: dict[int, User],
    summary: ReminderSweepSummary,
    settings: Settings,
    http_client: Optional[httpx.Client],
    textcard_url: Optional[str] = None,
) -> None:
    existing = session.exec(
        select(ReminderDelivery).where(ReminderDelivery.idempotency_key == idempotency_key)
    ).first()
    if existing is not None and existing.status in ("sent", "skipped_completed"):
        summary.already_processed_count += 1
        return

    delivery = existing or ReminderDelivery(
        idempotency_key=idempotency_key,
        source_type=source_type,
        source_id=source_id,
        recipient_user_ids=recipient_ids,
        scheduled_at=scheduled_at_utc,
        status="pending",
    )
    delivery.recipient_user_ids = recipient_ids
    delivery.scheduled_at = scheduled_at_utc

    if is_completed_skip:
        delivery.status = "skipped_completed"
        delivery.sent_at = None
        delivery.error_msg = None
        session.add(delivery)
        summary.skipped_completed_count += 1
        summary.delivery_keys.append(idempotency_key)
        return

    recipient_users = [users_by_id[uid] for uid in recipient_ids if uid in users_by_id]
    dispatch_res = dispatch_notification_to_users(
        users=recipient_users,
        title=title,
        markdown_body=markdown_body,
        textcard_url=textcard_url,
        settings=settings,
        http_client=http_client,
    )
    if dispatch_res.ok:
        delivery.status = "sent"
        delivery.sent_at = utcnow()
        delivery.error_msg = None
        summary.sent_count += 1
    else:
        delivery.status = "failed"
        delivery.sent_at = None
        delivery.error_msg = dispatch_res.error_msg or "Notification dispatch failed"
        summary.failed_count += 1

    session.add(delivery)
    summary.delivery_keys.append(idempotency_key)


def _evaluate_deadlines(
    *,
    session: Session,
    now_utc: datetime,
    now_sh: datetime,
    today_sh: date,
    cases_by_id: dict[int, Case],
    users_by_id: dict[int, User],
    summary: ReminderSweepSummary,
    settings: Settings,
    http_client: Optional[httpx.Client],
) -> None:
    horizon_min = today_sh - timedelta(days=1)
    deadlines = session.exec(
        select(Deadline).where(Deadline.due_date >= horizon_min).order_by(Deadline.due_date, Deadline.id)
    ).all()

    for dl in deadlines:
        if dl.id is None:
            continue
        case = cases_by_id.get(dl.case_id)
        if case is None:
            continue
        days_remaining = (dl.due_date - today_sh).days

        # 1) Rule-based day offsets
        offsets = sorted({int(x) for x in (dl.reminder_offsets_days or [])}, reverse=True)
        for offset in offsets:
            if days_remaining != offset:
                continue
            fire_date_sh = dl.due_date - timedelta(days=offset)
            trigger_sh = datetime.combine(
                fire_date_sh,
                time(hour=DEFAULT_DEADLINE_NOTIFY_HOUR, minute=0),
                tzinfo=SHANGHAI_TZ,
            )
            if now_sh < trigger_sh:
                continue

            recipient_ids, escalated = resolve_deadline_recipients(session, case, dl, days_remaining)
            prefix = "【⚠️督办合伙人升级预警】" if escalated else "【LawTeam 期限提醒】"
            title = f"{prefix}{dl.title}"
            case_no = f"（{case.case_number}）" if case.case_number else ""
            remaining_text = "今天到期" if days_remaining == 0 else f"剩余 {days_remaining} 天"
            markdown_body = (
                f"- **案件**：{case.case_title}{case_no}\n"
                f"- **事项**：{dl.title}（{dl.risk_level.value.upper()}）\n"
                f"- **到期日**：{dl.due_date.isoformat()}（{remaining_text}）\n"
                f"- **依据**：{dl.legal_basis or '自定义/程序规则'}"
            )
            if escalated:
                markdown_body += "\n- **升级说明**：红线期限临期 ≤ 1 天尚未标记完成，已自动抄送督办合伙人。"

            base_url = settings.frontend_base_url.rstrip("/")
            _deliver_with_idempotency(
                session=session,
                idempotency_key=f"deadline:{dl.id}:offset:{offset}:{dl.due_date.isoformat()}",
                source_type="deadline",
                source_id=dl.id,
                recipient_ids=recipient_ids,
                scheduled_at_utc=trigger_sh.astimezone(timezone.utc),
                is_completed_skip=dl.is_completed,
                title=title,
                markdown_body=markdown_body,
                users_by_id=users_by_id,
                summary=summary,
                settings=settings,
                http_client=http_client,
                textcard_url=f"{base_url}/cases/{dl.case_id}",
            )

        # 2) Personal custom timestamp alerts
        for alert in dl.personal_custom_alerts or []:
            if not isinstance(alert, dict):
                continue
            raw_uid = alert.get("user_id")
            fire_utc = _parse_iso_datetime_utc(alert.get("fire_at"))
            if not isinstance(raw_uid, int) or fire_utc is None:
                continue
            if now_utc < fire_utc:
                continue

            case_no = f"（{case.case_number}）" if case.case_number else ""
            markdown_body = (
                f"- **案件**：{case.case_title}{case_no}\n"
                f"- **个人定制闹钟**：{dl.title}\n"
                f"- **期限到期日**：{dl.due_date.isoformat()}"
            )
            _deliver_with_idempotency(
                session=session,
                idempotency_key=f"deadline_personal:{dl.id}:u{raw_uid}:{fire_utc.isoformat()}",
                source_type="deadline",
                source_id=dl.id,
                recipient_ids=[raw_uid],
                scheduled_at_utc=fire_utc,
                is_completed_skip=dl.is_completed,
                title=f"【个人期限闹钟】{dl.title}",
                markdown_body=markdown_body,
                users_by_id=users_by_id,
                summary=summary,
                settings=settings,
                http_client=http_client,
            )


def _evaluate_schedules(
    *,
    session: Session,
    now_utc: datetime,
    cases_by_id: dict[int, Case],
    users_by_id: dict[int, User],
    summary: ReminderSweepSummary,
    settings: Settings,
    http_client: Optional[httpx.Client],
) -> None:
    window_start = now_utc - timedelta(days=2)
    schedules = session.exec(
        select(ScheduleItem).where(ScheduleItem.end_time >= window_start).order_by(ScheduleItem.start_time, ScheduleItem.id)
    ).all()

    for sched in schedules:
        if sched.id is None:
            continue
        case = cases_by_id.get(sched.case_id) if sched.case_id is not None else None
        recipient_ids = resolve_schedule_recipients(session, case, sched)
        start_utc = _to_utc(sched.start_time)
        start_sh = start_utc.astimezone(SHANGHAI_TZ)

        offsets = sorted({int(m) for m in (sched.reminder_offsets_minutes or [])}, reverse=True)
        for offset_min in offsets:
            fire_utc = start_utc - timedelta(minutes=offset_min)
            if now_utc < fire_utc:
                continue

            case_line = f"- **关联案件**：{case.case_title}\n" if case is not None else ""
            if offset_min >= 1440:
                lead_desc = f"提前 {offset_min // 1440} 天"
            elif offset_min >= 60:
                lead_desc = f"提前 {offset_min // 60} 小时"
            else:
                lead_desc = f"提前 {offset_min} 分钟"
            markdown_body = (
                f"{case_line}"
                f"- **日程类别**：{sched.category}\n"
                f"- **开始时间**：{start_sh.strftime('%Y-%m-%d %H:%M')}（{lead_desc}）\n"
                f"- **地点**：{sched.location or '待定'}"
            )
            base_url = settings.frontend_base_url.rstrip("/")
            sched_url = f"{base_url}/cases/{sched.case_id}" if sched.case_id else f"{base_url}/calendar"
            _deliver_with_idempotency(
                session=session,
                idempotency_key=f"schedule:{sched.id}:min:{offset_min}:{start_utc.isoformat()}",
                source_type="schedule",
                source_id=sched.id,
                recipient_ids=recipient_ids,
                scheduled_at_utc=fire_utc,
                is_completed_skip=sched.is_completed,
                title=f"【LawTeam 日程提醒】{sched.title}",
                markdown_body=markdown_body,
                users_by_id=users_by_id,
                summary=summary,
                settings=settings,
                http_client=http_client,
                textcard_url=sched_url,
            )


def _evaluate_manual_triage_alerts(
    *,
    session: Session,
    now_utc: datetime,
    users_by_id: dict[int, User],
    summary: ReminderSweepSummary,
    settings: Settings,
    http_client: Optional[httpx.Client],
) -> None:
    items = session.exec(
        select(InboxTriageItem).where(InboxTriageItem.status == TriageStatus.MANUAL_FETCH_REQUIRED)
    ).all()

    for item in items:
        if item.id is None:
            continue
        payload = item.extracted_payload or {}
        case_no = payload.get("case_number") or "未识别案号"
        agency = payload.get("agency") or "司法送达平台"
        links = payload.get("court_links") or payload.get("invoice_links") or []
        link_line = f"\n- **原始链接**：{links[0]}" if links else ""
        markdown_body = (
            f"- **来源机关**：{agency}（{case_no}）\n"
            f"- **需要人工处理原因**：{item.error_or_manual_reason or '需人工点击核验或补充手机号后4位下载文书'}"
            f"{link_line}"
        )
        base_url = settings.frontend_base_url.rstrip("/")
        _deliver_with_idempotency(
            session=session,
            idempotency_key=f"triage_manual:{item.id}",
            source_type="triage_alert",
            source_id=item.id,
            recipient_ids=[item.owner_user_id],
            scheduled_at_utc=now_utc,
            is_completed_skip=False,
            title=f"【需人工核验取件】{agency} {case_no}",
            markdown_body=markdown_body,
            users_by_id=users_by_id,
            summary=summary,
            settings=settings,
            http_client=http_client,
            textcard_url=f"{base_url}/inbox?id={item.id}",
        )


def push_manual_fetch_alert(
    session: Session,
    triage_item: InboxTriageItem,
    *,
    settings: Optional[Settings] = None,
    http_client: Optional[httpx.Client] = None,
) -> ReminderSweepSummary:
    """Immediately push an idempotent alert when an inbox triage item requires manual action."""
    cfg = settings or get_settings()
    now_utc = utcnow()
    summary = ReminderSweepSummary(evaluated_at=now_utc)
    if triage_item.status != TriageStatus.MANUAL_FETCH_REQUIRED or triage_item.id is None:
        return summary

    users_by_id = _load_active_users_map(session)
    _evaluate_manual_triage_alerts(
        session=session,
        now_utc=now_utc,
        users_by_id=users_by_id,
        summary=summary,
        settings=cfg,
        http_client=http_client,
    )
    session.commit()
    return summary


def push_pending_confirm_alert(
    session: Session,
    triage_item: InboxTriageItem,
    *,
    settings: Optional[Settings] = None,
    http_client: Optional[httpx.Client] = None,
) -> ReminderSweepSummary:
    """Push an idempotent alert when an item has no covering auto-archive rule and needs human confirmation."""
    cfg = settings or get_settings()
    now_utc = utcnow()
    summary = ReminderSweepSummary(evaluated_at=now_utc)
    if triage_item.status != TriageStatus.PENDING or triage_item.id is None:
        return summary

    users_by_id = _load_active_users_map(session)
    payload = triage_item.extracted_payload or {}
    case_no = payload.get("case_number") or "未识别案号"
    hint = payload.get("doc_title_hint") or (
        "电子发票" if triage_item.item_type == "invoice_archive" else "程序短信/文书"
    )
    reason = payload.get("triage_reason") or "暂无覆盖规则或未匹配到唯一案件，请人工确认归档"
    base_url = cfg.frontend_base_url.rstrip("/")

    _deliver_with_idempotency(
        session=session,
        idempotency_key=f"triage_pending:{triage_item.id}",
        source_type="triage_alert",
        source_id=triage_item.id,
        recipient_ids=[triage_item.owner_user_id],
        scheduled_at_utc=now_utc,
        is_completed_skip=False,
        title=f"【待人工确认归档】{hint}（{case_no}）",
        markdown_body=f"- **事项**：{hint}\n- **案号**：{case_no}\n- **需确认原因**：{reason}",
        users_by_id=users_by_id,
        summary=summary,
        settings=cfg,
        http_client=http_client,
        textcard_url=f"{base_url}/inbox?id={triage_item.id}",
    )
    session.commit()
    return summary


def evaluate_due_reminders(
    session: Session,
    *,
    now: Optional[datetime] = None,
    settings: Optional[Settings] = None,
    http_client: Optional[httpx.Client] = None,
) -> ReminderSweepSummary:
    """Evaluate all due deadlines, personal alerts, schedules, and manual-fetch triage alerts."""
    cfg = settings or get_settings()
    now_utc = _to_utc(now or utcnow())
    now_sh = now_utc.astimezone(SHANGHAI_TZ)
    today_sh = now_sh.date()

    summary = ReminderSweepSummary(evaluated_at=now_utc)
    users_by_id = _load_active_users_map(session)
    cases = session.exec(select(Case)).all()
    cases_by_id = {c.id: c for c in cases if c.id is not None}

    _evaluate_deadlines(
        session=session,
        now_utc=now_utc,
        now_sh=now_sh,
        today_sh=today_sh,
        cases_by_id=cases_by_id,
        users_by_id=users_by_id,
        summary=summary,
        settings=cfg,
        http_client=http_client,
    )
    _evaluate_schedules(
        session=session,
        now_utc=now_utc,
        cases_by_id=cases_by_id,
        users_by_id=users_by_id,
        summary=summary,
        settings=cfg,
        http_client=http_client,
    )
    _evaluate_manual_triage_alerts(
        session=session,
        now_utc=now_utc,
        users_by_id=users_by_id,
        summary=summary,
        settings=cfg,
        http_client=http_client,
    )

    session.commit()
    return summary
