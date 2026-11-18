"""Inbox triage queue, manual upload, retry-fetch, simulation, and atomic confirmation API."""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from app.core.security import (
    check_case_access,
    get_accessible_case_ids_filter,
    get_current_user,
    record_audit_log,
)
from app.core.seed import resolve_default_subtype_and_stage
from app.database import get_session
from app.models.entities import (
    Case,
    CaseCategory,
    CaseEvent,
    CaseMember,
    CaseMemberRole,
    CaseVisibility,
    Document,
    InboxTriageItem,
    RiskLevel,
    ScheduleItem,
    SmsInbound,
    SystemRole,
    TriageStatus,
    User,
    utcnow,
)
from app.services import file_bridge
from app.services.court_fetcher import parse_court_pdf_bytes
from app.services.deadline_engine import derive_deadlines_for_event
from app.services.invoice_fetcher import format_invoice_filename, parse_invoice_pdf_bytes
from app.services.sms_normalizer import compute_sms_hashes, NormalizedSmsPayload
from app.services.sms_parser import match_case_for_user
from app.services.triage_pipeline import (
    enrich_and_fetch_triage_item,
    ingest_sms_and_create_triage,
)

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


# --- Schemas ---
class InboxTriageRead(BaseModel):
    id: int
    sms_id: Optional[int]
    owner_user_id: int
    owner_name: str
    matched_case_id: Optional[int]
    matched_case_title: Optional[str] = None
    matched_case_number: Optional[str] = None
    item_type: str
    status: TriageStatus
    extracted_payload: dict[str, Any]
    downloaded_files: list[str]
    error_or_manual_reason: Optional[str]
    sms_content: Optional[str] = None
    sms_sender: Optional[str] = None
    sms_platform: Optional[str] = None
    sms_category: Optional[str] = None
    confirmed_by: Optional[int] = None
    confirmed_at: Optional[datetime] = None
    created_at: datetime


class InboxSimulateRequest(BaseModel):
    content: str
    sender: str = "12368"
    client_platform: str = "ios_shortcuts"
    auto_fetch: bool = True
    phone_last4: Optional[str] = None
    confirm_sign: bool = False
    bypass_dedup: bool = True


class RetryFetchRequest(BaseModel):
    phone_last4: Optional[str] = None
    id_card_last4: Optional[str] = None
    extraction_code: Optional[str] = None
    confirm_sign: bool = True


class ConfirmCreateCasePayload(BaseModel):
    case_title: str
    case_number: Optional[str] = None
    case_category: CaseCategory = CaseCategory.CIVIL
    procedure_subtype: Optional[str] = None
    current_stage: Optional[str] = None
    visibility: CaseVisibility = CaseVisibility.TEAM
    client_name: str = "待补充委托人"
    handling_agency: Optional[str] = None


class ConfirmCreateHearingPayload(BaseModel):
    category: str = "hearing"
    title: str
    start_time: datetime
    end_time: datetime
    location: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.HIGH


class InboxConfirmRequest(BaseModel):
    case_id: Optional[int] = None
    create_case: Optional[ConfirmCreateCasePayload] = None
    event_type: Optional[str] = None
    event_date: Optional[date] = None
    service_method: str = "electronic"
    authority_specified_days: Optional[int] = None
    event_notes: Optional[str] = None
    new_stage: Optional[str] = None
    create_hearing: Optional[ConfirmCreateHearingPayload] = None
    file_subfolder: Optional[str] = None


class InboxConfirmResponse(BaseModel):
    triage_item: InboxTriageRead
    case_id: int
    created_case: bool
    created_event_id: Optional[int] = None
    generated_deadline_count: int = 0
    created_schedule_id: Optional[int] = None
    archived_documents: list[str] = PydanticField(default_factory=list)


# --- Helpers ---
def _serialize_triage_items(
    session: Session, items: list[InboxTriageItem]
) -> list[InboxTriageRead]:
    if not items:
        return []
    user_ids = {i.owner_user_id for i in items}
    case_ids = {i.matched_case_id for i in items if i.matched_case_id is not None}
    sms_ids = {i.sms_id for i in items if i.sms_id is not None}

    users_map = {
        u.id: u
        for u in session.exec(select(User).where(User.id.in_(user_ids))).all()
    }
    cases_map = (
        {
            c.id: c
            for c in session.exec(select(Case).where(Case.id.in_(case_ids))).all()
        }
        if case_ids
        else {}
    )
    sms_map = (
        {
            s.id: s
            for s in session.exec(select(SmsInbound).where(SmsInbound.id.in_(sms_ids))).all()
        }
        if sms_ids
        else {}
    )

    out: list[InboxTriageRead] = []
    for item in items:
        owner = users_map.get(item.owner_user_id)
        case = cases_map.get(item.matched_case_id) if item.matched_case_id else None
        sms = sms_map.get(item.sms_id) if item.sms_id else None
        out.append(
            InboxTriageRead(
                id=item.id,  # type: ignore[arg-type]
                sms_id=item.sms_id,
                owner_user_id=item.owner_user_id,
                owner_name=owner.full_name if owner else "未知用户",
                matched_case_id=item.matched_case_id,
                matched_case_title=case.case_title if case else None,
                matched_case_number=case.case_number if case else None,
                item_type=item.item_type,
                status=item.status,
                extracted_payload=item.extracted_payload or {},
                downloaded_files=list(item.downloaded_files or []),
                error_or_manual_reason=item.error_or_manual_reason,
                sms_content=sms.content if sms else None,
                sms_sender=sms.sender_number if sms else None,
                sms_platform=sms.client_platform if sms else None,
                sms_category=sms.sms_category if sms else None,
                confirmed_by=item.confirmed_by,
                confirmed_at=item.confirmed_at,
                created_at=item.created_at,
            )
        )
    return out


def _get_accessible_triage_or_404(
    session: Session, user: User, triage_id: int
) -> InboxTriageItem:
    item = session.get(InboxTriageItem, triage_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Triage item not found")
    if user.system_role == SystemRole.ADMIN or item.owner_user_id == user.id:
        return item
    if item.matched_case_id is not None:
        accessible_ids = get_accessible_case_ids_filter(user, session)
        if item.matched_case_id in accessible_ids:
            return item
    if item.item_type == "invoice_archive" and item.matched_case_id is None and user.system_role == SystemRole.ASSISTANT:
        return item
    raise HTTPException(status_code=403, detail="Access denied to this triage item")


# --- Endpoints ---
@router.get("", response_model=list[InboxTriageRead])
def list_inbox_items(
    status_filter: Optional[TriageStatus] = Query(default=None, alias="status"),
    item_type: Optional[str] = Query(default=None),
    matched_case_id: Optional[int] = Query(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[InboxTriageRead]:
    stmt = select(InboxTriageItem).order_by(InboxTriageItem.created_at.desc(), InboxTriageItem.id.desc())
    if status_filter is not None:
        stmt = stmt.where(InboxTriageItem.status == status_filter)
    if item_type:
        stmt = stmt.where(InboxTriageItem.item_type == item_type)
    if matched_case_id is not None:
        stmt = stmt.where(InboxTriageItem.matched_case_id == matched_case_id)

    all_items = session.exec(stmt).all()
    if current_user.system_role == SystemRole.ADMIN:
        return _serialize_triage_items(session, list(all_items))

    accessible_ids = get_accessible_case_ids_filter(current_user, session)
    visible: list[InboxTriageItem] = []
    for it in all_items:
        if it.owner_user_id == current_user.id:
            visible.append(it)
        elif it.matched_case_id is not None and it.matched_case_id in accessible_ids:
            visible.append(it)
        elif (
            it.item_type == "invoice_archive"
            and it.matched_case_id is None
            and current_user.system_role == SystemRole.ASSISTANT
        ):
            visible.append(it)
    return _serialize_triage_items(session, visible)


@router.post("/simulate", response_model=InboxTriageRead, status_code=status.HTTP_201_CREATED)
async def simulate_sms_ingest(
    payload: InboxSimulateRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxTriageRead:
    """Dev/workbench helper simulating multi-domain SMS arrival and parsing/fetching."""
    assert current_user.id is not None
    now = datetime.now(timezone.utc)
    shanghai_date, content_hash, dedup_key = compute_sms_hashes(
        current_user.id, payload.content, now
    )
    normalized = NormalizedSmsPayload(
        client_platform=payload.client_platform,
        sender_number=payload.sender,
        content=payload.content.strip(),
        received_at=now,
        shanghai_date=shanghai_date,
        content_hash=content_hash,
        dedup_key=dedup_key,
    )
    dedup_override = f"sim_{secrets.token_hex(12)}" if payload.bypass_dedup else None
    _, triage, _ = ingest_sms_and_create_triage(
        session, current_user, normalized, dedup_override=dedup_override
    )
    assert triage.id is not None
    await enrich_and_fetch_triage_item(
        triage.id,
        phone_last4_override=payload.phone_last4,
        confirm_sign=payload.confirm_sign,
        run_fetch=payload.auto_fetch,
    )
    session.expire_all()
    refreshed = session.get(InboxTriageItem, triage.id)
    assert refreshed is not None
    return _serialize_triage_items(session, [refreshed])[0]


@router.post("/manual-upload", response_model=InboxTriageRead, status_code=status.HTTP_201_CREATED)
async def manual_upload_triage_files(
    files: list[UploadFile] = File(...),
    item_type: str = Form(default="judicial_stage_and_doc"),
    case_id: Optional[int] = Form(default=None),
    notes: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxTriageRead:
    """Manually upload downloaded court PDFs or digital invoices into the triage queue."""
    assert current_user.id is not None
    if item_type not in ("judicial_stage_and_doc", "invoice_archive"):
        raise HTTPException(status_code=400, detail="Unsupported item_type")
    if case_id is not None:
        check_case_access(session.get(Case, case_id), current_user, session)

    batch_key = f"manual_{secrets.token_hex(6)}"
    staged_rels: list[str] = []
    parsed_docs: list[dict[str, Any]] = []
    invoice_meta: Optional[dict[str, Any]] = None
    pending_docs_to_insert: list[Document] = []

    for upload in files:
        raw_bytes = await upload.read()
        orig_name = upload.filename or "uploaded_file.pdf"
        suffix = Path(orig_name).suffix.lower() or ".pdf"

        if item_type == "invoice_archive":
            meta = parse_invoice_pdf_bytes(raw_bytes, fallback_name=orig_name) if suffix == ".pdf" else {}
            fmt_name = format_invoice_filename(meta, fallback_name=orig_name)
            _, rel_path = file_bridge.save_unclaimed_invoice(fmt_name, raw_bytes)
            staged_rels.append(rel_path)
            invoice_meta = meta
            pending_docs_to_insert.append(
                Document(
                    case_id=None,
                    folder_category=file_bridge.UNCLAIMED_INVOICES_DIR,
                    file_name=Path(rel_path).name,
                    rel_file_path=rel_path,
                    doc_subtype="电子发票",
                    invoice_meta=meta,
                    uploaded_by=current_user.id,
                )
            )
        else:
            _, rel_path = file_bridge.save_staged_triage_file(batch_key, orig_name, raw_bytes)
            staged_rels.append(rel_path)
            if suffix == ".pdf":
                parsed_docs.append(parse_court_pdf_bytes(raw_bytes, orig_name))

    payload: dict[str, Any] = {
        "manual_upload": True,
        "notes": notes,
        "parsed_docs": parsed_docs,
    }
    if invoice_meta:
        payload["invoice_meta"] = invoice_meta
    if parsed_docs:
        first_doc = parsed_docs[0]
        for k in ("case_number", "hearing_time", "hearing_location", "authority_specified_days"):
            if first_doc.get(k) or first_doc.get("case_no"):
                payload[k] = first_doc.get(k) or first_doc.get("case_no")
        if first_doc.get("doc_event_type"):
            payload["event_type_guess"] = first_doc["doc_event_type"]
        if first_doc.get("document_type"):
            payload["doc_title_hint"] = first_doc["document_type"]

    resolved_case_id = case_id
    if resolved_case_id is None:
        extra = " ".join(
            str(v)
            for v in (
                notes,
                (invoice_meta or {}).get("remark"),
                (invoice_meta or {}).get("buyer_name"),
            )
            if v
        )
        matched = match_case_for_user(session, current_user, payload, extra_text=extra)
        if matched:
            resolved_case_id = matched.id

    triage = InboxTriageItem(
        sms_id=None,
        owner_user_id=current_user.id,
        matched_case_id=resolved_case_id,
        item_type=item_type,
        status=TriageStatus.PENDING,
        extracted_payload=payload,
        downloaded_files=staged_rels,
    )
    session.add(triage)
    for d in pending_docs_to_insert:
        session.add(d)
    session.commit()
    session.refresh(triage)
    return _serialize_triage_items(session, [triage])[0]


@router.post("/{triage_id}/retry-fetch", response_model=InboxTriageRead)
async def retry_fetch_triage_item(
    triage_id: int,
    payload: RetryFetchRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxTriageRead:
    item = _get_accessible_triage_or_404(session, current_user, triage_id)
    if item.status in (TriageStatus.CONFIRMED, TriageStatus.IGNORED):
        raise HTTPException(status_code=409, detail="Triage item already finalized")

    if payload.extraction_code:
        updated_payload = dict(item.extracted_payload or {})
        updated_payload["extraction_code"] = payload.extraction_code.strip()
        item.extracted_payload = updated_payload
        session.add(item)
        session.commit()

    await enrich_and_fetch_triage_item(
        triage_id,
        phone_last4_override=payload.phone_last4,
        id_card_last4_override=payload.id_card_last4,
        confirm_sign=payload.confirm_sign,
        run_fetch=True,
    )
    session.expire_all()
    refreshed = session.get(InboxTriageItem, triage_id)
    assert refreshed is not None
    return _serialize_triage_items(session, [refreshed])[0]


@router.post("/{triage_id}/confirm", response_model=InboxConfirmResponse)
def confirm_triage_item(
    triage_id: int,
    payload: InboxConfirmRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxConfirmResponse:
    """Atomically confirm a triage item: create/link Case, move files, insert Event + Deadlines + Schedule."""
    assert current_user.id is not None
    item = _get_accessible_triage_or_404(session, current_user, triage_id)
    if item.status == TriageStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Triage item already confirmed")

    now = utcnow()
    created_case = False

    try:
        if payload.case_id is not None:
            target_case = check_case_access(session.get(Case, payload.case_id), current_user, session)
        elif payload.create_case is not None:
            cc = payload.create_case
            subtype, stage = resolve_default_subtype_and_stage(
                cc.case_category, cc.procedure_subtype, cc.current_stage
            )
            folder_rel_path = file_bridge.build_case_folder_rel_path(
                case_number=cc.case_number,
                client_name=cc.client_name,
                case_title=cc.case_title,
            )
            file_bridge.ensure_case_directories(folder_rel_path)
            target_case = Case(
                case_title=cc.case_title.strip(),
                case_number=cc.case_number.strip() if cc.case_number else None,
                case_category=cc.case_category,
                procedure_subtype=subtype,
                current_stage=stage,
                stage_entered_at=now,
                visibility=cc.visibility,
                owner_id=current_user.id,
                client_name=cc.client_name.strip(),
                handling_agency=cc.handling_agency,
                folder_rel_path=folder_rel_path,
                created_at=now,
                updated_at=now,
            )
            session.add(target_case)
            session.flush()
            session.add(
                CaseMember(
                    case_id=target_case.id,  # type: ignore[arg-type]
                    user_id=current_user.id,
                    role_in_case=CaseMemberRole.LEAD,
                )
            )
            created_case = True
        else:
            raise HTTPException(
                status_code=400,
                detail="Must specify either case_id or create_case when confirming triage item",
            )

        assert target_case.id is not None
        default_subfolder = (
            "05_财务与发票" if item.item_type == "invoice_archive" else "01_司法与程序文书"
        )
        target_subfolder = payload.file_subfolder or default_subfolder
        if target_subfolder not in file_bridge.STANDARD_CASE_SUBFOLDERS:
            raise HTTPException(status_code=400, detail=f"Invalid file_subfolder: {target_subfolder}")

        archived_paths: list[str] = []
        for src_rel in list(item.downloaded_files or []):
            src_path = Path(src_rel)
            desired_name = src_path.name
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
                extracted = item.extracted_payload or {}
                session.add(
                    Document(
                        case_id=target_case.id,
                        folder_category=target_subfolder,
                        file_name=Path(new_rel).name,
                        rel_file_path=new_rel,
                        doc_subtype=extracted.get("doc_title_hint"),
                        invoice_meta=extracted.get("invoice_meta"),
                        uploaded_by=current_user.id,
                    )
                )

        created_event_id: Optional[int] = None
        deadline_count = 0
        if payload.event_type and payload.event_date:
            ev = CaseEvent(
                case_id=target_case.id,
                event_type=payload.event_type.strip(),
                event_date=payload.event_date,
                service_method=payload.service_method,
                authority_specified_days=payload.authority_specified_days,
                source_sms_id=item.sms_id,
                notes=payload.event_notes,
                created_by=current_user.id,
            )
            session.add(ev)
            session.flush()
            created_event_id = ev.id
            deadlines = derive_deadlines_for_event(session, target_case, ev)
            deadline_count = len(deadlines)

        created_schedule_id: Optional[int] = None
        if payload.create_hearing is not None:
            ch = payload.create_hearing
            sh_tz = ZoneInfo("Asia/Shanghai")
            st_utc = (
                ch.start_time.replace(tzinfo=sh_tz)
                if ch.start_time.tzinfo is None
                else ch.start_time
            ).astimezone(timezone.utc)
            en_utc = (
                ch.end_time.replace(tzinfo=sh_tz)
                if ch.end_time.tzinfo is None
                else ch.end_time
            ).astimezone(timezone.utc)
            if en_utc <= st_utc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="create_hearing.end_time must be after start_time",
                )
            sched = ScheduleItem(
                case_id=target_case.id,
                owner_id=current_user.id,
                category=ch.category,
                title=ch.title.strip(),
                start_time=st_utc,
                end_time=en_utc,
                location=ch.location,
                risk_level=ch.risk_level,
            )
            session.add(sched)
            session.flush()
            created_schedule_id = sched.id

        if payload.new_stage and payload.new_stage.strip() and payload.new_stage.strip() != target_case.current_stage:
            target_case.current_stage = payload.new_stage.strip()
            target_case.stage_entered_at = now
        target_case.updated_at = now
        session.add(target_case)

        item.status = TriageStatus.CONFIRMED
        item.matched_case_id = target_case.id
        item.confirmed_by = current_user.id
        item.confirmed_at = now
        item.error_or_manual_reason = None
        if archived_paths:
            item.downloaded_files = archived_paths
        session.add(item)

        record_audit_log(
            session,
            actor_user_id=current_user.id,
            action="inbox.confirm",
            entity_type="inbox_triage_item",
            entity_id=item.id,
            case_id=target_case.id,
            detail={
                "created_case": created_case,
                "created_event_id": created_event_id,
                "generated_deadline_count": deadline_count,
                "created_schedule_id": created_schedule_id,
                "archived_documents": archived_paths,
                "new_stage": target_case.current_stage,
            },
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Inbox confirmation failed: {exc}") from exc

    session.refresh(item)
    return InboxConfirmResponse(
        triage_item=_serialize_triage_items(session, [item])[0],
        case_id=target_case.id,  # type: ignore[arg-type]
        created_case=created_case,
        created_event_id=created_event_id,
        generated_deadline_count=deadline_count,
        created_schedule_id=created_schedule_id,
        archived_documents=archived_paths,
    )


@router.post("/{triage_id}/ignore", response_model=InboxTriageRead)
def ignore_triage_item(
    triage_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxTriageRead:
    assert current_user.id is not None
    item = _get_accessible_triage_or_404(session, current_user, triage_id)
    if item.status == TriageStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Cannot ignore an already confirmed triage item")

    item.status = TriageStatus.IGNORED
    session.add(item)
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        action="inbox.ignore",
        entity_type="inbox_triage_item",
        entity_id=item.id,
        case_id=item.matched_case_id,
        detail={"item_type": item.item_type},
    )
    session.commit()
    session.refresh(item)
    return _serialize_triage_items(session, [item])[0]
