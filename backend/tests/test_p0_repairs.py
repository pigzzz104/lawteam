"""P0 repair verification tests: multi-format invoices, Hubei/Tier-2 court fetching, and WeCom callbacks."""

from __future__ import annotations

import base64
import io
import os
import secrets
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.core.security import create_triage_action_token
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
from app.services import court_fetcher, invoice_fetcher
from app.services.reminder_scheduler import push_pending_confirm_alert
from app.services.triage_pipeline import enrich_and_fetch_triage_item
from app.services.wecom_callback import compute_wecom_signature

_SAMPLE_INVOICE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<EInvoice>
  <Header>
    <EIid>26312000000012345678</EIid>
  </Header>
  <EInvoiceData>
    <SellerInformation><SellerName>上海市致诚律师事务所</SellerName></SellerInformation>
    <BuyerInformation><BuyerName>上海华兴机电工程有限公司</BuyerName></BuyerInformation>
    <BasicInformation>
      <TotalTax-includedAmount>12800.00</TotalTax-includedAmount>
      <IssueTime>2026-09-18 14:20:00</IssueTime>
      <Remark>案件 (2026)沪0105民初8801号 法律服务费</Remark>
    </BasicInformation>
    <IssuItemInformation><ItemName>法律咨询与代理费</ItemName></IssuItemInformation>
  </EInvoiceData>
</EInvoice>
""".encode("utf-8")


def _build_ofd_bytes(invoice_xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("OFD.xml", b"<?xml version='1.0'?><OFD/>")
        zf.writestr("Doc_0/Attachs/original_invoice.xml", invoice_xml)
    return buf.getvalue()


def _build_zip_bundle(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _encrypt_wecom_payload(aes_key_b64_43: str, plaintext: str, corp_id: str) -> str:
    aes_key = base64.b64decode(aes_key_b64_43 + "=")
    iv = aes_key[:16]
    msg_bytes = plaintext.encode("utf-8")
    raw = os.urandom(16) + struct.pack("!I", len(msg_bytes)) + msg_bytes + corp_id.encode("utf-8")
    pad_len = 32 - (len(raw) % 32)
    padded = raw + bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    cipher_bytes = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(cipher_bytes).decode("ascii")


def _login_headers(client: TestClient, username: str, password: str = "LawTeam@2026") -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup_records(
    *,
    case_ids: list[int],
    sms_ids: list[int],
    triage_ids: list[int],
) -> None:
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
        for sid in sms_ids:
            s = session.get(SmsInbound, sid)
            if s is not None:
                session.delete(s)
        for cid in case_ids:
            for model in (AuditLog, Deadline, ScheduleItem, Document, CaseEvent, CaseMember):
                for row in session.exec(select(model).where(model.case_id == cid)).all():
                    session.delete(row)
            c = session.get(Case, cid)
            if c is not None:
                session.delete(c)
        session.commit()


def test_invoice_xml_ofd_and_zip_parsing_plus_auto_archive(
    client: TestClient,
    isolated_cases_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml_meta = invoice_fetcher.parse_invoice_xml_bytes(_SAMPLE_INVOICE_XML)
    assert xml_meta["invoice_number"] == "26312000000012345678"
    assert xml_meta["issue_date"] == "2026-09-18"
    assert xml_meta["total_amount"] == "12800.00"
    assert xml_meta["buyer_name"] == "上海华兴机电工程有限公司"

    ofd_bytes = _build_ofd_bytes(_SAMPLE_INVOICE_XML)
    ofd_meta = invoice_fetcher.parse_invoice_ofd_bytes(ofd_bytes)
    assert ofd_meta["invoice_number"] == "26312000000012345678"
    assert ofd_meta["file_format"] == "ofd"

    # Guard against ZipSlip in ZIP invoice bundle while keeping valid entries
    zip_bundle = _build_zip_bundle(
        {
            "../../evil.pdf": b"%PDF-1.4\nevil",
            "invoice.xml": _SAMPLE_INVOICE_XML,
            "invoice.ofd": ofd_bytes,
        }
    )
    unpacked = invoice_fetcher._unpack_zip_invoice_artifact(zip_bundle)
    assert unpacked is not None
    ext, raw_bytes, merged = unpacked
    assert ext == ".ofd"
    assert raw_bytes == ofd_bytes
    assert merged["invoice_number"] == "26312000000012345678"

    # End-to-end Nuonuo OFD fetch + auto-archive into 05_财务与发票/ preserving .ofd suffix
    headers = _login_headers(client, "lead_lawyer")
    case_ids: list[int] = []
    sms_ids: list[int] = []
    triage_ids: list[int] = []
    try:
        case_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "华兴机电承揽合同纠纷",
                "case_number": "（2026）沪0105民初8801号",
                "case_category": "civil",
                "client_name": "上海华兴机电工程有限公司",
            },
        )
        assert case_resp.status_code == 201, case_resp.text
        case_id = case_resp.json()["case"]["id"]
        case_ids.append(case_id)

        async def mock_pdf_none(url: str) -> bytes | None:
            return None

        async def mock_artifact_ofd(url: str) -> tuple[str, bytes, dict[str, Any]]:
            return ".ofd", ofd_bytes, ofd_meta

        monkeypatch.setattr(invoice_fetcher, "_download_invoice_pdf_direct", mock_pdf_none)
        monkeypatch.setattr(invoice_fetcher, "_download_invoice_artifact_direct", mock_artifact_ofd)

        with Session(engine) as session:
            lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
            assert lead is not None
            token = lead.sms_webhook_token

        wh = client.post(
            f"/api/webhooks/sms/{token}",
            json={
                "content": f"【诺诺网】您有一张电子发票已开具，点击查看 https://nnfp.jss.com.cn/{secrets.token_hex(6)}",
                "dedup_key": f"p0-ofd-{secrets.token_hex(4)}",
            },
        )
        assert wh.status_code == 202, wh.text
        sms_ids.append(wh.json()["sms_id"])
        triage_id = wh.json()["triage_id"]
        triage_ids.append(triage_id)

        import asyncio

        asyncio.run(enrich_and_fetch_triage_item(triage_id))

        inbox_row = client.get("/api/inbox?status=confirmed", headers=headers).json()
        matched = next((x for x in inbox_row if x["id"] == triage_id), None)
        assert matched is not None
        assert matched["auto_archived"] is True
        assert matched["matched_case_id"] == case_id
        assert matched["downloaded_files"]
        archived_rel = matched["downloaded_files"][0]
        assert "/05_财务与发票/" in archived_rel
        assert archived_rel.endswith(".ofd")
        assert (isolated_cases_root / archived_rel).exists()
    finally:
        _cleanup_records(case_ids=case_ids, sms_ids=sms_ids, triage_ids=triage_ids)


def test_court_hbfy_api_and_tier2_auto_fetch_modes(
    client: TestClient,
    isolated_cases_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    # 1. HBFY Tier-1 API activation with batch docs & captcha detection
    async def mock_hbfy_ok(msg: str) -> dict[str, Any]:
        assert msg == "abc123"
        return {
            "data": {
                "isNeedCaptcha": "N",
                "courtName": "武汉市中级人民法院",
                "sendTime": "2026-09-19 09:00:00",
                "docList": [
                    {"docName": "开庭传票", "downloadPath": "/dl/1.pdf"},
                    {"docName": "举证通知书", "downloadPath": "/dl/2.pdf"},
                ],
            }
        }

    async def mock_dl_bytes(url: str, *, headers: dict[str, str] | None = None) -> bytes:
        return b"%PDF-1.4\n" + b"0" * 128

    monkeypatch.setattr(court_fetcher, "_post_hbfy_api", mock_hbfy_ok)
    monkeypatch.setattr(court_fetcher, "_download_url_bytes", mock_dl_bytes)

    res_ok = asyncio.run(
        court_fetcher.fetch_court_documents(
            {
                "court_links": [
                    {
                        "url": "http://dzsd.hbfy.gov.cn/hb/msg=abc123",
                        "domain": "dzsd.hbfy.gov.cn",
                        "tier": 2,
                        "params": {"msg": "abc123"},
                    }
                ],
                "case_number": "（2026）鄂01民初99号",
                "event_date": "2026-09-19",
            },
            batch_key="hbfy_batch_1",
        )
    )
    assert res_ok.status == "fetched"
    assert res_ok.tier_used == "tier1_api"
    assert res_ok.court_name == "武汉市中级人民法院"
    assert len(res_ok.downloaded_files) == 2
    for rel in res_ok.downloaded_files:
        assert (isolated_cases_root / rel).exists()

    # 2. Tier-2 configurable policy via PATCH /api/auth/me (no .env edit needed)
    headers = _login_headers(client, "lead_lawyer")
    with Session(engine) as session:
        orig_mode = session.exec(select(User).where(User.username == "lead_lawyer")).first().court_auto_fetch_mode
    try:
        patch_resp = client.patch(
            "/api/auth/me",
            headers=headers,
            json={"court_auto_fetch_mode": "auto_non_judgment"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["court_auto_fetch_mode"] == "auto_non_judgment"

        # Non-judgment summons passes auto_non_judgment gate
        reason_summons, _ = court_fetcher._check_tier2_fetch_gate(
            domain="jysd.10102368.com",
            auto_fetch_mode="auto_non_judgment",
            confirm_sign=False,
            phone_last4="8966",
            id_card_last4=None,
            extraction_code=None,
            sms_text="四川高院向您送达开庭传票",
        )
        assert reason_summons is None

        # Judgment SMS is blocked under auto_non_judgment, allowed under auto_all
        reason_judgment, _ = court_fetcher._check_tier2_fetch_gate(
            domain="jysd.10102368.com",
            auto_fetch_mode="auto_non_judgment",
            confirm_sign=False,
            phone_last4="8966",
            id_card_last4=None,
            extraction_code=None,
            sms_text="向您送达民事判决书，请点击查收",
        )
        assert reason_judgment is not None and "非裁决" in reason_judgment

        reason_all, _ = court_fetcher._check_tier2_fetch_gate(
            domain="jysd.10102368.com",
            auto_fetch_mode="auto_all",
            confirm_sign=False,
            phone_last4="8966",
            id_card_last4=None,
            extraction_code=None,
            sms_text="向您送达民事判决书，请点击查收",
        )
        assert reason_all is None
    finally:
        client.patch(
            "/api/auth/me",
            headers=headers,
            json={"court_auto_fetch_mode": orig_mode or ""},
        )


def test_wecom_callback_aes_verification_and_quick_action_loop(
    client: TestClient,
    isolated_cases_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aes_key_43 = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode("ascii").rstrip("=")
    cb_token = "lawteam_cb_token_2026"
    corp_id = "ww_lawteam_test"
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", cb_token)
    monkeypatch.setenv("WECOM_ENCODING_AES_KEY", aes_key_43)
    monkeypatch.setenv("WECOM_CORP_ID", corp_id)
    get_settings.cache_clear()

    headers = _login_headers(client, "lead_lawyer")
    case_ids: list[int] = []
    sms_ids: list[int] = []
    triage_ids: list[int] = []

    with Session(engine) as session:
        lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
        assert lead is not None
        orig_wecom_id = lead.wecom_userid
        lead.wecom_userid = "lawyer_zhang"
        session.add(lead)
        session.commit()
        lead_id = lead.id
        webhook_tok = lead.sms_webhook_token

    try:
        # 1. GET URL verification challenge
        enc_echo = _encrypt_wecom_payload(aes_key_43, "echo_challenge_ok", corp_id)
        ts, nonce = "1730000000", "nonce123"
        sig = compute_wecom_signature(cb_token, ts, nonce, enc_echo)
        echo_resp = client.get(
            "/api/webhooks/wecom/callback",
            params={"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": enc_echo},
        )
        assert echo_resp.status_code == 200
        assert echo_resp.text == "echo_challenge_ok"

        bad_echo = client.get(
            "/api/webhooks/wecom/callback",
            params={"msg_signature": "bad", "timestamp": ts, "nonce": nonce, "echostr": enc_echo},
        )
        assert bad_echo.status_code == 403

        # 2. Seed a case & pending triage item, then confirm via encrypted WeCom callback
        case_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "企微回调确认验证案",
                "case_number": "（2026）京0101民初555号",
                "case_category": "civil",
                "client_name": "北京测试科技有限公司",
            },
        )
        assert case_resp.status_code == 201
        case_id = case_resp.json()["case"]["id"]
        case_ids.append(case_id)

        with Session(engine) as session:
            sms = SmsInbound(
                receiver_user_id=lead_id,
                client_platform="ios_shortcut",
                content="【北京朝阳法院】请查收补充通知文书，无明确规则覆盖",
                content_hash=secrets.token_hex(16),
                dedup_key=f"p0-cb-{secrets.token_hex(6)}",
                sms_category="court_notice",
                received_at=datetime.now(timezone.utc),
            )
            session.add(sms)
            session.flush()
            sms_ids.append(sms.id)

            triage1 = InboxTriageItem(
                sms_id=sms.id,
                owner_user_id=lead_id,
                matched_case_id=case_id,
                item_type="judicial_stage_and_doc",
                status=TriageStatus.PENDING,
                extracted_payload={
                    "case_number": "（2026）京0101民初555号",
                    "doc_title_hint": "补充告知书",
                    "event_type_guess": "custom_notice_without_rule",
                    "event_date": "2026-09-20",
                },
            )
            session.add(triage1)
            session.commit()
            session.refresh(triage1)
            triage_ids.append(triage1.id)

            # Verify interactive alert card builds quick action links & buttons
            sweep = push_pending_confirm_alert(session, triage1)
            assert sweep.sent_count == 1

        inner_xml = (
            "<xml>"
            "<ToUserName>ww_lawteam_test</ToUserName>"
            "<FromUserName>lawyer_zhang</FromUserName>"
            "<Event>template_card_event</Event>"
            f"<EventKey>triage:confirm:{triage_ids[0]}:{case_id}</EventKey>"
            "</xml>"
        )
        enc_event = _encrypt_wecom_payload(aes_key_43, inner_xml, corp_id)
        outer_xml = f"<xml><Encrypt>{enc_event}</Encrypt></xml>"
        ev_sig = compute_wecom_signature(cb_token, ts, nonce, enc_event)
        cb_resp = client.post(
            "/api/webhooks/wecom/callback",
            params={"msg_signature": ev_sig, "timestamp": ts, "nonce": nonce},
            content=outer_xml,
            headers={"Content-Type": "application/xml"},
        )
        assert cb_resp.status_code == 200, cb_resp.text
        assert cb_resp.json()["ok"] is True
        assert cb_resp.json()["status"] == "confirmed"
        assert cb_resp.json()["case_id"] == case_id

        with Session(engine) as session:
            t1_after = session.get(InboxTriageItem, triage_ids[0])
            assert t1_after is not None and t1_after.status == TriageStatus.CONFIRMED
            logs = session.exec(
                select(AuditLog).where(
                    AuditLog.entity_type == "inbox_triage_item",
                    AuditLog.entity_id == triage_ids[0],
                )
            ).all()
            assert any(entry.action == "inbox.confirm" for entry in logs)

        # 3. Signed one-tap quick-action link for IGNORE
        wh2 = client.post(
            f"/api/webhooks/sms/{webhook_tok}",
            json={"content": "待忽略通知短信测试", "dedup_key": f"p0-ign-{secrets.token_hex(6)}"},
        )
        assert wh2.status_code == 202
        sms_ids.append(wh2.json()["sms_id"])
        triage2_id = wh2.json()["triage_id"]
        triage_ids.append(triage2_id)

        tok = create_triage_action_token(user_id=lead_id, triage_id=triage2_id, action="ignore")
        act_resp = client.get(f"/api/webhooks/triage-action?token={tok}&format=json")
        assert act_resp.status_code == 200, act_resp.text
        assert act_resp.json()["status"] == "ignored"
    finally:
        with Session(engine) as session:
            lead = session.get(User, lead_id)
            if lead is not None:
                lead.wecom_userid = orig_wecom_id
                session.add(lead)
                session.commit()
        _cleanup_records(case_ids=case_ids, sms_ids=sms_ids, triage_ids=triage_ids)
        get_settings.cache_clear()
