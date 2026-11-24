"""Inbox triage queue, manual upload, retry-fetch, simulation, and atomic confirmation API."""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session, select

from app.core.security import (
    check_case_access,
    check_triage_access,
    filter_visible_triage_items,
    get_current_user,
    record_audit_log,
)
from app.core.seed import resolve_default_subtype_and_stage
from app.database import get_session
from app.models.entities import (
    Case,
    CaseCategory,
    CaseMember,
    CaseMemberRole,
    CaseVisibility,
    Document,
    InboxTriageItem,
    RiskLevel,
    SmsInbound,
    TriageStatus,
    User,
    utcnow,
)
from app.services import file_bridge
from app.services.court_fetcher import parse_court_pdf_bytes_with_ocr
from app.services.invoice_fetcher import format_invoice_filename, parse_invoice_bytes_with_ocr
from app.services.sms_normalizer import compute_sms_hashes, NormalizedSmsPayload
from app.services.sms_parser import CaseMatchResult, match_case_with_confidence
from app.services.triage_archiver import (
    apply_triage_to_case,
    mark_triage_ignored,
    reopen_triage_item,
)
from app.services.triage_pipeline import (
    enrich_and_fetch_triage_item,
    evaluate_and_apply_auto_triage,
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
    auto_archived: bool = False
    triage_reason: Optional[str] = None
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


class BatchIgnoreRequest(BaseModel):
    triage_ids: list[int]


class BatchIgnoreResponse(BaseModel):
    ignored_count: int
    ignored_ids: list[int]


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
    crime_type: Optional[str] = None
    charge_name: Optional[str] = None


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
                auto_archived=bool((item.extracted_payload or {}).get("auto_archived")),
                triage_reason=(item.extracted_payload or {}).get("triage_reason"),
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
    return check_triage_access(session, user, session.get(InboxTriageItem, triage_id))


# --- Endpoints ---
@router.get("", response_model=list[InboxTriageRead])
def list_inbox_items(
    status_filter: Optional[TriageStatus] = Query(default=None, alias="status"),
    item_type: Optional[str] = Query(default=None),
    matched_case_id: Optional[int] = Query(default=None),
    auto_archived: Optional[bool] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
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

    all_items = list(session.exec(stmt).all())
    if auto_archived is not None:
        all_items = [
            it for it in all_items
            if bool((it.extracted_payload or {}).get("auto_archived")) is auto_archived
        ]

    visible = filter_visible_triage_items(session, current_user, all_items)
    paged = visible[offset : offset + limit]
    return _serialize_triage_items(session, paged)


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
        session=session,
    )
    session.refresh(triage)
    return _serialize_triage_items(session, [triage])[0]


@router.post("/manual-upload", response_model=InboxTriageRead, status_code=status.HTTP_201_CREATED)
async def manual_upload_triage_files(
    files: list[UploadFile] = File(...),
    item_type: str = Form(default="judicial_stage_and_doc"),
    case_id: Optional[int] = Form(default=None),
    notes: Optional[str] = Form(default=None),
    auto_archive: bool = Form(default=True),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxTriageRead:
    """Manually upload court PDFs or digital invoices; auto-archive when covered by rules."""
    assert current_user.id is not None
    if item_type not in ("judicial_stage_and_doc", "invoice_archive"):
        raise HTTPException(status_code=400, detail="Unsupported item_type")
    explicit_case: Optional[Case] = None
    if case_id is not None:
        explicit_case = check_case_access(session.get(Case, case_id), current_user, session)

    batch_key = f"manual_{secrets.token_hex(6)}"
    staged_rels: list[str] = []
    parsed_docs: list[dict[str, Any]] = []
    invoice_metas: list[dict[str, Any]] = []
    invoice_meta: Optional[dict[str, Any]] = None
    pending_docs_to_insert: list[Document] = []

    for upload in files:
        raw_bytes = await upload.read()
        orig_name = upload.filename or "uploaded_file.pdf"
        suffix = Path(orig_name).suffix.lower() or ".pdf"

        if item_type == "invoice_archive":
            inv_ext = suffix if suffix in (".pdf", ".ofd", ".xml") else ".pdf"
            meta = await parse_invoice_bytes_with_ocr(raw_bytes, ext=inv_ext, fallback_name=orig_name)
            fmt_name = format_invoice_filename(meta, fallback_name=orig_name, ext=inv_ext)
            _, rel_path = file_bridge.save_unclaimed_invoice(fmt_name, raw_bytes)
            staged_rels.append(rel_path)
            invoice_metas.append(meta)
            if invoice_meta is None:
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
                parsed_docs.append(await parse_court_pdf_bytes_with_ocr(raw_bytes, orig_name))

    payload: dict[str, Any] = {
        "manual_upload": True,
        "notes": notes,
        "parsed_docs": parsed_docs,
    }
    if invoice_metas:
        payload["invoice_metas"] = invoice_metas
    if invoice_meta:
        payload["invoice_meta"] = invoice_meta
    for doc_item in parsed_docs:
        if doc_item.get("case_no") and not payload.get("case_number"):
            payload["case_number"] = doc_item["case_no"]
        for k in ("case_number", "hearing_time", "hearing_location", "authority_specified_days"):
            if doc_item.get(k) and not payload.get(k):
                payload[k] = doc_item[k]
        if doc_item.get("doc_event_type") and not payload.get("event_type_guess"):
            payload["event_type_guess"] = doc_item["doc_event_type"]
        if doc_item.get("document_type") and not payload.get("doc_title_hint"):
            payload["doc_title_hint"] = doc_item["document_type"]

    if explicit_case is not None:
        match_res = CaseMatchResult(case=explicit_case, confidence="exact_case_number")
    else:
        extra_parts = [notes]
        for m in invoice_metas:
            extra_parts.append(m.get("remark"))
            extra_parts.append(m.get("buyer_name"))
        extra = " ".join(str(v) for v in extra_parts if v)
        match_res = match_case_with_confidence(session, current_user, payload, extra_text=extra)

    triage = InboxTriageItem(
        sms_id=None,
        owner_user_id=current_user.id,
        matched_case_id=match_res.case.id if match_res.case else None,
        item_type=item_type,
        status=TriageStatus.PENDING,
        extracted_payload=payload,
        downloaded_files=staged_rels,
    )
    session.add(triage)
    for d in pending_docs_to_insert:
        session.add(d)
    session.flush()

    evaluate_and_apply_auto_triage(
        session,
        owner=current_user,
        triage=triage,
        match_res=match_res,
        allow_auto_archive=auto_archive,
    )
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
        session=session,
    )
    session.refresh(item)
    return _serialize_triage_items(session, [item])[0]


@router.get("/{triage_id}/files/download")
def download_staged_triage_file(
    triage_id: int,
    rel_path: str = Query(..., description="Relative path inside this triage item's downloaded_files"),
    inline: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FileResponse:
    """Preview or download a staged/archived file attached to a triage item."""
    item = _get_accessible_triage_or_404(session, current_user, triage_id)
    normalized_rel = rel_path.lstrip("/")
    if normalized_rel not in list(item.downloaded_files or []):
        raise HTTPException(status_code=403, detail="Requested file does not belong to this triage item")
    try:
        resolved = file_bridge.resolve_safe_path(normalized_rel, must_exist=True)
    except (file_bridge.PathSecurityError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=Path(resolved),
        filename=resolved.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.post("/{triage_id}/confirm", response_model=InboxConfirmResponse)
def confirm_triage_item(
    triage_id: int,
    payload: InboxConfirmRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxConfirmResponse:
    """Atomically confirm a triage item: create/link Case, move files, insert Event + Deadlines + Schedule."""
    assert current_user.id is not None
    locked = session.exec(
        select(InboxTriageItem)
        .where(InboxTriageItem.id == triage_id)
        .with_for_update()
    ).first()
    item = check_triage_access(session, current_user, locked)
    if item.status == TriageStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Triage item already confirmed")

    if payload.file_subfolder and payload.file_subfolder not in file_bridge.STANDARD_CASE_SUBFOLDERS:
        raise HTTPException(status_code=400, detail=f"Invalid file_subfolder: {payload.file_subfolder}")
    ch = payload.create_hearing
    if ch is not None:
        st_chk = ch.start_time.replace(tzinfo=timezone.utc) if ch.start_time.tzinfo is None else ch.start_time
        en_chk = ch.end_time.replace(tzinfo=timezone.utc) if ch.end_time.tzinfo is None else ch.end_time
        if en_chk <= st_chk:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="create_hearing.end_time must be after start_time",
            )

    now = utcnow()
    created_case = False
    new_case_folder_to_create: Optional[str] = None

    try:
        if payload.case_id is not None:
            target_case = check_case_access(session.get(Case, payload.case_id), current_user, session)
        elif payload.create_case is not None:
            cc = payload.create_case
            if (
                cc.visibility == CaseVisibility.PRIVATE
                and item.owner_user_id != current_user.id
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Cannot move another user's triage item into a private case",
                )
            subtype, stage = resolve_default_subtype_and_stage(
                cc.case_category, cc.procedure_subtype, cc.current_stage
            )
            folder_rel_path = file_bridge.build_case_folder_rel_path(
                case_number=cc.case_number,
                client_name=cc.client_name,
                case_title=cc.case_title,
            )
            resolved_crime_type: Optional[str] = None
            if cc.case_category == CaseCategory.CRIMINAL:
                raw_crime_type = (cc.crime_type or "").strip() or "一般犯罪"
                if raw_crime_type not in ("一般犯罪", "流窜/多次/结伙作案"):
                    raise HTTPException(
                        status_code=400,
                        detail="crime_type must be '一般犯罪' or '流窜/多次/结伙作案'",
                    )
                resolved_crime_type = raw_crime_type
            resolved_charge_name = (cc.charge_name or "").strip() or None

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
                crime_type=resolved_crime_type,
                charge_name=resolved_charge_name,
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
            new_case_folder_to_create = folder_rel_path
        else:
            raise HTTPException(
                status_code=400,
                detail="Must specify either case_id or create_case when confirming triage item",
            )

        if ch is not None:
            try:
                res = apply_triage_to_case(
                    session,
                    item=item,
                    actor_user=current_user,
                    target_case=target_case,
                    file_subfolder=payload.file_subfolder,
                    event_type=payload.event_type,
                    event_date=payload.event_date,
                    service_method=payload.service_method,
                    authority_specified_days=payload.authority_specified_days,
                    event_notes=payload.event_notes,
                    new_stage=payload.new_stage,
                    auto_advance_stage=True,
                    hearing_category=ch.category,
                    hearing_title=ch.title,
                    hearing_start=ch.start_time,
                    hearing_end=ch.end_time,
                    hearing_location=ch.location,
                    hearing_risk_level=ch.risk_level,
                    auto_archived=False,
                    created_case=created_case,
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="create_hearing.end_time must be after start_time",
                ) from exc
        else:
            res = apply_triage_to_case(
                session,
                item=item,
                actor_user=current_user,
                target_case=target_case,
                file_subfolder=payload.file_subfolder,
                event_type=payload.event_type,
                event_date=payload.event_date,
                service_method=payload.service_method,
                authority_specified_days=payload.authority_specified_days,
                event_notes=payload.event_notes,
                new_stage=payload.new_stage,
                auto_advance_stage=True,
                auto_archived=False,
                created_case=created_case,
            )
        if new_case_folder_to_create:
            file_bridge.ensure_case_directories(new_case_folder_to_create)
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
        case_id=res.case_id,
        created_case=created_case,
        created_event_id=res.created_event_id,
        generated_deadline_count=res.generated_deadline_count,
        created_schedule_id=res.created_schedule_id,
        archived_documents=res.archived_documents,
    )


@router.post("/{triage_id}/reopen", response_model=InboxTriageRead)
def reopen_triage_endpoint(
    triage_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> InboxTriageRead:
    """Undo an auto-archived or confirmed item back to PENDING so it can be re-assigned."""
    locked = session.exec(
        select(InboxTriageItem)
        .where(InboxTriageItem.id == triage_id)
        .with_for_update()
    ).first()
    item = check_triage_access(session, current_user, locked)
    if item.status == TriageStatus.IGNORED:
        extracted = dict(item.extracted_payload or {})
        extracted["action_epoch"] = int(extracted.get("action_epoch") or 0) + 1
        item.extracted_payload = extracted
        item.status = TriageStatus.PENDING
        item.error_or_manual_reason = None
        session.add(item)
        session.commit()
        session.refresh(item)
        return _serialize_triage_items(session, [item])[0]
    if item.status != TriageStatus.CONFIRMED:
        raise HTTPException(status_code=409, detail="Only confirmed, auto-archived, or ignored items can be reopened")
    if item.matched_case_id is not None:
        check_case_access(
            session.get(Case, item.matched_case_id),
            current_user,
            session,
            require_manage=True,
        )
    reopen_triage_item(session, item=item, actor_user=current_user)
    session.commit()
    session.refresh(item)
    return _serialize_triage_items(session, [item])[0]


def _cleanup_orphan_unclaimed_docs(session: Session, item: InboxTriageItem) -> None:
    if item.item_type != "invoice_archive":
        return
    for rel in list(item.downloaded_files or []):
        doc = session.exec(
            select(Document).where(
                Document.rel_file_path == rel,
                Document.case_id == None,  # noqa: E711
            )
        ).first()
        if doc is not None:
            session.delete(doc)


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

    mark_triage_ignored(session, item=item, actor_user=current_user, source="inbox")
    session.commit()
    session.refresh(item)
    return _serialize_triage_items(session, [item])[0]


@router.post("/batch-ignore", response_model=BatchIgnoreResponse)
def batch_ignore_triage_items(
    payload: BatchIgnoreRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BatchIgnoreResponse:
    """Ignore multiple pending/manual triage items in one action."""
    assert current_user.id is not None
    ignored_ids: list[int] = []
    for tid in payload.triage_ids:
        try:
            item = _get_accessible_triage_or_404(session, current_user, tid)
        except HTTPException:
            continue
        if item.status in (TriageStatus.CONFIRMED, TriageStatus.IGNORED):
            continue
        _cleanup_orphan_unclaimed_docs(session, item)
        item.status = TriageStatus.IGNORED
        session.add(item)
        record_audit_log(
            session,
            actor_user_id=current_user.id,
            action="inbox.ignore",
            entity_type="inbox_triage_item",
            entity_id=item.id,
            case_id=item.matched_case_id,
            detail={"item_type": item.item_type, "batch": True},
        )
        if item.id is not None:
            ignored_ids.append(item.id)
    session.commit()
    return BatchIgnoreResponse(ignored_count=len(ignored_ids), ignored_ids=ignored_ids)
