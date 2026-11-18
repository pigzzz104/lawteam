"""P1 repair verification tests: scanned PDF OCR fallback, 12309/arbitration fetching, and fuzzy matching."""

from __future__ import annotations

import asyncio
import io
import secrets
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlmodel import Session, select

from app.config import get_settings
from app.database import engine
from app.models.entities import (
    AuditLog,
    Case,
    CaseEvent,
    CaseMember,
    Deadline,
    Document,
    InboxTriageItem,
    ReminderDelivery,
    ScheduleItem,
    SmsInbound,
    TriageStatus,
    User,
)
from app.services import court_fetcher, invoice_fetcher, ocr_service
from app.services.sms_parser import match_case_with_confidence, parse_sms_regex
from app.services.triage_pipeline import enrich_and_fetch_triage_item


def _make_image_only_pdf(label: str = "SCANNED") -> bytes:
    """Create a real image-only PDF (zero embedded text layer)."""
    img = Image.new("RGB", (600, 800), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((40, 40), label, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def _login_headers(client: TestClient, username: str, password: str = "LawTeam@2026") -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup_records(*, case_ids: list[int], sms_ids: list[int], triage_ids: list[int]) -> None:
    with Session(engine) as session:
        for tid in triage_ids:
            for d in session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.source_type == "triage_alert",
                    ReminderDelivery.source_id == tid,
                )
            ).all():
                session.delete(d)
            for log in session.exec(
                select(AuditLog).where(
                    AuditLog.entity_type == "inbox_triage_item",
                    AuditLog.entity_id == tid,
                )
            ).all():
                session.delete(log)
            t = session.get(InboxTriageItem, tid)
            if t is not None:
                session.delete(t)
        for cid in case_ids:
            for model in (AuditLog, Deadline, ScheduleItem, Document, CaseEvent, CaseMember):
                for row in session.exec(select(model).where(model.case_id == cid)).all():
                    session.delete(row)
        session.flush()
        for sid in sms_ids:
            s = session.get(SmsInbound, sid)
            if s is not None:
                session.delete(s)
        for cid in case_ids:
            c = session.get(Case, cid)
            if c is not None:
                session.delete(c)
        session.commit()


def test_scanned_evidence_pdf_ocr_vlm_and_safe_pending_fallback(
    client: TestClient,
    isolated_cases_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanned_pdf = _make_image_only_pdf("EVIDENCE_NOTICE_SCAN")
    base_info = court_fetcher.parse_court_pdf_bytes(scanned_pdf, "举证通知书.pdf")
    assert base_info["has_text"] is False
    assert base_info["is_scanned"] is True

    headers = _login_headers(client, "lead_lawyer")
    case_ids: list[int] = []
    sms_ids: list[int] = []
    triage_ids: list[int] = []

    try:
        case_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "扫描举证OCR自动归档验证案",
                "case_number": "（2026）沪0105民初7771号",
                "case_category": "civil",
                "client_name": "上海原告甲有限公司",
            },
        )
        assert case_resp.status_code == 201, case_resp.text
        case_id = case_resp.json()["case"]["id"]
        case_ids.append(case_id)

        with Session(engine) as session:
            lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
            assert lead is not None
            token = lead.sms_webhook_token

        # Case 1: VLM enabled extracts authority_specified_days=15 -> auto-archives!
        monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
        monkeypatch.setenv("LLM_API_KEY", "test-vlm-key")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
        get_settings.cache_clear()

        async def mock_vlm_call(
            self: Any,
            *,
            system_prompt: str,
            user_content: Any,
            temperature: float,
            max_tokens: int,
        ) -> str:
            assert isinstance(user_content, list)
            assert any(part.get("type") == "image_url" for part in user_content)
            return "上海市长宁区人民法院\n举证通知书\n（2026）沪0105民初7771号\n本院指定举证期限为自收到本通知书之日起15日。"

        monkeypatch.setattr(
            ocr_service.UniversalLLMClient,
            "_call_openai_compatible",
            mock_vlm_call,
        )

        async def mock_zxfw_post(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "success": True,
                "data": [
                    {
                        "c_fymc": "上海市长宁区人民法院",
                        "c_wsmc": "举证通知书",
                        "wjlj": "https://example.invalid/scanned_evidence.pdf",
                        "c_wjgs": "pdf",
                        "dt_cjsj": "2026-09-21 10:00:00",
                    }
                ],
            }

        async def mock_dl_scanned(*args: Any, **kwargs: Any) -> bytes:
            return scanned_pdf

        monkeypatch.setattr(court_fetcher, "_post_zxfw_api", mock_zxfw_post)
        monkeypatch.setattr(court_fetcher, "_download_url_bytes", mock_dl_scanned)

        wh1 = client.post(
            f"/api/webhooks/sms/{token}",
            json={
                "content": (
                    "【上海长宁法院】请查收（2026）沪0105民初7771号案件送达文书。"
                    "https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?"
                    "qdbh=q1&sdbh=s1&sdsin=d1"
                ),
                "dedup_key": f"p1-ocr-ok-{secrets.token_hex(5)}",
            },
        )
        assert wh1.status_code == 202
        sms_ids.append(wh1.json()["sms_id"])
        triage1_id = wh1.json()["triage_id"]
        triage_ids.append(triage1_id)

        asyncio.run(enrich_and_fetch_triage_item(triage1_id))

        with Session(engine) as session:
            t1 = session.get(InboxTriageItem, triage1_id)
            assert t1 is not None
            assert t1.status == TriageStatus.CONFIRMED
            assert t1.extracted_payload["auto_archived"] is True
            assert t1.extracted_payload["authority_specified_days"] == 15
            parsed_doc = t1.extracted_payload["parsed_docs"][0]
            assert parsed_doc["ocr_used"] is True
            assert parsed_doc["ocr_source"] == "vlm"

        # Case 2: Scanned PDF yields title via local OCR without authority_specified_days -> stays PENDING
        monkeypatch.setenv("LLM_PROVIDER", "none")
        get_settings.cache_clear()
        monkeypatch.setattr(
            ocr_service,
            "_run_local_ocr_on_images",
            lambda imgs: ("上海市长宁区人民法院\n举证通知书\n（2026）沪0105民初7771号", "rapidocr"),
        )

        wh2 = client.post(
            f"/api/webhooks/sms/{token}",
            json={
                "content": (
                    "【上海长宁法院】请查收（2026）沪0105民初7771号案件送达文书。"
                    "https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?"
                    "qdbh=q2&sdbh=s2&sdsin=d2"
                ),
                "dedup_key": f"p1-ocr-pending-{secrets.token_hex(5)}",
            },
        )
        assert wh2.status_code == 202
        sms_ids.append(wh2.json()["sms_id"])
        triage2_id = wh2.json()["triage_id"]
        triage_ids.append(triage2_id)

        asyncio.run(enrich_and_fetch_triage_item(triage2_id))

        with Session(engine) as session:
            t2 = session.get(InboxTriageItem, triage2_id)
            assert t2 is not None
            assert t2.status == TriageStatus.PENDING
            assert "扫描件" in (t2.extracted_payload.get("triage_reason") or "")

        # Case 3: Scanned PDF invoice OCR fallback extracts metadata cleanly
        monkeypatch.setattr(
            ocr_service,
            "_run_local_ocr_on_images",
            lambda imgs: (
                "电子发票\n发票号码：26310000112233445566\n开票日期：2026年09月22日\n"
                "购买方名称：上海原告甲有限公司\n价税合计（小写）¥6800.00",
                "rapidocr",
            ),
        )
        inv_meta = asyncio.run(
            invoice_fetcher.parse_invoice_bytes_with_ocr(_make_image_only_pdf("INVOICE"), ext=".pdf")
        )
        assert inv_meta["invoice_number"] == "26310000112233445566"
        assert inv_meta["total_amount"] == "6800.00"
        assert inv_meta["buyer_name"] == "上海原告甲有限公司"
        assert inv_meta["ocr_used"] is True
    finally:
        get_settings.cache_clear()
        _cleanup_records(case_ids=case_ids, sms_ids=sms_ids, triage_ids=triage_ids)


def test_12309_and_arbitration_links_plus_html_pdf_redirect(
    isolated_cases_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sms_12309 = (
        "【12309中国检察网】张律师，请查收（2026）沪0115检刑诉99号审查起诉阶段权利义务告知书："
        "https://www.12309.gov.cn/notice/doc99"
    )
    parsed_12309 = parse_sms_regex(sms_12309)
    assert parsed_12309["sms_category"] == "procuratorate_12309"
    assert parsed_12309["case_number"] == "（2026）沪0115检刑诉99号"
    assert parsed_12309["court_links"][0]["domain"] == "12309.gov.cn"
    assert parsed_12309["court_links"][0]["download_strategy"] == "direct_or_playwright"

    sms_arb = (
        "【北京仲裁委员会】您代理的京仲案字〔2026〕第0512号案件请查收仲裁通知书："
        "https://www.bjac.org.cn/sd/case512"
    )
    parsed_arb = parse_sms_regex(sms_arb)
    assert parsed_arb["sms_category"] == "arbitration"
    assert parsed_arb["case_number"] == "京仲案字（2026）第0512号"
    assert parsed_arb["court_links"][0]["domain"] == "arbitration_portal"

    async def mock_fetch_resp(url: str) -> tuple[bytes, str, str, str]:
        if url.endswith("/case512"):
            html = '<html><body><a href="/files/arbitration_notice.pdf">下载文书</a></body></html>'
            return html.encode("utf-8"), url, "text/html", ""
        return b"%PDF-1.4\n" + b"1" * 128, url, "application/pdf", ""

    monkeypatch.setattr(court_fetcher, "_fetch_url_response", mock_fetch_resp)

    res = asyncio.run(
        court_fetcher.fetch_court_documents(
            parsed_arb,
            batch_key="arb_512",
        )
    )
    assert res.status == "fetched"
    assert res.tier_used == "tier1_api"
    assert len(res.downloaded_files) == 1
    assert (isolated_cases_root / res.downloaded_files[0]).exists()


def test_fuzzy_company_alias_and_loose_case_number_with_ambiguity_guard(
    client: TestClient,
    isolated_cases_root: Path,
) -> None:
    headers = _login_headers(client, "lead_lawyer")
    case_ids: list[int] = []
    try:
        c1_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "馨成达执行案",
                "case_number": "（2026）沪0105执88号",
                "case_category": "enforcement",
                "client_name": "上海馨成达会展科技有限公司",
            },
        )
        assert c1_resp.status_code == 201
        c1_id = c1_resp.json()["case"]["id"]
        case_ids.append(c1_id)

        with Session(engine) as session:
            lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
            assert lead is not None

            # 1. Loose case number match (`执第88号` vs `执88号`)
            loose_res = match_case_with_confidence(
                session, lead, {"case_number": "(2026)沪0105执第88号"}
            )
            assert loose_res.confidence == "exact_case_number"
            assert loose_res.case is not None and loose_res.case.id == c1_id

            # 2. Unique corporate short-name alias match (`馨成达`)
            alias_res = match_case_with_confidence(
                session,
                lead,
                {"sms_category": "invoice", "invoice_meta": {"buyer_name": "馨成达"}},
                extra_text="开票给馨成达的法律服务费电子发票",
            )
            assert alias_res.confidence == "unique_client_alias"
            assert alias_res.case is not None and alias_res.case.id == c1_id

        # 3. Adding second company sharing short name triggers ambiguity protection
        c2_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "江苏馨成达建设纠纷案",
                "case_number": "（2026）苏01民初333号",
                "case_category": "civil",
                "client_name": "江苏馨成达建筑工程有限公司",
            },
        )
        assert c2_resp.status_code == 201
        c2_id = c2_resp.json()["case"]["id"]
        case_ids.append(c2_id)

        with Session(engine) as session:
            lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
            assert lead is not None
            ambig = match_case_with_confidence(
                session,
                lead,
                {"sms_category": "invoice", "invoice_meta": {"buyer_name": "馨成达"}},
                extra_text="开票给馨成达的法律服务费",
            )
            assert ambig.confidence == "ambiguous"
            assert ambig.case is None
            assert set(ambig.candidate_case_ids) >= {c1_id, c2_id}
    finally:
        _cleanup_records(case_ids=case_ids, sms_ids=[], triage_ids=[])
