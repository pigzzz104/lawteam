"""Shared atomic triage archiving, confirmation, and undo/reopen service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.core.security import record_audit_log
from app.models.entities import (
    Case,
    CaseEvent,
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
from app.services.sms_parser import suggest_stage_for_event

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


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
    if hearing_start is not None and hearing_end is not None:
        st_utc = _to_utc(hearing_start)
        en_utc = _to_utc(hearing_end)
        if en_utc <= st_utc:
            raise ValueError("hearing_end must be after hearing_start")
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

    resolved_stage = new_stage.strip() if new_stage and new_stage.strip() else None
    if not resolved_stage and auto_advance_stage and clean_event_type:
        resolved_stage = suggest_stage_for_event(
            target_case.case_category.value,
            clean_event_type,
            procedure_subtype=target_case.procedure_subtype,
            current_stage=target_case.current_stage,
        )
    if resolved_stage and resolved_stage != target_case.current_stage:
        target_case.current_stage = resolved_stage
        target_case.stage_entered_at = now
    target_case.updated_at = now
    session.add(target_case)

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

    record_audit_log(
        session,
        actor_user_id=actor_user.id,
        action="inbox.reopen",
        entity_type="inbox_triage_item",
        entity_id=item.id,
        case_id=prev_case_id,
        detail={"restored_files": restored_files},
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
