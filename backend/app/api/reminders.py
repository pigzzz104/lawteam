from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import get_settings
from app.core.security import check_case_access, get_accessible_case_ids_filter, get_current_user
from app.database import get_session
from app.models.entities import (
    Case,
    Deadline,
    InboxTriageItem,
    ReminderDelivery,
    ScheduleItem,
    SystemRole,
    User,
)
from app.services.reminder_scheduler import evaluate_due_reminders
from app.services.wecom_notifier import dispatch_notification_to_users

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


class TestPushRequest(BaseModel):
    title: str = "【LawTeam-OS 测试推送】通知通道连通性验证"
    message: str = "这是一条来自 LawTeam-OS 律所协同系统的测试提醒，说明您的个人微信 / 企业微信接收通道工作正常。"


class PersonalAlertAddRequest(BaseModel):
    fire_at: datetime


class ReminderSweepResponse(BaseModel):
    evaluated_at: datetime
    sent_count: int
    skipped_completed_count: int
    failed_count: int
    already_processed_count: int
    delivery_keys: list[str]


class TestPushResponse(BaseModel):
    ok: bool
    dry_run: bool
    channel: str
    summary: str
    error_msg: Optional[str] = None
    attempts: list[dict[str, Any]]


@router.get("/deliveries", response_model=list[ReminderDelivery])
def list_reminder_deliveries(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    source_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ReminderDelivery]:
    """List reminder delivery ledger records visible to the current user."""
    assert current_user.id is not None
    stmt = select(ReminderDelivery).order_by(ReminderDelivery.id.desc())  # type: ignore[union-attr]
    if status_filter:
        stmt = stmt.where(ReminderDelivery.status == status_filter)
    if source_type:
        stmt = stmt.where(ReminderDelivery.source_type == source_type)

    rows = list(session.exec(stmt.limit(limit * 3 if current_user.system_role != SystemRole.ADMIN else limit)).all())
    if current_user.system_role == SystemRole.ADMIN:
        return rows[:limit]

    accessible_ids = get_accessible_case_ids_filter(current_user, session)
    dl_ids = {r.source_id for r in rows if r.source_type == "deadline"}
    sched_ids = {r.source_id for r in rows if r.source_type == "schedule"}
    triage_ids = {r.source_id for r in rows if r.source_type == "triage_alert"}

    dl_cases = (
        {
            d.id: d.case_id
            for d in session.exec(select(Deadline).where(Deadline.id.in_(dl_ids))).all()  # type: ignore[union-attr]
            if d.id is not None
        }
        if dl_ids
        else {}
    )
    sched_map = (
        {
            s.id: s
            for s in session.exec(select(ScheduleItem).where(ScheduleItem.id.in_(sched_ids))).all()  # type: ignore[union-attr]
            if s.id is not None
        }
        if sched_ids
        else {}
    )
    triage_owners = (
        {
            t.id: t.owner_user_id
            for t in session.exec(select(InboxTriageItem).where(InboxTriageItem.id.in_(triage_ids))).all()  # type: ignore[union-attr]
            if t.id is not None
        }
        if triage_ids
        else {}
    )

    visible: list[ReminderDelivery] = []
    for row in rows:
        if current_user.id in (row.recipient_user_ids or []):
            visible.append(row)
        elif row.source_type == "deadline" and dl_cases.get(row.source_id) in accessible_ids:
            visible.append(row)
        elif row.source_type == "schedule":
            sched = sched_map.get(row.source_id)
            if sched is not None and (
                sched.owner_id == current_user.id
                or (sched.case_id is not None and sched.case_id in accessible_ids)
            ):
                visible.append(row)
        elif row.source_type == "triage_alert" and triage_owners.get(row.source_id) == current_user.id:
            visible.append(row)
        if len(visible) >= limit:
            break
    return visible


@router.post("/run-once", response_model=ReminderSweepResponse)
def trigger_reminder_sweep(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ReminderSweepResponse:
    """Evaluate all currently due reminders immediately (idempotent)."""
    summary = evaluate_due_reminders(session)
    return ReminderSweepResponse(
        evaluated_at=summary.evaluated_at,
        sent_count=summary.sent_count,
        skipped_completed_count=summary.skipped_completed_count,
        failed_count=summary.failed_count,
        already_processed_count=summary.already_processed_count,
        delivery_keys=summary.delivery_keys,
    )


@router.post("/test-push", response_model=TestPushResponse)
@router.post("/test-wecom", response_model=TestPushResponse)
def send_test_push(
    payload: Optional[TestPushRequest] = None,
    current_user: User = Depends(get_current_user),
) -> TestPushResponse:
    """Send a test notification to the current user via their configured WeCom/WeChat channel."""
    req = payload or TestPushRequest()
    settings = get_settings()
    res = dispatch_notification_to_users(
        users=[current_user],
        title=req.title,
        markdown_body=f"- **接收人**：{current_user.full_name}（{current_user.username}）\n- **说明**：{req.message}",
        settings=settings,
    )
    return TestPushResponse(
        ok=res.ok,
        dry_run=res.dry_run,
        channel=current_user.notify_channel or "wecom",
        summary=res.summary,
        error_msg=res.error_msg,
        attempts=[
            {
                "channel": a.channel,
                "target_summary": a.target_summary,
                "ok": a.ok,
                "dry_run": a.dry_run,
                "error": a.error,
                "response_payload": a.response_payload,
            }
            for a in res.attempts
        ],
    )


@router.post("/deadlines/{deadline_id}/personal-alerts", response_model=Deadline)
def add_personal_deadline_alert(
    deadline_id: int,
    payload: PersonalAlertAddRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Deadline:
    """Add or update a personal custom alert timestamp on a deadline for the current user."""
    assert current_user.id is not None
    dl = session.get(Deadline, deadline_id)
    if dl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deadline not found")
    check_case_access(session.get(Case, dl.case_id), current_user, session)

    fire_iso = payload.fire_at.isoformat()
    alerts = [a for a in (dl.personal_custom_alerts or []) if isinstance(a, dict)]
    if not any(a.get("user_id") == current_user.id and a.get("fire_at") == fire_iso for a in alerts):
        alerts.append({"user_id": current_user.id, "fire_at": fire_iso})
    dl.personal_custom_alerts = alerts
    session.add(dl)
    session.commit()
    session.refresh(dl)
    return dl
