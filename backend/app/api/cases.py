from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import get_settings
from app.core.security import (
    check_case_access,
    get_accessible_case_ids_filter,
    get_current_user,
    record_audit_log,
)
from app.core.seed import load_stage_templates, resolve_default_subtype_and_stage
from app.database import get_session
from app.models.entities import (
    AuditLog,
    Case,
    CaseCategory,
    CaseEvent,
    CaseMember,
    CaseMemberRole,
    CaseVisibility,
    Deadline,
    Document,
    User,
    utcnow,
)
from app.services import file_bridge

router = APIRouter(prefix="/api/cases", tags=["cases"])


class CaseMemberInput(BaseModel):
    user_id: int
    role_in_case: CaseMemberRole = CaseMemberRole.CO_COUNSEL


class CaseMemberRead(BaseModel):
    id: int
    user_id: int
    username: str
    full_name: str
    calendar_color: str
    role_in_case: CaseMemberRole


class CaseCreate(BaseModel):
    case_title: str
    case_number: Optional[str] = None
    case_category: CaseCategory = CaseCategory.CIVIL
    procedure_subtype: Optional[str] = None
    current_stage: Optional[str] = None
    visibility: CaseVisibility = CaseVisibility.TEAM
    client_name: str
    client_id_last4: Optional[str] = None
    case_side: Optional[str] = None
    opponent_name: Optional[str] = None
    handling_agency: Optional[str] = None
    judge_or_officer_name: Optional[str] = None
    judge_or_officer_phone: Optional[str] = None
    is_foreign_related: bool = False
    custody_status: Optional[str] = None
    custody_place: Optional[str] = None
    crime_type: Optional[str] = None
    charge_name: Optional[str] = None
    trial_mode: Optional[str] = None
    members: list[CaseMemberInput] = []


class CaseUpdate(BaseModel):
    case_title: Optional[str] = None
    case_number: Optional[str] = None
    case_category: Optional[CaseCategory] = None
    procedure_subtype: Optional[str] = None
    current_stage: Optional[str] = None
    visibility: Optional[CaseVisibility] = None
    client_name: Optional[str] = None
    client_id_last4: Optional[str] = None
    case_side: Optional[str] = None
    opponent_name: Optional[str] = None
    handling_agency: Optional[str] = None
    judge_or_officer_name: Optional[str] = None
    judge_or_officer_phone: Optional[str] = None
    is_foreign_related: Optional[bool] = None
    custody_status: Optional[str] = None
    custody_place: Optional[str] = None
    crime_type: Optional[str] = None
    charge_name: Optional[str] = None
    trial_mode: Optional[str] = None
    is_archived: Optional[bool] = None


class CaseEventCreate(BaseModel):
    event_type: str
    event_date: date
    service_method: str = "electronic"
    authority_specified_days: Optional[int] = None
    notes: Optional[str] = None


class CaseListItem(BaseModel):
    id: int
    case_title: str
    case_number: Optional[str]
    case_category: CaseCategory
    procedure_subtype: str
    current_stage: str
    stage_entered_at: datetime
    stage_days_elapsed: int
    stale_warning: bool
    visibility: CaseVisibility
    owner_id: int
    client_name: str
    opponent_name: Optional[str]
    handling_agency: Optional[str]
    is_foreign_related: bool
    custody_status: Optional[str]
    custody_place: Optional[str]
    folder_rel_path: str
    is_archived: bool
    created_at: datetime
    members: list[CaseMemberRead]
    open_deadlines_count: int = 0
    next_deadline_date: Optional[date] = None


class CaseDetailRead(BaseModel):
    case: Case
    stage_days_elapsed: int
    stale_warning: bool
    members: list[CaseMemberRead]
    events: list[CaseEvent]
    deadlines: list[Deadline]
    documents: list[Document]
    files_tree: dict[str, list[dict[str, Any]]]


class CaseEventResponse(BaseModel):
    event: CaseEvent
    derived_deadlines: list[Deadline] = []


def _compute_stage_days(case: Case) -> tuple[int, bool]:
    tz = get_settings().tz
    entered_at = (
        case.stage_entered_at
        if case.stage_entered_at.tzinfo is not None
        else case.stage_entered_at.replace(tzinfo=timezone.utc)
    )
    entered_local = entered_at.astimezone(tz).date()
    today_local = datetime.now(tz).date()
    elapsed = max(0, (today_local - entered_local).days)
    stale_days = int(load_stage_templates().get("stale_days_default", 30))
    is_stale = (
        not case.is_archived
        and "归档" not in case.current_stage
        and elapsed >= stale_days
    )
    return elapsed, is_stale


def _resolve_default_subtype_and_stage(
    category: CaseCategory,
    requested_subtype: Optional[str],
    requested_stage: Optional[str],
) -> tuple[str, str]:
    return resolve_default_subtype_and_stage(category, requested_subtype, requested_stage)


def _fetch_case_members(session: Session, case_ids: list[int]) -> dict[int, list[CaseMemberRead]]:
    if not case_ids:
        return {}
    rows = session.exec(
        select(CaseMember, User)
        .join(User, User.id == CaseMember.user_id)
        .where(CaseMember.case_id.in_(case_ids))  # type: ignore[union-attr]
        .order_by(CaseMember.id)
    ).all()
    grouped: dict[int, list[CaseMemberRead]] = {cid: [] for cid in case_ids}
    for member, user in rows:
        if member.id is None or user.id is None:
            continue
        grouped.setdefault(member.case_id, []).append(
            CaseMemberRead(
                id=member.id,
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
                calendar_color=user.calendar_color,
                role_in_case=member.role_in_case,
            )
        )
    return grouped


@router.get("", response_model=list[CaseListItem])
def list_cases(
    case_category: Optional[CaseCategory] = Query(default=None),
    is_archived: Optional[bool] = Query(default=None),
    visibility: Optional[CaseVisibility] = Query(default=None),
    search: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[CaseListItem]:
    """List cases accessible to the current user according to RBAC and visibility."""
    accessible_ids = get_accessible_case_ids_filter(current_user, session)
    if not accessible_ids:
        return []

    stmt = select(Case).where(Case.id.in_(accessible_ids))  # type: ignore[union-attr]
    if case_category is not None:
        stmt = stmt.where(Case.case_category == case_category)
    if is_archived is not None:
        stmt = stmt.where(Case.is_archived == is_archived)
    if visibility is not None:
        stmt = stmt.where(Case.visibility == visibility)

    cases = list(session.exec(stmt.order_by(Case.created_at.desc())).all())
    if search:
        kw = search.strip().lower()
        cases = [
            c
            for c in cases
            if kw in (c.case_title or "").lower()
            or kw in (c.case_number or "").lower()
            or kw in (c.client_name or "").lower()
            or kw in (c.opponent_name or "").lower()
        ]

    case_ids = [c.id for c in cases if c.id is not None]
    members_map = _fetch_case_members(session, case_ids)

    open_deadlines = session.exec(
        select(Deadline).where(
            Deadline.case_id.in_(case_ids),  # type: ignore[union-attr]
            Deadline.is_completed == False,  # noqa: E712
        )
    ).all() if case_ids else []
    deadline_counts: dict[int, int] = {}
    next_dates: dict[int, date] = {}
    for dl in open_deadlines:
        deadline_counts[dl.case_id] = deadline_counts.get(dl.case_id, 0) + 1
        prev = next_dates.get(dl.case_id)
        if prev is None or dl.due_date < prev:
            next_dates[dl.case_id] = dl.due_date

    items: list[CaseListItem] = []
    for c in cases:
        assert c.id is not None
        elapsed, is_stale = _compute_stage_days(c)
        items.append(
            CaseListItem(
                id=c.id,
                case_title=c.case_title,
                case_number=c.case_number,
                case_category=c.case_category,
                procedure_subtype=c.procedure_subtype,
                current_stage=c.current_stage,
                stage_entered_at=c.stage_entered_at,
                stage_days_elapsed=elapsed,
                stale_warning=is_stale,
                visibility=c.visibility,
                owner_id=c.owner_id,
                client_name=c.client_name,
                opponent_name=c.opponent_name,
                handling_agency=c.handling_agency,
                is_foreign_related=c.is_foreign_related,
                custody_status=c.custody_status,
                custody_place=c.custody_place,
                folder_rel_path=c.folder_rel_path,
                is_archived=c.is_archived,
                created_at=c.created_at,
                members=members_map.get(c.id, []),
                open_deadlines_count=deadline_counts.get(c.id, 0),
                next_deadline_date=next_dates.get(c.id),
            )
        )
    return items


@router.post("", response_model=CaseDetailRead, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CaseDetailRead:
    """Create a multi-domain case and materialize its 5 standardized local folders."""
    assert current_user.id is not None
    subtype, stage = _resolve_default_subtype_and_stage(
        payload.case_category,
        payload.procedure_subtype,
        payload.current_stage,
    )
    if payload.visibility == CaseVisibility.PRIVATE:
        for m in payload.members:
            if m.user_id != current_user.id:
                raise HTTPException(
                    status_code=400,
                    detail="Private cases cannot have other team members",
                )

    seen_users: dict[int, CaseMemberRole] = {current_user.id: CaseMemberRole.LEAD}
    for m in payload.members:
        u = session.get(User, m.user_id)
        if u is None or not u.is_active:
            raise HTTPException(
                status_code=400,
                detail=f"Member user_id={m.user_id} not found or inactive",
            )
        seen_users[m.user_id] = m.role_in_case

    resolved_crime_type = (payload.crime_type or "").strip() or None
    if payload.case_category == CaseCategory.CRIMINAL and not resolved_crime_type:
        resolved_crime_type = "一般犯罪"

    now = utcnow()
    folder_rel_path = file_bridge.build_case_folder_rel_path(
        case_number=payload.case_number,
        client_name=payload.client_name,
        case_title=payload.case_title,
    )
    case = Case(
        case_title=payload.case_title.strip(),
        case_number=payload.case_number.strip() if payload.case_number else None,
        case_category=payload.case_category,
        procedure_subtype=subtype,
        current_stage=stage,
        stage_entered_at=now,
        visibility=payload.visibility,
        owner_id=current_user.id,
        client_name=payload.client_name.strip(),
        client_id_last4=payload.client_id_last4.strip()[:4] if payload.client_id_last4 else None,
        case_side=payload.case_side,
        opponent_name=payload.opponent_name,
        handling_agency=payload.handling_agency,
        judge_or_officer_name=payload.judge_or_officer_name,
        judge_or_officer_phone=payload.judge_or_officer_phone,
        is_foreign_related=payload.is_foreign_related,
        custody_status=payload.custody_status,
        custody_place=payload.custody_place,
        crime_type=resolved_crime_type,
        charge_name=payload.charge_name.strip() if payload.charge_name else None,
        trial_mode=payload.trial_mode,
        folder_rel_path=folder_rel_path,
        created_at=now,
        updated_at=now,
    )
    session.add(case)
    session.flush()
    assert case.id is not None

    for uid, role in seen_users.items():
        session.add(CaseMember(case_id=case.id, user_id=uid, role_in_case=role))

    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="case.create",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        detail={
            "case_title": case.case_title,
            "case_category": case.case_category.value,
            "folder_rel_path": case.folder_rel_path,
        },
    )
    session.commit()
    file_bridge.ensure_case_directories(folder_rel_path)
    session.refresh(case)
    return get_case_detail(case.id, current_user=current_user, session=session)


@router.get("/{case_id}", response_model=CaseDetailRead)
def get_case_detail(
    case_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CaseDetailRead:
    """Fetch complete case details, members, events, deadlines, and live NAS directory tree."""
    case = check_case_access(session.get(Case, case_id), current_user, session)
    assert case.id is not None
    elapsed, is_stale = _compute_stage_days(case)
    members = _fetch_case_members(session, [case.id]).get(case.id, [])
    events = list(
        session.exec(
            select(CaseEvent)
            .where(CaseEvent.case_id == case.id)
            .order_by(CaseEvent.event_date.desc(), CaseEvent.id.desc())
        ).all()
    )
    deadlines = list(
        session.exec(
            select(Deadline)
            .where(Deadline.case_id == case.id)
            .order_by(Deadline.is_completed, Deadline.due_date)
        ).all()
    )
    documents = list(
        session.exec(
            select(Document)
            .where(Document.case_id == case.id)
            .order_by(Document.created_at.desc())
        ).all()
    )
    files_tree = file_bridge.list_case_files(case.folder_rel_path)
    return CaseDetailRead(
        case=case,
        stage_days_elapsed=elapsed,
        stale_warning=is_stale,
        members=members,
        events=events,
        deadlines=deadlines,
        documents=documents,
        files_tree=files_tree,
    )


@router.patch("/{case_id}", response_model=CaseDetailRead)
def update_case(
    case_id: int,
    payload: CaseUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CaseDetailRead:
    """Update case metadata, criminal custody/defense dimensions, or advance procedure stage."""
    case = check_case_access(session.get(Case, case_id), current_user, session)
    assert case.id is not None
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return get_case_detail(case.id, current_user=current_user, session=session)

    sensitive_fields = {"visibility", "is_archived", "case_category"}
    if any(f in updates and getattr(case, f) != updates[f] for f in sensitive_fields):
        check_case_access(case, current_user, session, require_manage=True)
    if "visibility" in updates and updates["visibility"] == CaseVisibility.PRIVATE:
        if case.owner_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Only the case owner can change visibility to private",
            )

    non_nullable_fields = {
        "case_title",
        "client_name",
        "case_category",
        "procedure_subtype",
        "current_stage",
        "visibility",
        "is_foreign_related",
        "is_archived",
    }
    for req_field in non_nullable_fields:
        if req_field in updates and updates[req_field] is None:
            raise HTTPException(status_code=400, detail=f"{req_field} cannot be null")

    old_stage = case.current_stage
    old_visibility = case.visibility
    rule_affecting_fields = ("case_category", "is_foreign_related", "custody_status", "crime_type")
    old_rule_snapshot = {f: getattr(case, f) for f in rule_affecting_fields}

    if "client_id_last4" in updates:
        raw_id4 = updates["client_id_last4"]
        updates["client_id_last4"] = raw_id4.strip()[:4] if raw_id4 and raw_id4.strip() else None

    for field, val in updates.items():
        if isinstance(val, str):
            val = val.strip()
            if field not in non_nullable_fields:
                val = val or None
        setattr(case, field, val)

    if case.case_category == CaseCategory.CRIMINAL and not case.crime_type:
        case.crime_type = "一般犯罪"

    if old_visibility != CaseVisibility.PRIVATE and case.visibility == CaseVisibility.PRIVATE:
        other_members = session.exec(
            select(CaseMember).where(
                CaseMember.case_id == case.id,
                CaseMember.user_id != case.owner_id,
            )
        ).all()
        for om in other_members:
            session.delete(om)

    if "current_stage" in updates and updates["current_stage"] != old_stage:
        case.stage_entered_at = utcnow()
    case.updated_at = utcnow()

    session.add(case)
    session.flush()

    if any(getattr(case, f) != old_rule_snapshot[f] for f in rule_affecting_fields):
        from app.services.deadline_engine import derive_deadlines_for_event

        case_events = list(
            session.exec(select(CaseEvent).where(CaseEvent.case_id == case.id)).all()
        )
        for ev in case_events:
            derive_deadlines_for_event(session, case, ev)

    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="case.stage_change" if ("current_stage" in updates and updates["current_stage"] != old_stage) else "case.update",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        detail={"old_stage": old_stage, "updates": payload.model_dump(mode="json", exclude_unset=True)},
    )
    session.commit()
    return get_case_detail(case.id, current_user=current_user, session=session)


@router.put("/{case_id}/members", response_model=list[CaseMemberRead])
def replace_case_members(
    case_id: int,
    members: list[CaseMemberInput],
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[CaseMemberRead]:
    """Replace team member assignments for a case (lead/owner/admin only)."""
    case = check_case_access(session.get(Case, case_id), current_user, session, require_manage=True)
    assert case.id is not None

    if case.visibility == CaseVisibility.PRIVATE:
        for m in members:
            if m.user_id != case.owner_id:
                raise HTTPException(
                    status_code=400,
                    detail="Private cases cannot have other team members",
                )

    dedup: dict[int, CaseMemberRole] = {}
    for m in members:
        u = session.get(User, m.user_id)
        if u is None or not u.is_active:
            raise HTTPException(status_code=400, detail=f"User {m.user_id} not found or inactive")
        dedup[m.user_id] = m.role_in_case

    if not dedup:
        dedup[case.owner_id] = CaseMemberRole.LEAD

    existing = session.exec(select(CaseMember).where(CaseMember.case_id == case.id)).all()
    for row in existing:
        session.delete(row)
    session.flush()

    for uid, role in dedup.items():
        session.add(CaseMember(case_id=case.id, user_id=uid, role_in_case=role))

    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="case.members_replace",
        entity_type="case",
        entity_id=case.id,
        case_id=case.id,
        detail={"members": [{"user_id": uid, "role": role.value} for uid, role in dedup.items()]},
    )
    session.commit()
    return _fetch_case_members(session, [case.id]).get(case.id, [])


@router.post("/{case_id}/events", response_model=CaseEventResponse, status_code=status.HTTP_201_CREATED)
def create_case_event(
    case_id: int,
    payload: CaseEventCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CaseEventResponse:
    """Log an objective procedural event on a case (and trigger deterministic deadline derivation)."""
    assert current_user.id is not None
    case = check_case_access(session.get(Case, case_id), current_user, session)
    assert case.id is not None

    event = CaseEvent(
        case_id=case.id,
        event_type=payload.event_type.strip(),
        event_date=payload.event_date,
        service_method=payload.service_method,
        authority_specified_days=payload.authority_specified_days,
        notes=payload.notes,
        created_by=current_user.id,
    )
    session.add(event)
    session.flush()

    derived: list[Deadline] = []
    # Phase 2 hooks in deadline_engine.derive_deadlines_for_event cleanly here.
    try:
        from app.services.deadline_engine import derive_deadlines_for_event

        derived = derive_deadlines_for_event(session, case, event)
    except ImportError:
        derived = []

    from app.services.sms_parser import suggest_stage_for_event

    suggested_stage = suggest_stage_for_event(
        case.case_category.value,
        event.event_type,
        procedure_subtype=case.procedure_subtype,
        current_stage=case.current_stage,
    )
    if suggested_stage and suggested_stage != case.current_stage:
        now = utcnow()
        case.current_stage = suggested_stage
        case.stage_entered_at = now
        case.updated_at = now
        session.add(case)

    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="case.event_create",
        entity_type="case_event",
        entity_id=event.id,
        case_id=case.id,
        detail={
            "event_type": event.event_type,
            "event_date": event.event_date.isoformat(),
            "derived_deadlines": len(derived),
            "current_stage": case.current_stage,
        },
    )
    session.commit()
    session.refresh(event)
    for dl in derived:
        session.refresh(dl)
    return CaseEventResponse(event=event, derived_deadlines=derived)


@router.post("/{case_id}/events/{event_id}/recalculate", response_model=CaseEventResponse)
def recalculate_case_event_deadlines(
    case_id: int,
    event_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CaseEventResponse:
    """Re-run deterministic deadline derivation for an existing event (preserving manual overrides)."""
    from app.services.deadline_engine import derive_deadlines_for_event

    case = check_case_access(session.get(Case, case_id), current_user, session)
    event = session.get(CaseEvent, event_id)
    if event is None or event.case_id != case.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case event not found")

    derived = derive_deadlines_for_event(session, case, event)
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="case.event_recalculate",
        entity_type="case_event",
        entity_id=event.id,
        case_id=case.id,
        detail={"event_type": event.event_type, "derived_deadlines": len(derived)},
    )
    session.commit()
    session.refresh(event)
    for dl in derived:
        session.refresh(dl)
    return CaseEventResponse(event=event, derived_deadlines=derived)


@router.get("/{case_id}/files")
def list_case_folder_files(
    case_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict[str, list[dict[str, Any]]]:
    """List files across the 5 standard subfolders of a case."""
    case = check_case_access(session.get(Case, case_id), current_user, session)
    return file_bridge.list_case_files(case.folder_rel_path)


@router.post("/{case_id}/files/upload", response_model=Document, status_code=status.HTTP_201_CREATED)
async def upload_case_file(
    case_id: int,
    subfolder: str = Form(...),
    doc_subtype: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Document:
    """Upload a file into one of the 5 standard subfolders of a case."""
    assert current_user.id is not None
    case = check_case_access(session.get(Case, case_id), current_user, session)
    assert case.id is not None

    if subfolder not in file_bridge.STANDARD_CASE_SUBFOLDERS:
        raise HTTPException(
            status_code=400,
            detail=f"subfolder must be one of: {', '.join(file_bridge.STANDARD_CASE_SUBFOLDERS)}",
        )

    data = await file.read()
    dest_path, rel_path = file_bridge.save_to_case_subfolder(
        case.folder_rel_path,
        subfolder,
        file.filename or "upload.bin",
        data,
    )
    doc = Document(
        case_id=case.id,
        folder_category=subfolder,
        file_name=dest_path.name,
        rel_file_path=rel_path,
        doc_subtype=doc_subtype,
        uploaded_by=current_user.id,
    )
    session.add(doc)
    session.flush()
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="case.file_upload",
        entity_type="document",
        entity_id=doc.id,
        case_id=case.id,
        detail={"subfolder": subfolder, "rel_file_path": rel_path},
    )
    session.commit()
    session.refresh(doc)
    return doc


@router.get("/{case_id}/files/download")
def download_case_file(
    case_id: int,
    rel_path: str = Query(..., description="Relative path under CASES_ROOT inside this case folder"),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FileResponse:
    """Safely download a file belonging to the specified case."""
    case = check_case_access(session.get(Case, case_id), current_user, session)
    expected_prefix = f"{case.folder_rel_path.rstrip('/')}/"
    normalized_rel = rel_path.lstrip("/")
    if not normalized_rel.startswith(expected_prefix):
        raise HTTPException(status_code=403, detail="Requested file does not belong to this case")

    try:
        resolved = file_bridge.resolve_safe_path(normalized_rel, must_exist=True)
    except (file_bridge.PathSecurityError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path=Path(resolved), filename=resolved.name)


class AuditLogRead(BaseModel):
    id: int
    actor_user_id: Optional[int]
    actor_name: str
    action: str
    entity_type: str
    entity_id: Optional[int]
    case_id: Optional[int]
    detail: dict[str, Any]
    created_at: datetime


@router.get("/{case_id}/audit-logs", response_model=list[AuditLogRead])
def list_case_audit_logs(
    case_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[AuditLogRead]:
    """Return chronological audit trail entries scoped to this case."""
    case = check_case_access(session.get(Case, case_id), current_user, session)
    assert case.id is not None
    rows = list(
        session.exec(
            select(AuditLog)
            .where(AuditLog.case_id == case.id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())  # type: ignore[union-attr]
            .limit(limit)
        ).all()
    )
    actor_ids = {r.actor_user_id for r in rows if r.actor_user_id is not None}
    users_map = (
        {
            u.id: u.full_name
            for u in session.exec(select(User).where(User.id.in_(actor_ids))).all()  # type: ignore[union-attr]
            if u.id is not None
        }
        if actor_ids
        else {}
    )
    return [
        AuditLogRead(
            id=r.id,  # type: ignore[arg-type]
            actor_user_id=r.actor_user_id,
            actor_name=users_map.get(r.actor_user_id, "系统") if r.actor_user_id else "系统",
            action=r.action,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            case_id=r.case_id,
            detail=r.detail or {},
            created_at=r.created_at,
        )
        for r in rows
        if r.id is not None
    ]
