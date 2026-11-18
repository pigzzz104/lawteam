"""End-to-end SMS ingestion -> parsing -> document fetching -> InboxTriageItem staging pipeline."""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlmodel import Session, select

from app.database import engine
from app.models.entities import (
    Document,
    InboxTriageItem,
    SmsInbound,
    TriageStatus,
    User,
)
from app.services.court_fetcher import fetch_court_documents
from app.services.invoice_fetcher import fetch_and_stage_invoice
from app.services.sms_normalizer import NormalizedSmsPayload
from app.services.sms_parser import (
    match_case_for_user,
    parse_sms_regex,
    parse_sms_with_optional_llm,
)

logger = logging.getLogger(__name__)


def ingest_sms_and_create_triage(
    session: Session,
    user: User,
    normalized: NormalizedSmsPayload,
    *,
    dedup_override: Optional[str] = None,
) -> tuple[SmsInbound, InboxTriageItem, bool]:
    """Persist ``SmsInbound`` and create a ``PENDING`` ``InboxTriageItem`` (idempotent on ``dedup_key``).

    Never mutates ``Case``, ``CaseEvent``, ``Deadline``, or ``ScheduleItem`` directly (Iron Rule #3).
    """
    assert user.id is not None
    eff_dedup_key = dedup_override or normalized.dedup_key

    existing_sms = session.exec(
        select(SmsInbound).where(SmsInbound.dedup_key == eff_dedup_key)
    ).first()
    if existing_sms is not None:
        existing_triage = session.exec(
            select(InboxTriageItem)
            .where(InboxTriageItem.sms_id == existing_sms.id)
            .order_by(InboxTriageItem.id.desc())
        ).first()
        if existing_triage is None:
            existing_triage = InboxTriageItem(
                sms_id=existing_sms.id,
                owner_user_id=user.id,
                item_type="invoice_archive" if existing_sms.sms_category == "invoice" else "judicial_stage_and_doc",
                status=TriageStatus.PENDING,
                extracted_payload={},
            )
            session.add(existing_triage)
            session.commit()
            session.refresh(existing_triage)
        return existing_sms, existing_triage, True

    parsed = parse_sms_regex(normalized.content, received_at=normalized.received_at)
    matched_case = match_case_for_user(
        session, user, parsed, extra_text=normalized.content
    )

    sms_row = SmsInbound(
        receiver_user_id=user.id,
        client_platform=normalized.client_platform,
        sender_number=normalized.sender_number,
        content=normalized.content,
        content_hash=normalized.content_hash,
        dedup_key=eff_dedup_key,
        sms_category=parsed["sms_category"],
        received_at=normalized.received_at,
    )
    session.add(sms_row)
    session.flush()

    triage = InboxTriageItem(
        sms_id=sms_row.id,
        owner_user_id=user.id,
        matched_case_id=matched_case.id if matched_case else None,
        item_type=parsed["item_type"],
        status=TriageStatus.PENDING,
        extracted_payload=parsed,
        downloaded_files=[],
    )
    session.add(triage)
    session.commit()
    session.refresh(sms_row)
    session.refresh(triage)
    return sms_row, triage, False


def _merge_court_pdf_metadata(
    payload: dict[str, Any],
    parsed_docs: list[dict[str, Any]],
    court_name: Optional[str],
    sent_at: Optional[str],
) -> dict[str, Any]:
    updated = dict(payload)
    updated["parsed_docs"] = parsed_docs
    if court_name and not updated.get("agency"):
        updated["agency"] = court_name
    if sent_at:
        updated["platform_sent_at"] = sent_at

    for doc in parsed_docs:
        if not updated.get("case_number") and doc.get("case_no"):
            updated["case_number"] = doc["case_no"]
        if not updated.get("hearing_time") and doc.get("hearing_time"):
            updated["hearing_time"] = doc["hearing_time"]
        if not updated.get("hearing_location") and doc.get("hearing_location"):
            updated["hearing_location"] = doc["hearing_location"]
        if doc.get("authority_specified_days") and not updated.get("authority_specified_days"):
            updated["authority_specified_days"] = doc["authority_specified_days"]
        if doc.get("doc_event_type") and updated.get("event_type_guess") in (None, "document_served"):
            updated["event_type_guess"] = doc["doc_event_type"]
        if doc.get("document_type") and not updated.get("doc_title_hint"):
            updated["doc_title_hint"] = doc["document_type"]
    return updated


async def enrich_and_fetch_triage_item(
    triage_id: int,
    *,
    phone_last4_override: Optional[str] = None,
    id_card_last4_override: Optional[str] = None,
    confirm_sign: bool = False,
    run_fetch: bool = True,
) -> None:
    """Enrich an ``InboxTriageItem`` via optional LLM fallback and 3-tier court/invoice fetchers."""
    with Session(engine) as session:
        triage = session.get(InboxTriageItem, triage_id)
        if triage is None or triage.status in (TriageStatus.CONFIRMED, TriageStatus.IGNORED):
            return
        owner = session.get(User, triage.owner_user_id)
        sms_row = session.get(SmsInbound, triage.sms_id) if triage.sms_id else None
        if owner is None:
            return

        payload = dict(triage.extracted_payload or {})
        sms_content = sms_row.content if sms_row else ""

        # Optional LLM fallback when regex missed key factual fields
        if sms_content and not payload.get("llm_assisted"):
            payload = await parse_sms_with_optional_llm(
                sms_content,
                received_at=sms_row.received_at if sms_row else None,
            )

        if triage.item_type == "invoice_archive" and run_fetch and payload.get("invoice_links"):
            inv_res = await fetch_and_stage_invoice(payload)
            if inv_res.status == "fetched" and inv_res.rel_file_path:
                files = list(triage.downloaded_files or [])
                if inv_res.rel_file_path not in files:
                    files.append(inv_res.rel_file_path)
                triage.downloaded_files = files
                payload["invoice_meta"] = inv_res.invoice_meta

                existing_doc = session.exec(
                    select(Document).where(Document.rel_file_path == inv_res.rel_file_path)
                ).first()
                if existing_doc is None:
                    doc = Document(
                        case_id=None,
                        folder_category="_待认领发票",
                        file_name=inv_res.file_name or "电子发票.pdf",
                        rel_file_path=inv_res.rel_file_path,
                        doc_subtype="电子发票",
                        invoice_meta=inv_res.invoice_meta,
                        uploaded_by=owner.id,  # type: ignore[arg-type]
                    )
                    session.add(doc)
                else:
                    existing_doc.invoice_meta = inv_res.invoice_meta
                    session.add(existing_doc)

                extra = " ".join(
                    str(v)
                    for v in (
                        inv_res.invoice_meta.get("remark"),
                        inv_res.invoice_meta.get("buyer_name"),
                    )
                    if v
                )
                matched = match_case_for_user(session, owner, payload, extra_text=f"{sms_content} {extra}")
                if matched:
                    triage.matched_case_id = matched.id
                triage.status = TriageStatus.PENDING
                triage.error_or_manual_reason = None
            elif inv_res.status == "manual_required":
                triage.status = TriageStatus.MANUAL_FETCH_REQUIRED
                triage.error_or_manual_reason = inv_res.manual_reason

        elif run_fetch and payload.get("court_links"):
            phone_tail = (phone_last4_override or owner.phone_last4 or "").strip() or None
            id_tail = (id_card_last4_override or owner.id_card_last4 or "").strip() or None
            batch_key = f"triage_{triage.id}"
            court_res = await fetch_court_documents(
                payload,
                batch_key=batch_key,
                phone_last4=phone_tail,
                id_card_last4=id_tail,
                confirm_sign=confirm_sign,
            )
            payload["fetch_tier"] = court_res.tier_used
            payload["requires_phone_last4"] = court_res.requires_phone_last4
            if phone_tail:
                payload["phone_last4_used"] = phone_tail

            if court_res.status == "fetched":
                existing_files = list(triage.downloaded_files or [])
                for rel in court_res.downloaded_files:
                    if rel not in existing_files:
                        existing_files.append(rel)
                triage.downloaded_files = existing_files
                payload = _merge_court_pdf_metadata(
                    payload,
                    court_res.parsed_docs,
                    court_res.court_name,
                    court_res.sent_at,
                )
                triage.status = TriageStatus.PENDING
                triage.error_or_manual_reason = None
            elif court_res.status == "manual_required":
                triage.status = TriageStatus.MANUAL_FETCH_REQUIRED
                triage.error_or_manual_reason = court_res.manual_reason

        if triage.matched_case_id is None:
            matched = match_case_for_user(session, owner, payload, extra_text=sms_content)
            if matched:
                triage.matched_case_id = matched.id

        triage.extracted_payload = payload
        session.add(triage)
        session.commit()
        session.refresh(triage)

        if triage.status == TriageStatus.MANUAL_FETCH_REQUIRED:
            from app.services.reminder_scheduler import push_manual_fetch_alert

            push_manual_fetch_alert(session, triage)
