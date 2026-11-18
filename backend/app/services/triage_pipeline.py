"""End-to-end SMS ingestion -> parsing -> document fetching -> auto-archive / triage pipeline."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.config import get_settings
from app.core.security import record_audit_log
from app.database import engine
from app.models.entities import (
    DeadlineRule,
    Document,
    InboxTriageItem,
    SmsInbound,
    TriageStatus,
    User,
)
from app.services.court_fetcher import fetch_court_documents
from app.services.deadline_engine import rule_matches_case
from app.services.invoice_fetcher import fetch_and_stage_invoice
from app.services.sms_normalizer import NormalizedSmsPayload
from app.services.sms_parser import (
    CaseMatchResult,
    match_case_with_confidence,
    parse_sms_regex,
    parse_sms_with_optional_llm,
)
from app.services.triage_archiver import apply_triage_to_case

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _parse_event_date(raw: Any) -> date:
    if isinstance(raw, str) and len(raw.strip()) >= 10:
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            pass
    return datetime.now(SHANGHAI_TZ).date()


def _parse_hearing_datetime(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        return dt.replace(tzinfo=SHANGHAI_TZ) if dt.tzinfo is None else dt
    except ValueError:
        return None


def _apply_match_metadata(
    payload: dict[str, Any], match_res: CaseMatchResult
) -> dict[str, Any]:
    updated = dict(payload)
    updated["match_confidence"] = match_res.confidence
    if match_res.candidate_case_ids:
        updated["ambiguous_case_ids"] = match_res.candidate_case_ids
    else:
        updated.pop("ambiguous_case_ids", None)
    return updated


def evaluate_and_apply_auto_triage(
    session: Session,
    *,
    owner: User,
    triage: InboxTriageItem,
    match_res: CaseMatchResult,
    allow_auto_archive: bool = True,
) -> InboxTriageItem:
    """Apply default auto-archive / auto-ignore when rules cover the item; otherwise leave PENDING."""
    assert owner.id is not None
    payload = _apply_match_metadata(dict(triage.extracted_payload or {}), match_res)

    if triage.status in (TriageStatus.CONFIRMED, TriageStatus.IGNORED):
        triage.extracted_payload = payload
        return triage

    if triage.status == TriageStatus.MANUAL_FETCH_REQUIRED:
        payload["triage_decision"] = "manual_fetch"
        payload["triage_reason"] = triage.error_or_manual_reason or "需要人工核验或手动取回文书"
        triage.extracted_payload = payload
        return triage

    if not allow_auto_archive or not get_settings().auto_archive_enabled:
        payload["triage_decision"] = "needs_confirm"
        payload["triage_reason"] = "已暂存，等待人工核对后确认入案"
        triage.extracted_payload = payload
        return triage

    # 1. Auto-ignore obvious non-business noise SMS
    if (
        triage.item_type != "invoice_archive"
        and payload.get("sms_category") == "other"
        and not payload.get("court_links")
        and not payload.get("invoice_links")
        and not payload.get("case_number")
        and not payload.get("event_type_guess")
        and not payload.get("hearing_time")
        and not triage.downloaded_files
    ):
        payload["auto_ignored"] = True
        payload["triage_decision"] = "auto_ignored"
        payload["triage_reason"] = "非涉案/非程序通知短信，已自动忽略降噪"
        triage.status = TriageStatus.IGNORED
        triage.extracted_payload = payload
        record_audit_log(
            session,
            actor_user_id=owner.id,
            action="inbox.auto_ignore",
            entity_type="inbox_triage_item",
            entity_id=triage.id,
            case_id=None,
            detail={"sms_category": payload.get("sms_category")},
        )
        return triage

    # 2. Electronic invoice default auto-archive when unambiguously matched to a case
    if triage.item_type == "invoice_archive":
        if not triage.downloaded_files:
            payload["triage_decision"] = "needs_confirm"
            payload["triage_reason"] = "未获取到电子发票 PDF，请手动上传或补充信息"
            triage.extracted_payload = payload
            return triage

        if match_res.confidence in ("exact_case_number", "unique_client") and match_res.case is not None:
            triage.extracted_payload = payload
            apply_triage_to_case(
                session,
                item=triage,
                actor_user=owner,
                target_case=match_res.case,
                file_subfolder="05_财务与发票",
                auto_archived=True,
                triage_reason=f"电子发票已自动匹配案件「{match_res.case.case_title}」并归档至「05_财务与发票」",
            )
            return triage

        payload["triage_decision"] = "needs_confirm"
        payload["triage_reason"] = match_res.reason or "未匹配到唯一案件，请人工选择归属案件"
        triage.extracted_payload = payload
        return triage

    # 3. Judicial SMS / Court Docs: require exact case number + covering rule
    if match_res.confidence != "exact_case_number" or match_res.case is None:
        payload["triage_decision"] = "needs_confirm"
        payload["triage_reason"] = (
            match_res.reason
            if match_res.confidence == "ambiguous"
            else "未精确匹配到在办案号，请人工选择或新建案件"
        )
        triage.extracted_payload = payload
        return triage

    target_case = match_res.case
    ev_type = payload.get("event_type_guess")
    hearing_dt = _parse_hearing_datetime(payload.get("hearing_time"))
    parsed_docs = payload.get("parsed_docs") or []

    # 3a. Hearing notice with parsed date/time -> auto-archive + write ScheduleItem
    if ev_type == "hearing_scheduled" and hearing_dt is not None:
        triage.extracted_payload = payload
        ev_date = _parse_event_date(payload.get("event_date")) if payload.get("event_date") else hearing_dt.date()
        doc_hint = payload.get("doc_title_hint") or "开庭通知"
        apply_triage_to_case(
            session,
            item=triage,
            actor_user=owner,
            target_case=target_case,
            file_subfolder="01_司法与程序文书",
            event_type="hearing_scheduled",
            event_date=ev_date,
            event_notes=f"自动归档：{doc_hint}",
            hearing_category="hearing",
            hearing_title=f"{target_case.case_number or target_case.case_title} 开庭审理",
            hearing_start=hearing_dt,
            hearing_end=hearing_dt + timedelta(minutes=90),
            hearing_location=payload.get("hearing_location"),
            auto_archived=True,
            triage_reason="已精确匹配案号并识别开庭时间，自动归档文书并写入开庭日程",
        )
        return triage

    # 3b. Routine non-deadline document (e.g. 上诉缴费单) -> auto-archive file + log event
    if ev_type == "document_served" and triage.downloaded_files and payload.get("doc_title_hint"):
        triage.extracted_payload = payload
        doc_hint = str(payload.get("doc_title_hint"))
        apply_triage_to_case(
            session,
            item=triage,
            actor_user=owner,
            target_case=target_case,
            file_subfolder="01_司法与程序文书",
            event_type="document_served",
            event_date=_parse_event_date(payload.get("event_date")),
            event_notes=f"自动归档：{doc_hint}",
            auto_archived=True,
            triage_reason=f"常规程序文书「{doc_hint}」已自动归档至「01_司法与程序文书」",
        )
        return triage

    # 3c. Statutory deadline event covered by enabled DeadlineRule(s) for this case
    if ev_type:
        candidate_rules = list(
            session.exec(
                select(DeadlineRule)
                .where(
                    DeadlineRule.case_category == target_case.case_category,
                    DeadlineRule.trigger_event_type == ev_type,
                    DeadlineRule.is_enabled == True,  # noqa: E712
                )
                .order_by(DeadlineRule.id)
            ).all()
        )
        matching_rules = [r for r in candidate_rules if rule_matches_case(r, target_case)]
        if matching_rules:
            if (
                ev_type == "evidence_notice_served"
                and not payload.get("authority_specified_days")
                and any(isinstance(d, dict) and d.get("has_text") is False for d in parsed_docs)
            ):
                payload["triage_decision"] = "needs_confirm"
                payload["triage_reason"] = "举证通知书为扫描件，未能自动提取法院指定举证天数，请人工核对"
                triage.extracted_payload = payload
                return triage

            triage.extracted_payload = payload
            doc_hint = payload.get("doc_title_hint") or ev_type
            apply_triage_to_case(
                session,
                item=triage,
                actor_user=owner,
                target_case=target_case,
                file_subfolder="01_司法与程序文书",
                event_type=ev_type,
                event_date=_parse_event_date(payload.get("event_date")),
                authority_specified_days=payload.get("authority_specified_days"),
                event_notes=f"自动归档：{doc_hint}",
                auto_archived=True,
                triage_reason=f"命中 {len(matching_rules)} 条法定期限规则，已自动归档、推进阶段并生成期限",
            )
            return triage

    # 3d. Fallback: no covering rule -> alert human confirmation
    payload["triage_decision"] = "needs_confirm"
    if not ev_type:
        payload["triage_reason"] = "未能识别具体程序事件或文书类型（无覆盖规则），请人工确认"
    else:
        payload["triage_reason"] = (
            f"案件类别「{target_case.case_category.value}」下暂无事件「{ev_type}」的自动处理规则，请人工确认"
        )
    triage.extracted_payload = payload
    return triage


def ingest_sms_and_create_triage(
    session: Session,
    user: User,
    normalized: NormalizedSmsPayload,
    *,
    dedup_override: Optional[str] = None,
) -> tuple[SmsInbound, InboxTriageItem, bool]:
    """Persist ``SmsInbound`` and create an ``InboxTriageItem`` (idempotent on ``dedup_key``)."""
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
    match_res = match_case_with_confidence(
        session, user, parsed, extra_text=normalized.content
    )
    parsed = _apply_match_metadata(parsed, match_res)

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
        matched_case_id=match_res.case.id if match_res.case else None,
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
    """Enrich an ``InboxTriageItem``, fetch docs/invoices, and auto-archive if covered by rules."""
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
            prev_code = payload.get("extraction_code")
            payload = await parse_sms_with_optional_llm(
                sms_content,
                received_at=sms_row.received_at if sms_row else None,
            )
            if prev_code and not payload.get("extraction_code"):
                payload["extraction_code"] = prev_code

        extra_match_parts: list[str] = [sms_content]

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

                for v in (
                    inv_res.invoice_meta.get("remark"),
                    inv_res.invoice_meta.get("buyer_name"),
                ):
                    if v:
                        extra_match_parts.append(str(v))
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

        match_res = match_case_with_confidence(
            session, owner, payload, extra_text=" ".join(p for p in extra_match_parts if p)
        )
        if match_res.case is not None:
            triage.matched_case_id = match_res.case.id

        triage.extracted_payload = payload
        evaluate_and_apply_auto_triage(
            session,
            owner=owner,
            triage=triage,
            match_res=match_res,
        )
        session.add(triage)
        session.commit()
        session.refresh(triage)

        if triage.status == TriageStatus.MANUAL_FETCH_REQUIRED:
            from app.services.reminder_scheduler import push_manual_fetch_alert

            push_manual_fetch_alert(session, triage)
        elif triage.status == TriageStatus.PENDING:
            from app.services.reminder_scheduler import push_pending_confirm_alert

            push_pending_confirm_alert(session, triage)
