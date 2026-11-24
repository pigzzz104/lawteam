"""Shared atomic triage archiving, confirmation, and undo/reopen service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.security import get_accessible_case_ids_filter, record_audit_log
from app.core.seed import resolve_default_subtype_and_stage
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
    InboxTriageItem,
    RiskLevel,
    ScheduleItem,
    TriageStatus,
    User,
    utcnow,
)
from app.services import file_bridge
from app.services.deadline_engine import derive_deadlines_for_event
from app.services.sms_parser import (
    _case_number_match_keys,
    normalize_case_number,
    suggest_stage_for_event,
)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class CaseGlobalConflictError(Exception):
    """Raised when global case-number dedup finds an archived or inaccessible existing case."""

    def __init__(self, conflict_kind: str, case: Optional[Case] = None) -> None:
        self.conflict_kind = conflict_kind
        self.case = case
        super().__init__(conflict_kind)


@dataclass
class TriageApplyResult:
    case_id: int
    created_event_id: Optional[int] = None
    generated_deadline_count: int = 0
    created_schedule_id: Optional[int] = None
    archived_documents: list[str] = field(default_factory=list)


def _to_utc(dt: datetime) -> datetime:
    localized = dt.replace(tzinfo=SHANGHAI_TZ) if dt.tzinfo is None else dt
    return localized.astimezone(timezone.utc)


def _parse_optional_date(raw: Any) -> Optional[date]:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and len(raw.strip()) >= 10:
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    return None


def _parse_optional_datetime(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            return dt.replace(tzinfo=SHANGHAI_TZ) if dt.tzinfo is None else dt
        except ValueError:
            return None
    return None


def find_global_cases_by_number(session: Session, case_number: str) -> list[Case]:
    """Search ALL cases globally (including archived/other users) by normalized case_number keys."""
    target_keys = _case_number_match_keys(case_number)
    if not target_keys:
        return []
    all_with_no = session.exec(select(Case).where(Case.case_number != None)).all()  # noqa: E711
    return [
        c for c in all_with_no if c.case_number and bool(_case_number_match_keys(c.case_number) & target_keys)
    ]


def auto_create_case_for_triage(
    session: Session,
    *,
    owner: User,
    payload: dict[str, Any],
) -> tuple[Case, bool, Optional[dict[str, Any]]]:
    """
    Create (or safely reuse under advisory lock) a Case from extracted SMS/PDF payload.

    Returns ``(case, created_case, related_case_hint)``.
    """
    assert owner.id is not None
    case_number = normalize_case_number(payload.get("case_number"))
    if not case_number:
        raise ValueError("Cannot auto-create case without a clear case_number")

    try:
        bind = session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:cn))"),
                {"cn": case_number},
            )
    except Exception:  # noqa: BLE001
        pass

    accessible_ids = get_accessible_case_ids_filter(owner, session)
    global_matches = find_global_cases_by_number(session, case_number)
    if global_matches:
        accessible_active = [
            c for c in global_matches if c.id in accessible_ids and not c.is_archived
        ]
        if accessible_active:
            return accessible_active[0], False, None
        accessible_archived = [
            c for c in global_matches if c.id in accessible_ids and c.is_archived
        ]
        if accessible_archived:
            raise CaseGlobalConflictError("archived_accessible", accessible_archived[0])
        raise CaseGlobalConflictError("inaccessible_existing", None)

    raw_cat = str(payload.get("case_category_guess") or "civil").strip()
    try:
        case_cat = CaseCategory(raw_cat)
    except ValueError:
        case_cat = CaseCategory.CIVIL

    subtype, stage = resolve_default_subtype_and_stage(case_cat)
    inv_meta = payload.get("invoice_meta") if isinstance(payload.get("invoice_meta"), dict) else {}
    client_raw = str(
        payload.get("client_name_guess")
        or payload.get("client_hint")
        or inv_meta.get("buyer_name")
        or ""
    ).strip()
    client_name = client_raw if len(client_raw) >= 2 else "待补充委托人"
    agency = str(payload.get("agency") or "").strip() or None
    case_title = (
        f"{case_number} {client_name}"
        if client_name != "待补充委托人"
        else f"{case_number} 待补充案件"
    )

    related_hint: Optional[dict[str, Any]] = None
    if client_name != "待补充委托人" and accessible_ids:
        related_cases = session.exec(
            select(Case).where(
                Case.id.in_(accessible_ids),  # type: ignore[union-attr]
                Case.is_archived == False,  # noqa: E712
                Case.client_name == client_name,
            )
        ).all()
        if related_cases:
            rc = related_cases[0]
            related_hint = {
                "case_id": rc.id,
                "case_title": rc.case_title,
                "case_number": rc.case_number,
                "procedure_subtype": rc.procedure_subtype,
            }

    now = utcnow()
    folder_rel_path = file_bridge.build_case_folder_rel_path(
        case_number=case_number,
        client_name=client_name,
        case_title=case_title,
    )
    new_case = Case(
        case_title=case_title,
        case_number=case_number,
        case_category=case_cat,
        procedure_subtype=subtype,
        current_stage=stage,
        stage_entered_at=now,
        visibility=CaseVisibility.TEAM,
        owner_id=owner.id,
        client_name=client_name,
        handling_agency=agency,
        crime_type="一般犯罪" if case_cat == CaseCategory.CRIMINAL else None,
        folder_rel_path=folder_rel_path,
        created_at=now,
        updated_at=now,
    )
    session.add(new_case)
    session.flush()
    assert new_case.id is not None

    session.add(
        CaseMember(
            case_id=new_case.id,
            user_id=owner.id,
            role_in_case=CaseMemberRole.LEAD,
        )
    )
    record_audit_log(
        session,
        actor_user_id=owner.id,
        action="case.auto_create",
        entity_type="case",
        entity_id=new_case.id,
        case_id=new_case.id,
        detail={
            "case_title": new_case.case_title,
            "case_number": new_case.case_number,
            "case_category": new_case.case_category.value,
        },
    )
    file_bridge.ensure_case_directories(folder_rel_path)
    return new_case, True, related_hint


def build_confirm_kwargs_from_triage(
    item: InboxTriageItem, target_case: Case
) -> dict[str, Any]:
    """Build complete Event/Deadline/Schedule/Subfolder kwargs from extracted triage payload."""
    payload = item.extracted_payload or {}
    if item.item_type == "invoice_archive":
        return {"file_subfolder": "05_财务与发票"}

    hearing_dt = _parse_optional_datetime(payload.get("hearing_time"))
    ev_type = str(payload.get("event_type_guess") or "").strip() or None
    if ev_type is None and hearing_dt is not None:
        ev_type = "hearing_scheduled"

    ev_date = _parse_optional_date(payload.get("event_date"))
    if ev_date is None:
        ev_date = hearing_dt.date() if hearing_dt is not None else datetime.now(SHANGHAI_TZ).date()

    auth_days_raw = payload.get("authority_specified_days")
    auth_days = int(auth_days_raw) if isinstance(auth_days_raw, int) and auth_days_raw > 0 else None
    doc_hint = str(payload.get("doc_title_hint") or "").strip() or None

    kwargs: dict[str, Any] = {
        "file_subfolder": "01_司法与程序文书",
        "event_type": ev_type,
        "event_date": ev_date if ev_type else None,
        "authority_specified_days": auth_days,
        "event_notes": f"自动识别入案：{doc_hint}" if doc_hint else "收件箱快捷确认入案",
        "new_stage": str(payload.get("suggested_stage") or "").strip() or None,
    }
    if hearing_dt is not None:
        kwargs["hearing_category"] = "hearing"
        kwargs["hearing_title"] = f"{target_case.case_number or target_case.case_title} 开庭审理"
        kwargs["hearing_start"] = hearing_dt
        kwargs["hearing_end"] = hearing_dt + timedelta(minutes=90)
        kwargs["hearing_location"] = payload.get("hearing_location")
    return kwargs


def apply_triage_to_case(
    session: Session,
    *,
    item: InboxTriageItem,
    actor_user: User,
    target_case: Case,
    file_subfolder: Optional[str] = None,
    event_type: Optional[str] = None,
    event_date: Optional[date] = None,
    service_method: str = "electronic",
    authority_specified_days: Optional[int] = None,
    event_notes: Optional[str] = None,
    new_stage: Optional[str] = None,
    auto_advance_stage: bool = True,
    hearing_category: str = "hearing",
    hearing_title: Optional[str] = None,
    hearing_start: Optional[datetime] = None,
    hearing_end: Optional[datetime] = None,
    hearing_location: Optional[str] = None,
    hearing_risk_level: RiskLevel = RiskLevel.HIGH,
    auto_archived: bool = False,
    triage_reason: Optional[str] = None,
    created_case: bool = False,
) -> TriageApplyResult:
    """Atomically archive triage files and commit Event/Deadlines/Schedule/Stage to ``target_case``."""
    assert actor_user.id is not None
    assert target_case.id is not None

    now = utcnow()
    default_subfolder = (
        "05_财务与发票" if item.item_type == "invoice_archive" else "01_司法与程序文书"
    )
    target_subfolder = file_subfolder or default_subfolder
    if target_subfolder not in file_bridge.STANDARD_CASE_SUBFOLDERS:
        raise ValueError(f"Invalid file_subfolder: {target_subfolder}")

    st_utc: Optional[datetime] = None
    en_utc: Optional[datetime] = None
    if hearing_start is not None and hearing_end is not None:
        st_utc = _to_utc(hearing_start)
        en_utc = _to_utc(hearing_end)
        if en_utc <= st_utc:
            raise ValueError("hearing_end must be after hearing_start")

    archived_paths: list[str] = []
    extracted = dict(item.extracted_payload or {})
    for src_rel in list(item.downloaded_files or []):
        desired_name = Path(src_rel).name
        new_rel = file_bridge.move_rel_file_to_case(
            src_rel,
            target_case.folder_rel_path,
            target_subfolder,
            desired_filename=desired_name,
        )
        archived_paths.append(new_rel)
        existing_doc = session.exec(
            select(Document).where(Document.rel_file_path == src_rel)
        ).first()
        if existing_doc is not None:
            existing_doc.case_id = target_case.id
            existing_doc.folder_category = target_subfolder
            existing_doc.file_name = Path(new_rel).name
            existing_doc.rel_file_path = new_rel
            session.add(existing_doc)
        else:
            session.add(
                Document(
                    case_id=target_case.id,
                    folder_category=target_subfolder,
                    file_name=Path(new_rel).name,
                    rel_file_path=new_rel,
                    doc_subtype=extracted.get("doc_title_hint"),
                    invoice_meta=extracted.get("invoice_meta"),
                    uploaded_by=actor_user.id,
                )
            )

    created_event_id: Optional[int] = None
    deadline_count = 0
    clean_event_type = event_type.strip() if event_type else None
    if clean_event_type and event_date:
        ev = CaseEvent(
            case_id=target_case.id,
            event_type=clean_event_type,
            event_date=event_date,
            service_method=service_method,
            authority_specified_days=authority_specified_days,
            source_sms_id=item.sms_id,
            notes=event_notes,
            created_by=actor_user.id,
        )
        session.add(ev)
        session.flush()
        created_event_id = ev.id
        deadlines = derive_deadlines_for_event(session, target_case, ev)
        deadline_count = len(deadlines)

    created_schedule_id: Optional[int] = None
    if st_utc is not None and en_utc is not None:
        sched = ScheduleItem(
            case_id=target_case.id,
            owner_id=actor_user.id,
            category=hearing_category,
            title=(hearing_title or "开庭审理").strip(),
            start_time=st_utc,
            end_time=en_utc,
            location=hearing_location,
            risk_level=hearing_risk_level,
        )
        session.add(sched)
        session.flush()
        created_schedule_id = sched.id

    previous_stage = target_case.current_stage
    resolved_stage = new_stage.strip() if new_stage and new_stage.strip() else None
    if not resolved_stage and auto_advance_stage and clean_event_type:
        resolved_stage = suggest_stage_for_event(
            target_case.case_category.value,
            clean_event_type,
            procedure_subtype=target_case.procedure_subtype,
            current_stage=target_case.current_stage,
        )
    if resolved_stage and resolved_stage != target_case.current_stage:
        extracted["previous_case_stage"] = previous_stage
        target_case.current_stage = resolved_stage
        target_case.stage_entered_at = now
    target_case.updated_at = now
    session.add(target_case)

    if created_case:
        extracted["auto_created_case_id"] = target_case.id
        extracted["auto_created_case"] = True
    extracted["auto_archived"] = auto_archived
    extracted["archived_subfolder"] = target_subfolder
    if created_event_id is not None:
        extracted["auto_created_event_id"] = created_event_id
    if created_schedule_id is not None:
        extracted["auto_created_schedule_id"] = created_schedule_id
    if auto_archived:
        extracted["triage_decision"] = "auto_archived"
    else:
        extracted["triage_decision"] = "confirmed"
    if triage_reason:
        extracted["triage_reason"] = triage_reason

    item.status = TriageStatus.CONFIRMED
    item.matched_case_id = target_case.id
    item.confirmed_by = actor_user.id
    item.confirmed_at = now
    item.error_or_manual_reason = None
    item.extracted_payload = extracted
    if archived_paths:
        item.downloaded_files = archived_paths
    session.add(item)

    record_audit_log(
        session,
        actor_user_id=actor_user.id,
        action="inbox.auto_archive" if auto_archived else "inbox.confirm",
        entity_type="inbox_triage_item",
        entity_id=item.id,
        case_id=target_case.id,
        detail={
            "auto_archived": auto_archived,
            "created_case": created_case,
            "created_event_id": created_event_id,
            "generated_deadline_count": deadline_count,
            "created_schedule_id": created_schedule_id,
            "archived_documents": archived_paths,
            "new_stage": target_case.current_stage,
            "reason": triage_reason,
        },
    )

    return TriageApplyResult(
        case_id=target_case.id,
        created_event_id=created_event_id,
        generated_deadline_count=deadline_count,
        created_schedule_id=created_schedule_id,
        archived_documents=archived_paths,
    )


def reopen_triage_item(
    session: Session,
    *,
    item: InboxTriageItem,
    actor_user: User,
) -> InboxTriageItem:
    """Undo a confirmed/auto-archived triage item back to ``PENDING`` for manual re-triage."""
    assert actor_user.id is not None
    extracted = dict(item.extracted_payload or {})
    prev_case_id = item.matched_case_id
    is_invoice = item.item_type == "invoice_archive"
    batch_key = f"triage_{item.id}_reopen"

    restored_files: list[str] = []
    for rel_path in list(item.downloaded_files or []):
        try:
            new_rel = file_bridge.move_rel_file_to_staging(
                rel_path,
                is_invoice=is_invoice,
                batch_key=batch_key,
                desired_filename=Path(rel_path).name,
            )
            restored_files.append(new_rel)
            existing_doc = session.exec(
                select(Document).where(Document.rel_file_path == rel_path)
            ).first()
            if existing_doc is not None:
                if is_invoice:
                    existing_doc.case_id = None
                    existing_doc.folder_category = file_bridge.UNCLAIMED_INVOICES_DIR
                    existing_doc.file_name = Path(new_rel).name
                    existing_doc.rel_file_path = new_rel
                    session.add(existing_doc)
                else:
                    session.delete(existing_doc)
        except (FileNotFoundError, file_bridge.PathSecurityError):
            continue

    sched_id = extracted.pop("auto_created_schedule_id", None)
    if isinstance(sched_id, int):
        sched = session.get(ScheduleItem, sched_id)
        if sched is not None:
            session.delete(sched)

    event_id = extracted.pop("auto_created_event_id", None)
    if isinstance(event_id, int):
        dls = list(
            session.exec(select(Deadline).where(Deadline.source_event_id == event_id)).all()
        )
        has_manual_override = False
        for dl in dls:
            if dl.is_manual_override:
                has_manual_override = True
            else:
                session.delete(dl)
        if not has_manual_override:
            ev = session.get(CaseEvent, event_id)
            if ev is not None:
                session.delete(ev)

    prev_stage = extracted.pop("previous_case_stage", None)
    auto_case_id = extracted.pop("auto_created_case_id", None)
    extracted.pop("auto_created_case", None)
    extracted.pop("archived_subfolder", None)
    extracted["action_epoch"] = int(extracted.get("action_epoch") or 0) + 1
    extracted["auto_archived"] = False
    extracted["triage_decision"] = "needs_confirm"
    extracted["triage_reason"] = "已撤回归档，请人工重新确认归属案件与事项"

    item.status = TriageStatus.PENDING
    item.confirmed_by = None
    item.confirmed_at = None
    item.error_or_manual_reason = None
    item.extracted_payload = extracted
    if restored_files:
        item.downloaded_files = restored_files
    session.add(item)
    session.flush()

    deleted_empty_auto_case = False
    if prev_case_id is not None:
        prev_case = session.get(Case, prev_case_id)
        if prev_case is not None:
            if isinstance(prev_stage, str) and prev_stage.strip():
                prev_case.current_stage = prev_stage.strip()
                session.add(prev_case)
            if auto_case_id == prev_case_id:
                has_ev = session.exec(select(CaseEvent.id).where(CaseEvent.case_id == prev_case_id)).first() is not None
                has_dl = session.exec(select(Deadline.id).where(Deadline.case_id == prev_case_id)).first() is not None
                has_sc = session.exec(select(ScheduleItem.id).where(ScheduleItem.case_id == prev_case_id)).first() is not None
                has_doc = session.exec(select(Document.id).where(Document.case_id == prev_case_id)).first() is not None
                has_other_triage = session.exec(
                    select(InboxTriageItem.id).where(
                        InboxTriageItem.matched_case_id == prev_case_id,
                        InboxTriageItem.id != item.id,
                    )
                ).first() is not None
                if not (has_ev or has_dl or has_sc or has_doc or has_other_triage):
                    item.matched_case_id = None
                    session.add(item)
                    for m in session.exec(select(CaseMember).where(CaseMember.case_id == prev_case_id)).all():
                        session.delete(m)
                    for log in session.exec(select(AuditLog).where(AuditLog.case_id == prev_case_id)).all():
                        log.case_id = None
                        session.add(log)
                    folder_rel = prev_case.folder_rel_path
                    session.delete(prev_case)
                    session.flush()
                    file_bridge.remove_empty_case_directory(folder_rel)
                    deleted_empty_auto_case = True

    record_audit_log(
        session,
        actor_user_id=actor_user.id,
        action="inbox.reopen",
        entity_type="inbox_triage_item",
        entity_id=item.id,
        case_id=None if deleted_empty_auto_case else prev_case_id,
        detail={
            "restored_files": restored_files,
            "deleted_empty_auto_case": deleted_empty_auto_case,
        },
    )
    return item


def mark_triage_ignored(
    session: Session,
    *,
    item: InboxTriageItem,
    actor_user: User,
    source: str = "inbox",
) -> InboxTriageItem:
    """Atomically ignore a non-confirmed triage item and write an audit entry."""
    assert actor_user.id is not None
    if item.item_type == "invoice_archive":
        for rel in list(item.downloaded_files or []):
            doc = session.exec(
                select(Document).where(
                    Document.rel_file_path == rel,
                    Document.case_id == None,  # noqa: E711
                )
            ).first()
            if doc is not None:
                session.delete(doc)
    item.status = TriageStatus.IGNORED
    session.add(item)
    record_audit_log(
        session,
        actor_user_id=actor_user.id,
        action="inbox.ignore",
        entity_type="inbox_triage_item",
        entity_id=item.id,
        case_id=item.matched_case_id,
        detail={"item_type": item.item_type, "source": source},
    )
    return item
