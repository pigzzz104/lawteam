from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.database import engine
from app.main import app
from app.models.entities import (
    AuditLog,
    Case,
    CaseEvent,
    CaseMember,
    Deadline,
    Document,
    InboxTriageItem,
    ScheduleItem,
    SmsInbound,
    TriageStatus,
    User,
)
from app.services import court_fetcher, invoice_fetcher
from app.services.llm_client import UniversalLLMClient
from app.services.sms_parser import parse_sms_regex, parse_sms_with_optional_llm


def _make_minimal_pdf(ascii_text: str = "LawTeam Test PDF") -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({ascii_text}) Tj ET".encode("latin-1")
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream)} >> stream\n".encode("ascii")
        + stream
        + b"\nendstream endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)


class _DummyPdfPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _DummyPdfDoc:
    def __init__(self, pages: list[str]) -> None:
        self.pages = [_DummyPdfPage(t) for t in pages]

    def __enter__(self) -> "_DummyPdfDoc":
        return self

    def __exit__(self, *args: Any) -> None:
        return None


@pytest.fixture
def isolated_cases_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CASES_ROOT", str(cases_dir))
    get_settings.cache_clear()
    yield cases_dir
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_cases_root: Path) -> TestClient:
    with TestClient(app) as tc:
        yield tc


def _login(client: TestClient, username: str, password: str = "LawTeam@2026") -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup_records(
    *,
    case_ids: list[int] | None = None,
    triage_ids: list[int] | None = None,
    sms_ids: list[int] | None = None,
    doc_ids: list[int] | None = None,
) -> None:
    with Session(engine) as session:
        if case_ids:
            for cid in case_ids:
                for model in (AuditLog, Deadline, ScheduleItem, Document, CaseEvent, CaseMember):
                    for row in session.exec(select(model).where(model.case_id == cid)).all():
                        session.delete(row)
                for row in session.exec(
                    select(InboxTriageItem).where(InboxTriageItem.matched_case_id == cid)
                ).all():
                    session.delete(row)
        if triage_ids:
            for tid in triage_ids:
                for row in session.exec(
                    select(AuditLog).where(
                        AuditLog.entity_type == "inbox_triage_item",
                        AuditLog.entity_id == tid,
                    )
                ).all():
                    session.delete(row)
                t = session.get(InboxTriageItem, tid)
                if t is not None:
                    session.delete(t)
        if doc_ids:
            for did in doc_ids:
                d = session.get(Document, did)
                if d is not None:
                    session.delete(d)
        if sms_ids:
            for sid in sms_ids:
                for t in session.exec(
                    select(InboxTriageItem).where(InboxTriageItem.sms_id == sid)
                ).all():
                    for rel_f in t.downloaded_files or []:
                        for d in session.exec(
                            select(Document).where(Document.rel_file_path == rel_f)
                        ).all():
                            session.delete(d)
                    session.delete(t)
                for ev in session.exec(
                    select(CaseEvent).where(CaseEvent.source_sms_id == sid)
                ).all():
                    ev.source_sms_id = None
                    session.add(ev)
                session.flush()
                s = session.get(SmsInbound, sid)
                if s is not None:
                    session.delete(s)
        if case_ids:
            for cid in case_ids:
                c = session.get(Case, cid)
                if c is not None:
                    session.delete(c)
        session.commit()


def test_real_court_and_multi_agency_sms_regex_parsing() -> None:
    samples = [
        (
            "【上海市第一中级人民法院】张晨你好，请查收（2026）沪01民终6604号案件中你的送达文书。"
            "点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?"
            "qdbh=d912b9c5315f4aafa1662b2e5cbf8fe3&sdbh=2bc0bb21b92f46198b836e53f6d30bf6&sdsin=77b305fb135ee6acca10ab3e138ae23c",
            "court_service",
            "civil",
            "（2026）沪01民终6604号",
            "zxfw.court.gov.cn",
            None,
        ),
        (
            "【上海市浦东新区人民法院】上海馨成达会展科技有限公司代理人：（2025）沪0115执26804号案件送达调查令。。"
            "点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?"
            "qdbh=310df442aa7547ea9e291d4361b27d21&sdbh=9f582873f762411b969d79203d6d394c&sdsin=31fce359e1dc8f7e48854d26e04a8b44",
            "court_service",
            "enforcement",
            "（2025）沪0115执26804号",
            "zxfw.court.gov.cn",
            "上海馨成达会展科技有限公司",
        ),
        (
            "【广州市越秀区人民法院】张晨你好，请查收（2025）粤0104民初27096号案件中你的送达文书（上诉缴费单）。"
            "点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?"
            "qdbh=d872f6c92c934cf1b9008b4600ec761f&sdbh=dbbd740def8d4def8820d463989b1c2d&sdsin=3331de5887a4f94040efd365fef43d9f 。",
            "court_service",
            "civil",
            "（2025）粤0104民初27096号",
            "zxfw.court.gov.cn",
            None,
        ),
        (
            "【12309中国检察网】张律师：您代理的（2026）沪0115检刑诉88号涉嫌职务侵占案已移送审查起诉，请及时联系办理阅卷。",
            "procuratorate_12309",
            "criminal",
            "（2026）沪0115检刑诉88号",
            None,
            None,
        ),
        (
            "【上海劳动仲裁委】您代理的（2026）沪劳人仲331号案件定于2026年07月10日上午9时30分开庭审理。",
            "arbitration",
            "labor_arbitration",
            "（2026）沪劳人仲331号",
            None,
            None,
        ),
    ]
    for content, exp_sms_cat, exp_case_cat, exp_case_no, exp_domain, exp_client in samples:
        parsed = parse_sms_regex(content)
        assert parsed["sms_category"] == exp_sms_cat
        assert parsed["case_category_guess"] == exp_case_cat
        assert parsed["case_number"] == exp_case_no
        if exp_domain:
            assert parsed["court_links"]
            link = parsed["court_links"][0]
            assert link["domain"] == exp_domain
            assert {"qdbh", "sdbh", "sdsin"}.issubset(link["params"].keys())
        if exp_client:
            assert parsed["client_hint"] == exp_client

    assert parse_sms_regex(samples[2][0])["doc_title_hint"] == "上诉缴费单"
    assert parse_sms_regex(samples[3][0])["event_type_guess"] == "prosecution_review_started"
    assert parse_sms_regex(samples[4][0])["hearing_time"] == "2026-07-10T09:30:00+08:00"


@pytest.mark.asyncio
async def test_optional_llm_fallback_only_fills_missing_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def fake_extract(self: UniversalLLMClient, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "case_number": "（2026）沪01民初999号",
            "case_category_guess": "civil",
            "agency": "上海市浦东新区人民法院",
            "event_type_guess": "hearing_scheduled",
            "hearing_time": "2026-08-05T14:00:00+08:00",
            "client_hint": "上海未来科技有限公司",
        }

    monkeypatch.setattr(UniversalLLMClient, "is_enabled", property(lambda self: True))
    monkeypatch.setattr(UniversalLLMClient, "extract_json", fake_extract)

    ambiguous_sms = "书记员通知：未来科技那个案子改期到下周三下午2点开庭，请准时到庭。"
    res = await parse_sms_with_optional_llm(ambiguous_sms)
    assert calls == 1
    assert res["llm_assisted"] is True
    assert res["case_number"] == "（2026）沪01民初999号"
    assert res["event_type_guess"] == "hearing_scheduled"
    assert res["client_hint"] == "上海未来科技有限公司"


def test_multi_platform_sms_webhooks_hmac_and_dedup(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Avoid real outbound HTTP calls during background fetch
    async def no_network_fetch(*args: Any, **kwargs: Any) -> court_fetcher.CourtFetchResult:
        return court_fetcher.CourtFetchResult(
            status="manual_required",
            tier_used="tier3_manual",
            manual_reason="测试跳过外网抓取",
        )

    monkeypatch.setattr(
        "app.services.triage_pipeline.fetch_court_documents",
        no_network_fetch,
    )

    with Session(engine) as session:
        lawyer = session.exec(select(User).where(User.username == "lead_lawyer")).first()
        assistant = session.exec(select(User).where(User.username == "legal_assistant")).first()
        assert lawyer is not None and assistant is not None
        lawyer_token = lawyer.sms_webhook_token
        assistant_token = assistant.sms_webhook_token
        orig_secret = assistant.sms_webhook_secret
        assistant.sms_webhook_secret = "test-hmac-secret-2026"
        session.add(assistant)
        session.commit()

    created_sms_ids: list[int] = []
    created_triage_ids: list[int] = []
    try:
        # 1. iOS Shortcuts JSON + <200ms check
        ios_text = "【上海市第一中级人民法院】张晨你好，请查收（2026）沪01民终6604号案件中你的送达文书。"
        t0 = time.perf_counter()
        r1 = client.post(
            f"/api/webhooks/sms?token={lawyer_token}&platform=ios_shortcuts",
            json={"content": ios_text, "sender": "12368", "timestamp": "2026-06-12T17:03:00+08:00"},
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert r1.status_code == 202, r1.text
        assert elapsed_ms < 2000  # plenty below budget under TestClient inline task
        b1 = r1.json()
        assert b1["status"] == "accepted"
        assert b1["client_platform"] == "ios_shortcuts"
        created_sms_ids.append(b1["sms_id"])
        created_triage_ids.append(b1["triage_id"])

        # 2. Same content on same Shanghai date -> deduplicated
        r_dup = client.post(
            f"/api/webhooks/sms/{lawyer_token}",
            json={"msg": ios_text, "timestamp": "2026-06-12T22:15:00+08:00"},
        )
        assert r_dup.status_code == 202
        assert r_dup.json()["status"] == "duplicate"
        assert r_dup.json()["sms_id"] == b1["sms_id"]

        # 3. Same content next Shanghai day -> new record allowed
        r_next_day = client.post(
            f"/api/webhooks/sms/{lawyer_token}?platform=mac_relay",
            json={"content": ios_text, "handle": "12368", "timestamp": "2026-06-13T09:00:00+08:00"},
        )
        assert r_next_day.status_code == 202
        assert r_next_day.json()["status"] == "accepted"
        assert r_next_day.json()["client_platform"] == "mac_relay"
        created_sms_ids.append(r_next_day.json()["sms_id"])
        created_triage_ids.append(r_next_day.json()["triage_id"])

        # 4. Android SmsForwarder HMAC rejection vs acceptance (urlencoded)
        ts = "1781254980000"
        digest = hmac.new(
            b"test-hmac-secret-2026",
            f"{ts}\ntest-hmac-secret-2026".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        valid_sig = base64.b64encode(digest).decode("ascii")

        r_bad_sig = client.post(
            f"/api/webhooks/sms?token={assistant_token}",
            data={"from": "1069000", "content": "【看守所】拘留通知", "timestamp": ts, "sign": "bad"},
        )
        assert r_bad_sig.status_code == 401

        r_good_sig = client.post(
            f"/api/webhooks/sms?token={assistant_token}&platform=android_smsforwarder",
            data={
                "from": "1069000",
                "content": "【浦东新区看守所】拘留通知书已送达（2026）沪0115刑初12号。",
                "timestamp": ts,
                "sign": valid_sig,
            },
        )
        assert r_good_sig.status_code == 202, r_good_sig.text
        assert r_good_sig.json()["client_platform"] == "android_smsforwarder"
        created_sms_ids.append(r_good_sig.json()["sms_id"])
        created_triage_ids.append(r_good_sig.json()["triage_id"])
    finally:
        with Session(engine) as session:
            assistant = session.exec(select(User).where(User.username == "legal_assistant")).first()
            if assistant:
                assistant.sms_webhook_secret = orig_secret
                session.add(assistant)
                session.commit()
        _cleanup_records(triage_ids=created_triage_ids, sms_ids=created_sms_ids)


def test_court_3tier_phone_last4_retry_and_atomic_confirm_pipeline(
    client: TestClient, isolated_cases_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "lead_lawyer")
    sample_pdf = _make_minimal_pdf("Court Judgment")

    # Simulate Tier-1 demanding phone_last4 until '8966' is provided
    async def mock_post_zxfw(
        qdbh: str, sdbh: str, sdsin: str, phone_last4: str | None = None
    ) -> dict[str, Any]:
        if phone_last4 != "8966":
            return {"success": False, "msg": "请验证手机号后四位后查收文书"}
        return {
            "success": True,
            "data": [
                {
                    "c_fymc": "上海市第一中级人民法院",
                    "c_wsmc": "民事判决书",
                    "wjlj": "https://mock.court.local/doc/judgment.pdf",
                    "c_wjgs": "pdf",
                    "dt_cjsj": "2026-06-12 17:03:00",
                }
            ],
        }

    async def mock_dl(url: str) -> bytes:
        return sample_pdf

    monkeypatch.setattr(court_fetcher, "_post_zxfw_api", mock_post_zxfw)
    monkeypatch.setattr(court_fetcher, "_download_url_bytes", mock_dl)
    monkeypatch.setattr(
        court_fetcher.pdfplumber,
        "open",
        lambda *args, **kwargs: _DummyPdfDoc(
            [
                "上海市第一中级人民法院\n民事判决书\n（2026）沪01民终6604号\n"
                "定于2026年06月30日上午9时30分在本院第三法庭公开宣判\n审判员：张敏\n书记员：李青"
            ]
        ),
    )

    sms_text = (
        "【上海市第一中级人民法院】张晨你好，请查收（2026）沪01民终6604号案件中你的送达文书。"
        "点击链接查阅：https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?"
        "qdbh=d912b9c5315f4aafa1662b2e5cbf8fe3&sdbh=2bc0bb21b92f46198b836e53f6d30bf6&sdsin=77b305fb135ee6acca10ab3e138ae23c"
    )

    created_case_ids: list[int] = []
    created_triage_ids: list[int] = []
    created_sms_ids: list[int] = []
    try:
        # Step 1: Ingest via simulate without tail 8966 -> Tier 3 MANUAL_FETCH_REQUIRED
        sim_resp = client.post(
            "/api/inbox/simulate",
            headers=headers,
            json={"content": sms_text, "sender": "12368", "auto_fetch": True},
        )
        assert sim_resp.status_code == 201, sim_resp.text
        triage = sim_resp.json()
        created_triage_ids.append(triage["id"])
        if triage["sms_id"]:
            created_sms_ids.append(triage["sms_id"])
        assert triage["status"] == TriageStatus.MANUAL_FETCH_REQUIRED.value
        assert triage["extracted_payload"]["requires_phone_last4"] is True

        # Iron Rule #3: No Case or Document was written automatically
        assert triage["matched_case_id"] is None

        # Step 2: Retry with phone_last4="8966" & confirm_sign=True -> succeeds!
        retry_resp = client.post(
            f"/api/inbox/{triage['id']}/retry-fetch",
            headers=headers,
            json={"phone_last4": "8966", "confirm_sign": True},
        )
        assert retry_resp.status_code == 200, retry_resp.text
        retried = retry_resp.json()
        assert retried["status"] == TriageStatus.PENDING.value
        assert len(retried["downloaded_files"]) == 1
        assert retried["downloaded_files"][0].startswith("_待分拣暂存/")
        assert (isolated_cases_root / retried["downloaded_files"][0]).is_file()
        assert "第三法庭" in (retried["extracted_payload"]["hearing_location"] or "")
        assert retried["extracted_payload"]["event_type_guess"] == "judgment_served"

        # Step 3: Atomic confirm -> creates Case, CaseEvent, Civil 15-day Appeal Deadline, ScheduleItem, Document, AuditLog
        confirm_resp = client.post(
            f"/api/inbox/{triage['id']}/confirm",
            headers=headers,
            json={
                "create_case": {
                    "case_title": "张晨合同纠纷二审",
                    "case_number": "（2026）沪01民终6604号",
                    "case_category": "civil",
                    "procedure_subtype": "二审",
                    "client_name": "张晨",
                    "handling_agency": "上海市第一中级人民法院",
                },
                "event_type": "judgment_served",
                "event_date": "2026-06-12",
                "service_method": "electronic",
                "new_stage": "裁判生效",
                "create_hearing": {
                    "category": "hearing",
                    "title": "二审宣判",
                    "start_time": "2026-06-30T09:30:00+08:00",
                    "end_time": "2026-06-30T10:30:00+08:00",
                    "location": "上海市一中院第三法庭",
                },
            },
        )
        assert confirm_resp.status_code == 200, confirm_resp.text
        cdata = confirm_resp.json()
        created_case_ids.append(cdata["case_id"])
        assert cdata["created_case"] is True
        assert cdata["created_event_id"] is not None
        assert cdata["generated_deadline_count"] >= 1
        assert cdata["created_schedule_id"] is not None
        assert len(cdata["archived_documents"]) == 1
        archived_rel = cdata["archived_documents"][0]
        assert "/01_司法与程序文书/" in archived_rel
        assert (isolated_cases_root / archived_rel).is_file()

        with Session(engine) as session:
            c = session.get(Case, cdata["case_id"])
            assert c is not None and c.current_stage == "裁判生效"
            dls = session.exec(select(Deadline).where(Deadline.case_id == c.id)).all()
            assert any(d.rule_code == "CIVIL_APPEAL_JUDGMENT_15D" and d.due_date == date(2026, 6, 29) for d in dls)
            audits = session.exec(
                select(AuditLog).where(AuditLog.case_id == c.id, AuditLog.action == "inbox.confirm")
            ).all()
            assert len(audits) == 1
    finally:
        _cleanup_records(
            case_ids=created_case_ids,
            triage_ids=created_triage_ids,
            sms_ids=created_sms_ids,
        )


def test_inbox_manual_upload_list_and_ignore(
    client: TestClient, isolated_cases_root: Path
) -> None:
    headers = _login(client, "lead_lawyer")
    sample_pdf = _make_minimal_pdf("Manual Court Upload")
    created_triage_ids: list[int] = []
    try:
        up_resp = client.post(
            "/api/inbox/manual-upload",
            headers=headers,
            data={"item_type": "judicial_stage_and_doc", "notes": "传票扫描件"},
            files={"files": ("summons.pdf", sample_pdf, "application/pdf")},
        )
        assert up_resp.status_code == 201, up_resp.text
        triage = up_resp.json()
        created_triage_ids.append(triage["id"])
        assert triage["status"] == TriageStatus.PENDING.value
        assert triage["downloaded_files"][0].startswith("_待分拣暂存/")
        assert (isolated_cases_root / triage["downloaded_files"][0]).is_file()

        list_resp = client.get("/api/inbox?status=pending", headers=headers)
        assert list_resp.status_code == 200
        assert any(it["id"] == triage["id"] for it in list_resp.json())

        ign_resp = client.post(f"/api/inbox/{triage['id']}/ignore", headers=headers)
        assert ign_resp.status_code == 200, ign_resp.text
        assert ign_resp.json()["status"] == TriageStatus.IGNORED.value
    finally:
        _cleanup_records(triage_ids=created_triage_ids)


def test_invoice_pdf_staging_and_claim_to_case(
    client: TestClient, isolated_cases_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _login(client, "lead_lawyer")
    invoice_pdf = _make_minimal_pdf("VAT Invoice")

    async def mock_dl_invoice(url: str) -> bytes:
        return invoice_pdf

    monkeypatch.setattr(invoice_fetcher, "_download_invoice_pdf_direct", mock_dl_invoice)
    monkeypatch.setattr(
        invoice_fetcher.pdfplumber,
        "open",
        lambda *args, **kwargs: _DummyPdfDoc(
            [
                "电子发票（普通发票）\n发票号码：26312000000123456789\n开票日期：2026年06月15日\n"
                "购买方信息 名称：上海馨成达会展科技有限公司 统一社会信用代码：91310115MA1K998877\n"
                "销售方信息 名称：上海某律所\n"
                "*鉴证咨询服务*法律服务费\n价税合计(小写)¥15000.00\n备注：（2026）沪0115执99804号案件律师费"
            ]
        ),
    )

    created_case_ids: list[int] = []
    created_triage_ids: list[int] = []
    created_sms_ids: list[int] = []
    try:
        case_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "馨成达会展执行测试案",
                "case_number": "（2026）沪0115执99804号",
                "case_category": "enforcement",
                "client_name": "上海馨成达会展科技有限公司",
            },
        )
        assert case_resp.status_code == 201, case_resp.text
        case_id = case_resp.json()["case"]["id"]
        created_case_ids.append(case_id)

        inv_sms = (
            "【诺诺网】您有一张电子发票已开具，请点击下载：https://nnfp.jss.com.cn/abc123def"
        )
        sim_resp = client.post(
            "/api/inbox/simulate",
            headers=headers,
            json={"content": inv_sms, "sender": "1069012", "auto_fetch": True},
        )
        assert sim_resp.status_code == 201, sim_resp.text
        triage = sim_resp.json()
        created_triage_ids.append(triage["id"])
        if triage["sms_id"]:
            created_sms_ids.append(triage["sms_id"])

        assert triage["item_type"] == "invoice_archive"
        assert triage["matched_case_id"] == case_id
        assert len(triage["downloaded_files"]) == 1
        staged_rel = triage["downloaded_files"][0]
        assert staged_rel.startswith("_待认领发票/20260615_法律服务费_15000.00元_26312000000123456789")
        assert (isolated_cases_root / staged_rel).is_file()

        confirm_resp = client.post(
            f"/api/inbox/{triage['id']}/confirm",
            headers=headers,
            json={"case_id": case_id},
        )
        assert confirm_resp.status_code == 200, confirm_resp.text
        archived_rel = confirm_resp.json()["archived_documents"][0]
        assert "/05_财务与发票/" in archived_rel
        assert (isolated_cases_root / archived_rel).is_file()
        assert not (isolated_cases_root / staged_rel).exists()

        with Session(engine) as session:
            doc = session.exec(
                select(Document).where(Document.rel_file_path == archived_rel)
            ).first()
            assert doc is not None
            assert doc.case_id == case_id
            assert doc.folder_category == "05_财务与发票"
            assert doc.invoice_meta and doc.invoice_meta["invoice_number"] == "26312000000123456789"
    finally:
        _cleanup_records(
            case_ids=created_case_ids,
            triage_ids=created_triage_ids,
            sms_ids=created_sms_ids,
        )
