from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.core.security import create_triage_action_token
from app.models.entities import (
    Case,
    CaseMember,
    CaseMemberRole,
    CaseVisibility,
    Deadline,
    InboxTriageItem,
    NotifyTargetType,
    ReminderDelivery,
    ScheduleItem,
    SystemRole,
    TriageStatus,
    User,
    utcnow,
)
from app.core.constants import RISK_LEVEL_LABELS
from app.services.wecom_notifier import dispatch_notification_to_users

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_DEADLINE_NOTIFY_HOUR = 9
MAX_DELIVERY_RETRIES = 3


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


def _db_to_utc(dt: datetime) -> datetime:
    """Interpret naive DB datetimes as UTC rather than Asia/Shanghai."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
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
    if case.visibility == CaseVisibility.PRIVATE:
        return [case.owner_id], False

    members_by_role = _load_case_members_by_role(session, deadline.case_id)
    lead_and_cocounsel = set(members_by_role[CaseMemberRole.LEAD]) | set(
        members_by_role[CaseMemberRole.CO_COUNSEL]
    )
    supervisors = set(members_by_role[CaseMemberRole.SUPERVISOR])

    target_type = deadline.notify_target_type
    recipient_ids: set[int] = set()
    escalated = False

    if target_type == NotifyTargetType.SELF:
        recipient_ids.add(deadline.notify_owner_id or deadline.override_by or case.owner_id)
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
    if case is not None and case.visibility == CaseVisibility.PRIVATE:
        return [case.owner_id]

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
    card_buttons: Optional[list[dict[str, Any]]] = None,
    now_utc: Optional[datetime] = None,
) -> None:
    current_now = _to_utc(now_utc) if now_utc is not None else utcnow()
    existing = session.exec(
        select(ReminderDelivery).where(ReminderDelivery.idempotency_key == idempotency_key)
    ).first()
    if existing is not None and existing.status in ("sent", "skipped_completed"):
        summary.already_processed_count += 1
        return
    if existing is not None and existing.status == "failed":
        if int(existing.retry_count or 0) >= MAX_DELIVERY_RETRIES:
            summary.already_processed_count += 1
            return
        if existing.next_retry_at is not None and _db_to_utc(existing.next_retry_at) > current_now:
            summary.already_processed_count += 1
            return

    active_recipient_ids = sorted({int(uid) for uid in recipient_ids if uid in users_by_id})
    delivery = existing or ReminderDelivery(
        idempotency_key=idempotency_key,
        source_type=source_type,
        source_id=source_id,
        recipient_user_ids=active_recipient_ids,
        sent_user_ids=[],
        retry_count=0,
        scheduled_at=scheduled_at_utc,
        status="pending",
    )
    delivery.recipient_user_ids = active_recipient_ids
    delivery.scheduled_at = scheduled_at_utc

    if is_completed_skip:
        delivery.status = "skipped_completed"
        delivery.sent_at = None
        delivery.error_msg = None
        delivery.next_retry_at = None
        session.add(delivery)
        summary.skipped_completed_count += 1
        summary.delivery_keys.append(idempotency_key)
        return

    already_sent = {int(uid) for uid in (delivery.sent_user_ids or [])}
    pending_ids = [uid for uid in active_recipient_ids if uid not in already_sent]
    if not pending_ids:
        if already_sent:
            delivery.sent_user_ids = sorted(already_sent)
            delivery.status = "sent"
            delivery.sent_at = current_now
            delivery.error_msg = None
            delivery.next_retry_at = None
            session.add(delivery)
            summary.sent_count += 1
            summary.delivery_keys.append(idempotency_key)
        return

    recipient_users = [users_by_id[uid] for uid in pending_ids if uid in users_by_id]
    dispatch_res = dispatch_notification_to_users(
        users=recipient_users,
        title=title,
        markdown_body=markdown_body,
        textcard_url=textcard_url,
        card_buttons=card_buttons,
        settings=settings,
        http_client=http_client,
    )
    failed_ids = [int(uid) for uid in (dispatch_res.failed_user_ids or [])]
    newly_sent = [int(uid) for uid in (dispatch_res.succeeded_user_ids or [])]
    if dispatch_res.ok and not failed_ids and not newly_sent:
        newly_sent = list(pending_ids)
    merged_sent = sorted(already_sent | set(newly_sent))
    delivery.sent_user_ids = merged_sent

    if dispatch_res.ok and not failed_ids:
        delivery.status = "sent"
        delivery.sent_at = current_now
        delivery.error_msg = None
        delivery.next_retry_at = None
        summary.sent_count += 1
    else:
        delivery.retry_count = int(delivery.retry_count or 0) + 1
        backoff_min = 5 * (2 ** max(delivery.retry_count - 1, 0))
        delivery.next_retry_at = (
            None
            if delivery.retry_count >= MAX_DELIVERY_RETRIES
            else (current_now + timedelta(minutes=backoff_min))
        )
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
        if case is None or case.is_archived:
            continue
        days_remaining = (dl.due_date - today_sh).days
        recipient_ids, escalated = resolve_deadline_recipients(session, case, dl, days_remaining)
        active_recipient_ids = [uid for uid in recipient_ids if uid in users_by_id]
        base_url = settings.frontend_base_url.rstrip("/")

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

            prefix = "【⚠️督办合伙人升级预警】" if escalated else "【LawTeam 期限提醒】"
            title = f"{prefix}{dl.title}"
            case_no = f"（{case.case_number}）" if case.case_number else ""
            remaining_text = "今天到期" if days_remaining == 0 else f"剩余 {days_remaining} 天"
            risk_label = RISK_LEVEL_LABELS.get(dl.risk_level, "重要")
            markdown_body = (
                f"- **案件**：{case.case_title}{case_no}\n"
                f"- **事项**：{dl.title}（{risk_label}）\n"
                f"- **到期日**：{dl.due_date.isoformat()}（{remaining_text}）\n"
                f"- **依据**：{dl.legal_basis or '自定义/程序规则'}"
            )
            if escalated:
                markdown_body += "\n- **升级说明**：该期限临期 ≤ 1 天尚未标记完成，已自动抄送督办合伙人。"

            _deliver_with_idempotency(
                session=session,
                idempotency_key=f"deadline:{dl.id}:offset:{offset}:{dl.due_date.isoformat()}",
                source_type="deadline",
                source_id=dl.id,
                recipient_ids=active_recipient_ids,
                scheduled_at_utc=trigger_sh.astimezone(timezone.utc),
                is_completed_skip=dl.is_completed,
                title=title,
                markdown_body=markdown_body,
                users_by_id=users_by_id,
                summary=summary,
                settings=settings,
                http_client=http_client,
                textcard_url=f"{base_url}/cases/{dl.case_id}",
                now_utc=now_utc,
            )

        # 1.5) Per-user default deadline offset preferences (beyond rule defaults)
        rule_offset_set = set(offsets)
        for uid in active_recipient_ids:
            user_pref = users_by_id.get(uid)
            if user_pref is None:
                continue
            extra_offsets = sorted(
                {int(x) for x in (user_pref.default_deadline_offsets_days or [])} - rule_offset_set,
                reverse=True,
            )
            for extra_offset in extra_offsets:
                if days_remaining != extra_offset:
                    continue
                fire_date_sh = dl.due_date - timedelta(days=extra_offset)
                trigger_sh = datetime.combine(
                    fire_date_sh,
                    time(hour=DEFAULT_DEADLINE_NOTIFY_HOUR, minute=0),
                    tzinfo=SHANGHAI_TZ,
                )
                if now_sh < trigger_sh:
                    continue
                case_no = f"（{case.case_number}）" if case.case_number else ""
                remaining_text = (
                    "今天到期" if days_remaining == 0 else f"剩余 {days_remaining} 天"
                )
                markdown_body = (
                    f"- **案件**：{case.case_title}{case_no}\n"
                    f"- **事项**：{dl.title}\n"
                    f"- **到期日**：{dl.due_date.isoformat()}（{remaining_text}，个人默认提醒）"
                )
                _deliver_with_idempotency(
                    session=session,
                    idempotency_key=f"deadline_pref:{dl.id}:u{uid}:offset:{extra_offset}:{dl.due_date.isoformat()}",
                    source_type="deadline",
                    source_id=dl.id,
                    recipient_ids=[uid],
                    scheduled_at_utc=trigger_sh.astimezone(timezone.utc),
                    is_completed_skip=dl.is_completed,
                    title=f"【个人默认期限提醒】{dl.title}",
                    markdown_body=markdown_body,
                    users_by_id=users_by_id,
                    summary=summary,
                    settings=settings,
                    http_client=http_client,
                    textcard_url=f"{base_url}/cases/{dl.case_id}",
                    now_utc=now_utc,
                )

        # 2) Personal custom timestamp alerts
        if dl.personal_custom_alerts:
            member_uid_set = set(
                session.exec(select(CaseMember.user_id).where(CaseMember.case_id == dl.case_id)).all()
            )
        else:
            member_uid_set = set()

        for alert in dl.personal_custom_alerts or []:
            if not isinstance(alert, dict):
                continue
            raw_uid = alert.get("user_id")
            fire_utc = _parse_iso_datetime_utc(alert.get("fire_at"))
            if not isinstance(raw_uid, int) or raw_uid not in users_by_id or fire_utc is None:
                continue
            if case.visibility == CaseVisibility.PRIVATE and raw_uid != case.owner_id:
                continue
            if case.visibility == CaseVisibility.TEAM:
                target_user = users_by_id[raw_uid]
                if (
                    raw_uid != case.owner_id
                    and target_user.system_role != SystemRole.ADMIN
                    and raw_uid not in member_uid_set
                ):
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
                textcard_url=f"{base_url}/cases/{dl.case_id}",
                now_utc=now_utc,
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
        if sched.case_id is not None and (case is None or case.is_archived):
            continue
        recipient_ids = [
            uid for uid in resolve_schedule_recipients(session, case, sched) if uid in users_by_id
        ]
        start_utc = _db_to_utc(sched.start_time)
        start_sh = start_utc.astimezone(SHANGHAI_TZ)

        offsets = sorted({int(m) for m in (sched.reminder_offsets_minutes or [])}, reverse=True)
        passed_offsets = [
            offset_min
            for offset_min in offsets
            if now_utc >= (start_utc - timedelta(minutes=offset_min))
        ]
        if not passed_offsets:
            continue
        # Only fire the most urgent (smallest) passed offset to prevent burst notifications
        for offset_min in [passed_offsets[-1]]:
            fire_utc = start_utc - timedelta(minutes=offset_min)

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
                now_utc=now_utc,
            )

        # Personal extra schedule reminder offsets beyond schedule defaults
        sched_offset_set = set(offsets)
        for uid in recipient_ids:
            user_pref = users_by_id.get(uid)
            if user_pref is None:
                continue
            extra_mins = sorted(
                {int(m) for m in (user_pref.default_schedule_offsets_minutes or [])}
                - sched_offset_set,
                reverse=True,
            )
            passed_extra = [
                m for m in extra_mins if now_utc >= (start_utc - timedelta(minutes=m))
            ]
            if not passed_extra:
                continue
            extra_min = passed_extra[-1]
            if extra_min >= 1440:
                lead_desc = f"提前 {extra_min // 1440} 天"
            elif extra_min >= 60:
                lead_desc = f"提前 {extra_min // 60} 小时"
            else:
                lead_desc = f"提前 {extra_min} 分钟"
            case_line = f"- **关联案件**：{case.case_title}\n" if case is not None else ""
            base_url = settings.frontend_base_url.rstrip("/")
            sched_url = (
                f"{base_url}/cases/{sched.case_id}" if sched.case_id else f"{base_url}/calendar"
            )
            _deliver_with_idempotency(
                session=session,
                idempotency_key=f"schedule_pref:{sched.id}:u{uid}:min:{extra_min}:{start_utc.isoformat()}",
                source_type="schedule",
                source_id=sched.id,
                recipient_ids=[uid],
                scheduled_at_utc=start_utc - timedelta(minutes=extra_min),
                is_completed_skip=sched.is_completed,
                title=f"【个人默认日程提醒】{sched.title}",
                markdown_body=(
                    f"{case_line}"
                    f"- **日程类别**：{sched.category}\n"
                    f"- **开始时间**：{start_sh.strftime('%Y-%m-%d %H:%M')}（{lead_desc}，个人默认提醒）\n"
                    f"- **地点**：{sched.location or '待定'}"
                ),
                users_by_id=users_by_id,
                summary=summary,
                settings=settings,
                http_client=http_client,
                textcard_url=sched_url,
                now_utc=now_utc,
            )


def _build_triage_card_actions(
    item: InboxTriageItem,
    *,
    settings: Settings,
) -> tuple[str, list[dict[str, Any]]]:
    """Generate signed quick-action links and WeCom interactive template_card buttons."""
    assert item.id is not None
    api_base = settings.public_api_url.rstrip("/")
    epoch = int((item.extracted_payload or {}).get("action_epoch") or 0)
    buttons: list[dict[str, Any]] = []
    links_md: list[str] = []

    if item.matched_case_id is not None:
        confirm_tok = create_triage_action_token(
            user_id=item.owner_user_id,
            triage_id=item.id,
            action="confirm",
            case_id=item.matched_case_id,
            action_epoch=epoch,
        )
        confirm_url = f"{api_base}/api/webhooks/triage-action?token={confirm_tok}"
        links_md.append(f"[一键确认归入推荐案件]({confirm_url})")
        buttons.append(
            {
                "text": "确认归入推荐案件",
                "style": 1,
                "key": f"triage:confirm:{item.id}:{item.matched_case_id}",
            }
        )

    ignore_tok = create_triage_action_token(
        user_id=item.owner_user_id,
        triage_id=item.id,
        action="ignore",
        action_epoch=epoch,
    )
    ignore_url = f"{api_base}/api/webhooks/triage-action?token={ignore_tok}"
    links_md.append(f"[忽略该条]({ignore_url})")
    buttons.append(
        {
            "text": "忽略",
            "style": 4,
            "key": f"triage:ignore:{item.id}",
        }
    )
    quick_line = "\n- **微信快捷操作**：" + " | ".join(links_md)
    return quick_line, buttons


def _deliver_single_manual_triage_alert(
    *,
    session: Session,
    item: InboxTriageItem,
    now_utc: datetime,
    users_by_id: dict[int, User],
    summary: ReminderSweepSummary,
    settings: Settings,
    http_client: Optional[httpx.Client],
) -> None:
    if item.id is None:
        return
    payload = item.extracted_payload or {}
    case_no = payload.get("case_number") or "未识别案号"
    agency = payload.get("agency") or "司法送达平台"
    links = payload.get("court_links") or payload.get("invoice_links") or []
    first_url = links[0].get("url") if links and isinstance(links[0], dict) else (links[0] if links else "")
    link_line = f"\n- **原始链接**：{first_url}" if first_url else ""
    quick_line, card_buttons = _build_triage_card_actions(item, settings=settings)
    markdown_body = (
        f"- **来源机关**：{agency}（{case_no}）\n"
        f"- **需要人工处理原因**：{item.error_or_manual_reason or '需人工点击核验或补充手机号后4位下载文书'}"
        f"{link_line}"
        f"{quick_line}"
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
        card_buttons=card_buttons,
        now_utc=now_utc,
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
        _deliver_single_manual_triage_alert(
            session=session,
            item=item,
            now_utc=now_utc,
            users_by_id=users_by_id,
            summary=summary,
            settings=settings,
            http_client=http_client,
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
    _deliver_single_manual_triage_alert(
        session=session,
        item=triage_item,
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
    quick_line, card_buttons = _build_triage_card_actions(triage_item, settings=cfg)

    _deliver_with_idempotency(
        session=session,
        idempotency_key=f"triage_pending:{triage_item.id}",
        source_type="triage_alert",
        source_id=triage_item.id,
        recipient_ids=[triage_item.owner_user_id],
        scheduled_at_utc=now_utc,
        is_completed_skip=False,
        title=f"【待人工确认归档】{hint}（{case_no}）",
        markdown_body=(
            f"- **事项**：{hint}\n"
            f"- **案号**：{case_no}\n"
            f"- **需确认原因**：{reason}"
            f"{quick_line}"
        ),
        users_by_id=users_by_id,
        summary=summary,
        settings=cfg,
        http_client=http_client,
        textcard_url=f"{base_url}/inbox?id={triage_item.id}",
        card_buttons=card_buttons,
        now_utc=now_utc,
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
