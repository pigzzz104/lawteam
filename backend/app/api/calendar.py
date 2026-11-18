from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import icalendar
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from app.core.security import (
    check_case_access,
    get_accessible_case_ids_filter,
    get_current_user,
    record_audit_log,
)
from app.database import get_session
from app.models.entities import (
    Case,
    CaseCategory,
    CaseMember,
    CaseMemberRole,
    Deadline,
    HolidayRollMode,
    InboxTriageItem,
    NotifyTargetType,
    RiskLevel,
    ScheduleItem,
    SystemRole,
    TriageStatus,
    User,
    utcnow,
)
from app.services.deadline_engine import calculate_due_date

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CONFLICT_CATEGORIES = {"hearing", "detention_meet", "file_review", "meeting"}

router = APIRouter(prefix="/api", tags=["calendar"])


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SHANGHAI_TZ)
    return dt.astimezone(timezone.utc)


def _to_shanghai(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SHANGHAI_TZ)


def _parse_triage_tentative_bounds(
    payload: dict[str, Any],
) -> Optional[tuple[str, str, bool]]:
    """Extract (start_iso, end_iso, all_day) from InboxTriageItem payload if present."""
    for key in ("hearing_time", "court_date", "due_date", "event_date"):
        raw = payload.get(key)
        if not raw:
            continue
        raw_str = str(raw).strip()
        try:
            if len(raw_str) == 10:
                d = date.fromisoformat(raw_str)
                return d.isoformat(), d.isoformat(), True
            dt = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
            dt_sh = _to_shanghai(dt) if dt.tzinfo else dt.replace(tzinfo=SHANGHAI_TZ)
            end_sh = dt_sh + timedelta(hours=1)
            return dt_sh.isoformat(), end_sh.isoformat(), False
        except ValueError:
            continue
    return None


class PersonalAlertItem(BaseModel):
    user_id: int
    fire_at: str


class DeadlineCreate(BaseModel):
    case_id: int
    title: str
    due_date: date
    legal_basis: Optional[str] = None
    holiday_roll_mode: HolidayRollMode = HolidayRollMode.ROLL_FORWARD_WORKDAY
    apply_holiday_roll: bool = False
    risk_level: RiskLevel = RiskLevel.HIGH
    override_reason: Optional[str] = None
    notify_target_type: NotifyTargetType = NotifyTargetType.CASE_TEAM
    custom_notify_user_ids: list[int] = PydanticField(default_factory=list)
    escalate_to_supervisor: bool = True
    reminder_offsets_days: list[int] = PydanticField(default_factory=lambda: [7, 3, 1, 0])
    personal_custom_alerts: list[PersonalAlertItem] = PydanticField(default_factory=list)


class DeadlineUpdate(BaseModel):
    title: Optional[str] = None
    due_date: Optional[date] = None
    legal_basis: Optional[str] = None
    holiday_roll_mode: Optional[HolidayRollMode] = None
    risk_level: Optional[RiskLevel] = None
    is_manual_override: Optional[bool] = None
    override_reason: Optional[str] = None
    is_completed: Optional[bool] = None
    notify_target_type: Optional[NotifyTargetType] = None
    custom_notify_user_ids: Optional[list[int]] = None
    escalate_to_supervisor: Optional[bool] = None
    reminder_offsets_days: Optional[list[int]] = None
    personal_custom_alerts: Optional[list[PersonalAlertItem]] = None


class ScheduleCreate(BaseModel):
    case_id: Optional[int] = None
    category: str = "hearing"
    title: str
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.HIGH
    notify_target_type: NotifyTargetType = NotifyTargetType.CASE_TEAM
    custom_notify_user_ids: list[int] = PydanticField(default_factory=list)
    reminder_offsets_minutes: list[int] = PydanticField(
        default_factory=lambda: [4320, 1440, 120]
    )


class ScheduleUpdate(BaseModel):
    case_id: Optional[int] = None
    category: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    risk_level: Optional[RiskLevel] = None
    notify_target_type: Optional[NotifyTargetType] = None
    custom_notify_user_ids: Optional[list[int]] = None
    reminder_offsets_minutes: Optional[list[int]] = None
    is_completed: Optional[bool] = None


class CalendarEventItem(BaseModel):
    id: str
    record_id: int
    source_type: str  # deadline | schedule | triage_tentative
    title: str
    start: str
    end: str
    all_day: bool
    is_tentative: bool = False
    case_id: Optional[int] = None
    case_title: Optional[str] = None
    case_number: Optional[str] = None
    case_category: Optional[CaseCategory] = None
    category: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.NORMAL
    rule_code: Optional[str] = None
    legal_basis: Optional[str] = None
    raw_due_date: Optional[date] = None
    due_date: Optional[date] = None
    holiday_roll_mode: Optional[HolidayRollMode] = None
    roll_reason: Optional[str] = None
    is_manual_override: bool = False
    is_completed: bool = False
    location: Optional[str] = None
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    color: str = "#3b82f6"
    status: Optional[str] = None


class CalendarConflict(BaseModel):
    user_id: int
    user_name: str
    schedule_id_a: int
    schedule_id_b: int
    title_a: str
    title_b: str
    overlap_start: str
    overlap_end: str
    message: str


class CalendarResponse(BaseModel):
    scope: str
    items: list[CalendarEventItem]
    conflicts: list[CalendarConflict]


def _get_user_involved_case_ids(user: User, accessible_ids: set[int], session: Session) -> set[int]:
    if not accessible_ids or user.id is None:
        return set()
    owned = set(
        cid
        for cid in session.exec(
            select(Case.id).where(Case.owner_id == user.id, Case.id.in_(accessible_ids))  # type: ignore[union-attr]
        ).all()
        if cid is not None
    )
    member = set(
        cid
        for cid in session.exec(
            select(CaseMember.case_id).where(
                CaseMember.user_id == user.id,
                CaseMember.case_id.in_(accessible_ids),  # type: ignore[union-attr]
            )
        ).all()
        if cid is not None
    )
    return owned | member


def _case_lawyer_participants(
    case_map: dict[int, Case], members_by_case: dict[int, list[CaseMember]]
) -> dict[int, set[int]]:
    participants: dict[int, set[int]] = {}
    for cid, case in case_map.items():
        uid_set: set[int] = {case.owner_id}
        for m in members_by_case.get(cid, []):
            if m.role_in_case in (CaseMemberRole.LEAD, CaseMemberRole.CO_COUNSEL):
                uid_set.add(m.user_id)
        participants[cid] = uid_set
    return participants


def _schedule_lawyers(
    sched: ScheduleItem,
    case_participants: dict[int, set[int]],
) -> set[int]:
    uids: set[int] = {sched.owner_id}
    if sched.notify_target_type == NotifyTargetType.CASE_TEAM and sched.case_id in case_participants:
        uids |= case_participants[sched.case_id]
    elif sched.notify_target_type == NotifyTargetType.CUSTOM_USERS:
        uids |= set(sched.custom_notify_user_ids or [])
    return uids


def _detect_schedule_conflicts(
    schedules: list[ScheduleItem],
    case_participants: dict[int, set[int]],
    users_by_id: dict[int, User],
) -> list[CalendarConflict]:
    active = [s for s in schedules if not s.is_completed and s.id is not None]
    active.sort(key=lambda s: (s.start_time, s.id or 0))
    conflicts: list[CalendarConflict] = []

    for i, a in enumerate(active):
        for b in active[i + 1 :]:
            if b.start_time >= a.end_time:
                break
            if a.category not in CONFLICT_CATEGORIES and b.category not in CONFLICT_CATEGORIES:
                continue
            overlap_start = max(a.start_time, b.start_time)
            overlap_end = min(a.end_time, b.end_time)
            if overlap_start >= overlap_end:
                continue
            shared_uids = sorted(
                _schedule_lawyers(a, case_participants)
                & _schedule_lawyers(b, case_participants)
            )
            for uid in shared_uids:
                user = users_by_id.get(uid)
                uname = user.full_name if user else f"User#{uid}"
                st_sh = _to_shanghai(overlap_start)
                en_sh = _to_shanghai(overlap_end)
                conflicts.append(
                    CalendarConflict(
                        user_id=uid,
                        user_name=uname,
                        schedule_id_a=a.id or 0,
                        schedule_id_b=b.id or 0,
                        title_a=a.title,
                        title_b=b.title,
                        overlap_start=st_sh.isoformat(),
                        overlap_end=en_sh.isoformat(),
                        message=(
                            f"{uname} 日程时间冲突：《{a.title}》与《{b.title}》在 "
                            f"{st_sh.strftime('%Y-%m-%d %H:%M')}-{en_sh.strftime('%H:%M')} 重叠"
                        ),
                    )
                )
    return conflicts


def _load_calendar_data(
    *,
    user: User,
    session: Session,
    scope: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    case_category: Optional[CaseCategory] = None,
    include_completed: bool = False,
) -> tuple[list[Deadline], list[ScheduleItem], dict[int, Case], dict[int, list[CaseMember]], dict[int, User]]:
    accessible_ids = get_accessible_case_ids_filter(user, session)
    my_case_ids = _get_user_involved_case_ids(user, accessible_ids, session)
    target_case_ids = my_case_ids if scope == "my" else accessible_ids

    cases = (
        list(session.exec(select(Case).where(Case.id.in_(accessible_ids))).all())  # type: ignore[union-attr]
        if accessible_ids
        else []
    )
    case_map = {c.id: c for c in cases if c.id is not None}

    if case_category is not None:
        target_case_ids = {
            cid for cid in target_case_ids if cid in case_map and case_map[cid].case_category == case_category
        }

    # Deadlines
    deadlines: list[Deadline] = []
    deadline_candidate_ids = accessible_ids if scope == "my" else target_case_ids
    if case_category is not None:
        deadline_candidate_ids = {
            cid
            for cid in deadline_candidate_ids
            if cid in case_map and case_map[cid].case_category == case_category
        }
    if deadline_candidate_ids:
        dl_stmt = select(Deadline).where(Deadline.case_id.in_(deadline_candidate_ids))  # type: ignore[union-attr]
        if not include_completed:
            dl_stmt = dl_stmt.where(Deadline.is_completed == False)  # noqa: E712
        if start is not None:
            dl_stmt = dl_stmt.where(Deadline.due_date >= start)
        if end is not None:
            dl_stmt = dl_stmt.where(Deadline.due_date <= end)
        for dl in session.exec(dl_stmt.order_by(Deadline.due_date, Deadline.id)).all():
            if scope == "my":
                if dl.case_id not in target_case_ids and (user.id not in (dl.custom_notify_user_ids or [])):
                    continue
            deadlines.append(dl)

    # Schedules
    sched_stmt = select(ScheduleItem)
    if not include_completed:
        sched_stmt = sched_stmt.where(ScheduleItem.is_completed == False)  # noqa: E712
    if start is not None:
        start_utc = datetime.combine(start, time.min, tzinfo=SHANGHAI_TZ).astimezone(timezone.utc)
        sched_stmt = sched_stmt.where(ScheduleItem.end_time >= start_utc)
    if end is not None:
        end_utc = datetime.combine(end, time.max, tzinfo=SHANGHAI_TZ).astimezone(timezone.utc)
        sched_stmt = sched_stmt.where(ScheduleItem.start_time <= end_utc)

    schedules: list[ScheduleItem] = []
    for sched in session.exec(sched_stmt.order_by(ScheduleItem.start_time, ScheduleItem.id)).all():
        if case_category is not None:
            if sched.case_id is None or sched.case_id not in target_case_ids:
                continue
        if sched.case_id is not None:
            if sched.case_id not in accessible_ids:
                continue
            if scope == "my":
                in_custom = user.id in (sched.custom_notify_user_ids or [])
                if sched.owner_id != user.id and sched.case_id not in target_case_ids and not in_custom:
                    continue
        else:
            if sched.owner_id != user.id and user.id not in (sched.custom_notify_user_ids or []):
                continue
        schedules.append(sched)

    member_rows = (
        list(session.exec(select(CaseMember).where(CaseMember.case_id.in_(accessible_ids))).all())  # type: ignore[union-attr]
        if accessible_ids
        else []
    )
    members_by_case: dict[int, list[CaseMember]] = {}
    for m in member_rows:
        members_by_case.setdefault(m.case_id, []).append(m)

    users_by_id = {u.id: u for u in session.exec(select(User)).all() if u.id is not None}
    return deadlines, schedules, case_map, members_by_case, users_by_id


@router.get("/calendar/events", response_model=CalendarResponse)
def get_calendar_events(
    scope: str = Query(default="my", pattern="^(my|team)$"),
    start: Optional[date] = Query(default=None),
    end: Optional[date] = Query(default=None),
    case_category: Optional[CaseCategory] = Query(default=None),
    include_completed: bool = Query(default=False),
    include_tentative: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CalendarResponse:
    """
    Unified team/personal calendar endpoint merging:
    1) Statutory & custom Deadlines (all-day events)
    2) Concrete ScheduleItems (hearings, detention meets, tasks)
    3) Tentative PENDING/MANUAL InboxTriageItems (is_tentative=True)
    Plus per-lawyer schedule conflict detection.
    """
    deadlines, schedules, case_map, members_by_case, users_by_id = _load_calendar_data(
        user=current_user,
        session=session,
        scope=scope,
        start=start,
        end=end,
        case_category=case_category,
        include_completed=include_completed,
    )

    items: list[CalendarEventItem] = []

    for dl in deadlines:
        if dl.id is None:
            continue
        c = case_map.get(dl.case_id)
        owner = users_by_id.get(c.owner_id) if c else None
        items.append(
            CalendarEventItem(
                id=f"deadline-{dl.id}",
                record_id=dl.id,
                source_type="deadline",
                title=dl.title,
                start=dl.due_date.isoformat(),
                end=dl.due_date.isoformat(),
                all_day=True,
                is_tentative=False,
                case_id=dl.case_id,
                case_title=c.case_title if c else None,
                case_number=c.case_number if c else None,
                case_category=c.case_category if c else None,
                category="deadline",
                risk_level=dl.risk_level,
                rule_code=dl.rule_code,
                legal_basis=dl.legal_basis,
                raw_due_date=dl.raw_due_date,
                due_date=dl.due_date,
                holiday_roll_mode=dl.holiday_roll_mode,
                roll_reason=dl.roll_reason,
                is_manual_override=dl.is_manual_override,
                is_completed=dl.is_completed,
                owner_id=c.owner_id if c else None,
                owner_name=owner.full_name if owner else None,
                color="#ef4444" if dl.risk_level == RiskLevel.CRITICAL else (
                    owner.calendar_color if owner else "#3b82f6"
                ),
            )
        )

    for sched in schedules:
        if sched.id is None:
            continue
        c = case_map.get(sched.case_id) if sched.case_id else None
        owner = users_by_id.get(sched.owner_id)
        st_sh = _to_shanghai(sched.start_time)
        en_sh = _to_shanghai(sched.end_time)
        items.append(
            CalendarEventItem(
                id=f"schedule-{sched.id}",
                record_id=sched.id,
                source_type="schedule",
                title=sched.title,
                start=st_sh.isoformat(),
                end=en_sh.isoformat(),
                all_day=False,
                is_tentative=False,
                case_id=sched.case_id,
                case_title=c.case_title if c else None,
                case_number=c.case_number if c else None,
                case_category=c.case_category if c else None,
                category=sched.category,
                risk_level=sched.risk_level,
                is_completed=sched.is_completed,
                location=sched.location,
                owner_id=sched.owner_id,
                owner_name=owner.full_name if owner else None,
                color=owner.calendar_color if owner else "#3b82f6",
            )
        )

    if include_tentative:
        accessible_ids = set(case_map.keys())
        triage_rows = session.exec(
            select(InboxTriageItem).where(
                InboxTriageItem.status.in_(  # type: ignore[attr-defined]
                    [TriageStatus.PENDING, TriageStatus.MANUAL_FETCH_REQUIRED]
                )
            )
        ).all()
        for item in triage_rows:
            if item.id is None:
                continue
            if item.matched_case_id is not None and item.matched_case_id not in accessible_ids:
                continue
            if scope == "my" and item.owner_user_id != current_user.id:
                continue
            if (
                scope == "team"
                and item.matched_case_id is None
                and item.owner_user_id != current_user.id
                and current_user.system_role != SystemRole.ADMIN
            ):
                continue

            c = case_map.get(item.matched_case_id) if item.matched_case_id else None
            if case_category is not None and (c is None or c.case_category != case_category):
                continue

            bounds = _parse_triage_tentative_bounds(item.extracted_payload or {})
            if bounds is None:
                continue
            ev_start, ev_end, is_all_day = bounds
            ev_date = date.fromisoformat(ev_start[:10])
            if start is not None and ev_date < start:
                continue
            if end is not None and ev_date > end:
                continue

            payload = item.extracted_payload or {}
            t_title = (
                payload.get("title")
                or payload.get("doc_title_hint")
                or payload.get("doc_type")
                or payload.get("event_type_guess")
                or payload.get("event_type")
                or "待确认短信日程"
            )
            owner = users_by_id.get(item.owner_user_id)
            items.append(
                CalendarEventItem(
                    id=f"triage-{item.id}",
                    record_id=item.id,
                    source_type="triage_tentative",
                    title=f"[待确认] {t_title}",
                    start=ev_start,
                    end=ev_end,
                    all_day=is_all_day,
                    is_tentative=True,
                    case_id=item.matched_case_id,
                    case_title=c.case_title if c else None,
                    case_number=c.case_number if c else None,
                    case_category=c.case_category if c else None,
                    category="triage_tentative",
                    risk_level=RiskLevel.HIGH,
                    location=payload.get("hearing_location")
                    or payload.get("court_room")
                    or payload.get("location"),
                    owner_id=item.owner_user_id,
                    owner_name=owner.full_name if owner else None,
                    color="#94a3b8",
                    status=item.status.value,
                )
            )

    case_participants = _case_lawyer_participants(case_map, members_by_case)
    conflicts = _detect_schedule_conflicts(schedules, case_participants, users_by_id)
    return CalendarResponse(scope=scope, items=items, conflicts=conflicts)


@router.get("/calendar/ics/{ics_feed_token}.ics")
def export_ics_feed(
    ics_feed_token: str,
    scope: str = Query(default="my", pattern="^(my|team)$"),
    session: Session = Depends(get_session),
) -> Response:
    """RFC 5545 iCalendar feed (VEVENT + VTODO in Asia/Shanghai) for Apple/Outlook subscription."""
    user = session.exec(select(User).where(User.ics_feed_token == ics_feed_token)).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid calendar token")

    deadlines, schedules, case_map, _, _ = _load_calendar_data(
        user=user,
        session=session,
        scope=scope,
        include_completed=True,
    )

    cal = icalendar.Calendar()
    cal.add("prodid", "-//LawTeam-OS//Legal Calendar//ZH")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", f"LawTeam-OS ({user.full_name})")
    cal.add("x-wr-timezone", "Asia/Shanghai")

    risk_labels = {
        RiskLevel.CRITICAL: "致命死线",
        RiskLevel.HIGH: "刚性程序",
        RiskLevel.NORMAL: "常规节点",
        RiskLevel.LOW: "待办",
    }

    for dl in deadlines:
        if dl.id is None:
            continue
        c = case_map.get(dl.case_id)
        case_prefix = f"{c.case_title} - " if c else ""
        status_prefix = "[已办结] " if dl.is_completed else ""
        summary = f"{status_prefix}[{risk_labels.get(dl.risk_level, '期限')}] {case_prefix}{dl.title}"
        desc_parts = [
            f"案件：{c.case_title}" if c else "",
            f"案号：{c.case_number}" if (c and c.case_number) else "",
            f"法条依据：{dl.legal_basis}" if dl.legal_basis else "",
            f"起算基准日：{dl.base_date.isoformat()}",
            f"原始到期日：{dl.raw_due_date.isoformat()}",
            f"最终到期日：{dl.due_date.isoformat()}",
            f"顺延说明：{dl.roll_reason}" if dl.roll_reason else "",
        ]
        desc = "\n".join(part for part in desc_parts if part)

        vevent = icalendar.Event()
        vevent.add("uid", f"deadline-event-{dl.id}@lawteam.local")
        vevent.add("summary", summary)
        vevent.add("dtstart", dl.due_date)
        vevent.add("dtend", dl.due_date + timedelta(days=1))
        vevent.add("description", desc)
        cal.add_component(vevent)

        if not dl.is_completed:
            vtodo = icalendar.Todo()
            vtodo.add("uid", f"deadline-todo-{dl.id}@lawteam.local")
            vtodo.add("summary", summary)
            vtodo.add("due", datetime.combine(dl.due_date, time(17, 0), tzinfo=SHANGHAI_TZ))
            vtodo.add("status", "NEEDS-ACTION")
            vtodo.add("priority", 1 if dl.risk_level == RiskLevel.CRITICAL else 5)
            vtodo.add("description", desc)
            cal.add_component(vtodo)

    for sched in schedules:
        if sched.id is None:
            continue
        c = case_map.get(sched.case_id) if sched.case_id else None
        case_prefix = f"{c.case_title} - " if c else ""
        status_prefix = "[已完成] " if sched.is_completed else ""
        vevent = icalendar.Event()
        vevent.add("uid", f"schedule-{sched.id}@lawteam.local")
        vevent.add("summary", f"{status_prefix}[{sched.category}] {case_prefix}{sched.title}")
        vevent.add("dtstart", _to_shanghai(sched.start_time))
        vevent.add("dtend", _to_shanghai(sched.end_time))
        if sched.location:
            vevent.add("location", sched.location)
        cal.add_component(vevent)

    return Response(
        content=cal.to_ical(),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="lawteam.ics"'},
    )


@router.get("/deadlines", response_model=list[Deadline])
def list_deadlines(
    case_id: Optional[int] = Query(default=None),
    include_completed: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Deadline]:
    """List deadlines across accessible cases (or a specific case)."""
    accessible_ids = get_accessible_case_ids_filter(current_user, session)
    if case_id is not None:
        check_case_access(session.get(Case, case_id), current_user, session)
        stmt = select(Deadline).where(Deadline.case_id == case_id)
    elif accessible_ids:
        stmt = select(Deadline).where(Deadline.case_id.in_(accessible_ids))  # type: ignore[union-attr]
    else:
        return []

    if not include_completed:
        stmt = stmt.where(Deadline.is_completed == False)  # noqa: E712
    return list(session.exec(stmt.order_by(Deadline.due_date, Deadline.id)).all())


@router.post("/deadlines", response_model=Deadline, status_code=status.HTTP_201_CREATED)
def create_custom_deadline(
    payload: DeadlineCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Deadline:
    """Create a manual custom deadline on a case (marked is_manual_override=True)."""
    assert current_user.id is not None
    case = check_case_access(session.get(Case, payload.case_id), current_user, session)
    assert case.id is not None

    if payload.apply_holiday_roll:
        calc = calculate_due_date(
            event_date=payload.due_date,
            duration_value=0,
            duration_unit="days",
            holiday_roll_mode=payload.holiday_roll_mode,
        )
        raw_due = calc.raw_due_date
        due = calc.due_date
        roll_reason = calc.roll_reason
    else:
        raw_due = payload.due_date
        due = payload.due_date
        roll_reason = "律师手动创建自定义期限"

    dl = Deadline(
        case_id=case.id,
        source_event_id=None,
        rule_code="CUSTOM",
        title=payload.title.strip(),
        legal_basis=payload.legal_basis,
        base_date=payload.due_date,
        raw_due_date=raw_due,
        due_date=due,
        holiday_roll_mode=payload.holiday_roll_mode,
        roll_reason=roll_reason,
        risk_level=payload.risk_level,
        is_manual_override=True,
        override_by=current_user.id,
        override_at=utcnow(),
        override_reason=payload.override_reason or "手动创建自定义期限",
        is_completed=False,
        notify_target_type=payload.notify_target_type,
        custom_notify_user_ids=payload.custom_notify_user_ids,
        escalate_to_supervisor=payload.escalate_to_supervisor,
        reminder_offsets_days=payload.reminder_offsets_days,
        personal_custom_alerts=[a.model_dump() for a in payload.personal_custom_alerts],
    )
    session.add(dl)
    session.flush()
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="deadline.create",
        entity_type="deadline",
        entity_id=dl.id,
        case_id=case.id,
        detail={"title": dl.title, "due_date": dl.due_date.isoformat()},
    )
    session.commit()
    session.refresh(dl)
    return dl


@router.patch("/deadlines/{deadline_id}", response_model=Deadline)
def update_deadline(
    deadline_id: int,
    payload: DeadlineUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Deadline:
    """Manually override a deadline's due_date, completion state, or personal reminder alerts."""
    assert current_user.id is not None
    dl = session.get(Deadline, deadline_id)
    if dl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deadline not found")
    check_case_access(session.get(Case, dl.case_id), current_user, session)

    updates = payload.model_dump(exclude_unset=True)
    old_due_date = dl.due_date
    old_completed = dl.is_completed

    if "title" in updates and updates["title"] is not None:
        dl.title = updates["title"].strip()
    if "legal_basis" in updates:
        dl.legal_basis = updates["legal_basis"]
    if "risk_level" in updates and updates["risk_level"] is not None:
        dl.risk_level = updates["risk_level"]
    if "holiday_roll_mode" in updates and updates["holiday_roll_mode"] is not None:
        dl.holiday_roll_mode = updates["holiday_roll_mode"]
    if "due_date" in updates and updates["due_date"] is not None:
        dl.due_date = updates["due_date"]
    if "notify_target_type" in updates and updates["notify_target_type"] is not None:
        dl.notify_target_type = updates["notify_target_type"]
    if "custom_notify_user_ids" in updates and updates["custom_notify_user_ids"] is not None:
        dl.custom_notify_user_ids = updates["custom_notify_user_ids"]
    if "escalate_to_supervisor" in updates and updates["escalate_to_supervisor"] is not None:
        dl.escalate_to_supervisor = updates["escalate_to_supervisor"]
    if "reminder_offsets_days" in updates and updates["reminder_offsets_days"] is not None:
        dl.reminder_offsets_days = updates["reminder_offsets_days"]
    if "personal_custom_alerts" in updates and updates["personal_custom_alerts"] is not None:
        dl.personal_custom_alerts = updates["personal_custom_alerts"]

    override_triggered = (
        ("due_date" in updates and dl.due_date != old_due_date)
        or updates.get("is_manual_override") is True
        or ("override_reason" in updates and updates["override_reason"] is not None)
    )
    if override_triggered:
        dl.is_manual_override = True
        dl.override_by = current_user.id
        dl.override_at = utcnow()
        dl.override_reason = (
            updates.get("override_reason") or dl.override_reason or "律师手动调整到期日"
        )
        record_audit_log(
            session,
            actor_user_id=current_user.id,
            action="deadline.override",
            entity_type="deadline",
            entity_id=dl.id,
            case_id=dl.case_id,
            detail={
                "old_due_date": old_due_date.isoformat(),
                "new_due_date": dl.due_date.isoformat(),
                "override_reason": dl.override_reason,
            },
        )
    elif updates.get("is_manual_override") is False:
        dl.is_manual_override = False

    if "is_completed" in updates and updates["is_completed"] is not None:
        new_completed = bool(updates["is_completed"])
        if new_completed != old_completed:
            dl.is_completed = new_completed
            if new_completed:
                dl.completed_at = utcnow()
                dl.completed_by = current_user.id
                action_name = "deadline.complete"
            else:
                dl.completed_at = None
                dl.completed_by = None
                action_name = "deadline.reopen"
            record_audit_log(
                session,
                actor_user_id=current_user.id,
                action=action_name,
                entity_type="deadline",
                entity_id=dl.id,
                case_id=dl.case_id,
                detail={"title": dl.title},
            )

    session.add(dl)
    session.commit()
    session.refresh(dl)
    return dl


@router.delete("/deadlines/{deadline_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deadline(
    deadline_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    dl = session.get(Deadline, deadline_id)
    if dl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deadline not found")
    check_case_access(session.get(Case, dl.case_id), current_user, session, require_manage=True)
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="deadline.delete",
        entity_type="deadline",
        entity_id=dl.id,
        case_id=dl.case_id,
        detail={"title": dl.title, "due_date": dl.due_date.isoformat()},
    )
    session.delete(dl)
    session.commit()


@router.get("/schedules", response_model=list[ScheduleItem])
def list_schedules(
    case_id: Optional[int] = Query(default=None),
    include_completed: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ScheduleItem]:
    accessible_ids = get_accessible_case_ids_filter(current_user, session)
    if case_id is not None:
        check_case_access(session.get(Case, case_id), current_user, session)
        stmt = select(ScheduleItem).where(ScheduleItem.case_id == case_id)
    else:
        if accessible_ids:
            stmt = select(ScheduleItem).where(
                (ScheduleItem.case_id.in_(accessible_ids))  # type: ignore[union-attr]
                | ((ScheduleItem.case_id == None) & (ScheduleItem.owner_id == current_user.id))  # noqa: E711
            )
        else:
            stmt = select(ScheduleItem).where(
                ScheduleItem.case_id == None,  # noqa: E711
                ScheduleItem.owner_id == current_user.id,
            )

    if not include_completed:
        stmt = stmt.where(ScheduleItem.is_completed == False)  # noqa: E712
    return list(session.exec(stmt.order_by(ScheduleItem.start_time, ScheduleItem.id)).all())


@router.post("/schedules", response_model=ScheduleItem, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ScheduleItem:
    assert current_user.id is not None
    if payload.case_id is not None:
        check_case_access(session.get(Case, payload.case_id), current_user, session)

    start_utc = _to_utc(payload.start_time)
    end_utc = _to_utc(payload.end_time)
    if end_utc <= start_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )

    sched = ScheduleItem(
        case_id=payload.case_id,
        owner_id=current_user.id,
        category=payload.category.strip(),
        title=payload.title.strip(),
        start_time=start_utc,
        end_time=end_utc,
        location=payload.location,
        risk_level=payload.risk_level,
        notify_target_type=payload.notify_target_type,
        custom_notify_user_ids=payload.custom_notify_user_ids,
        reminder_offsets_minutes=payload.reminder_offsets_minutes,
        is_completed=False,
    )
    session.add(sched)
    session.flush()
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="schedule.create",
        entity_type="schedule_item",
        entity_id=sched.id,
        case_id=sched.case_id,
        detail={"title": sched.title, "category": sched.category},
    )
    session.commit()
    session.refresh(sched)
    return sched


@router.patch("/schedules/{schedule_id}", response_model=ScheduleItem)
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ScheduleItem:
    sched = session.get(ScheduleItem, schedule_id)
    if sched is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    if sched.case_id is not None:
        check_case_access(session.get(Case, sched.case_id), current_user, session)
    elif sched.owner_id != current_user.id and current_user.system_role != SystemRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    updates = payload.model_dump(exclude_unset=True)
    if "case_id" in updates and updates["case_id"] is not None:
        check_case_access(session.get(Case, updates["case_id"]), current_user, session)
        sched.case_id = updates["case_id"]
    if "category" in updates and updates["category"] is not None:
        sched.category = updates["category"].strip()
    if "title" in updates and updates["title"] is not None:
        sched.title = updates["title"].strip()
    if "start_time" in updates and updates["start_time"] is not None:
        sched.start_time = _to_utc(updates["start_time"])
    else:
        sched.start_time = _to_utc(sched.start_time)
    if "end_time" in updates and updates["end_time"] is not None:
        sched.end_time = _to_utc(updates["end_time"])
    else:
        sched.end_time = _to_utc(sched.end_time)
    if sched.end_time <= sched.start_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_time must be after start_time",
        )
    if "location" in updates:
        sched.location = updates["location"]
    if "risk_level" in updates and updates["risk_level"] is not None:
        sched.risk_level = updates["risk_level"]
    if "notify_target_type" in updates and updates["notify_target_type"] is not None:
        sched.notify_target_type = updates["notify_target_type"]
    if "custom_notify_user_ids" in updates and updates["custom_notify_user_ids"] is not None:
        sched.custom_notify_user_ids = updates["custom_notify_user_ids"]
    if "reminder_offsets_minutes" in updates and updates["reminder_offsets_minutes"] is not None:
        sched.reminder_offsets_minutes = updates["reminder_offsets_minutes"]
    if "is_completed" in updates and updates["is_completed"] is not None:
        sched.is_completed = bool(updates["is_completed"])

    session.add(sched)
    session.commit()
    session.refresh(sched)
    return sched


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    sched = session.get(ScheduleItem, schedule_id)
    if sched is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    if sched.case_id is not None:
        check_case_access(session.get(Case, sched.case_id), current_user, session)
    elif sched.owner_id != current_user.id and current_user.system_role != SystemRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="schedule.delete",
        entity_type="schedule_item",
        entity_id=sched.id,
        case_id=sched.case_id,
        detail={"title": sched.title},
    )
    session.delete(sched)
    session.commit()
