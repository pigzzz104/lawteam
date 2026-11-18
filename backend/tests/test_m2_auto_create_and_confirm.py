from __future__ import annotations

from pathlib import Path
import secrets

from fastapi.testclient import TestClient
import pytest
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
from app.services import court_fetcher
from app.services.sms_parser import parse_sms_regex
from app.services.wecom_callback import execute_triage_quick_action


def _login(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": "LawTeam@2026"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(
    *,
    case_ids: list[int] | None = None,
    triage_ids: list[int] | None = None,
    sms_ids: list[int] | None = None,
) -> None:
    with Session(engine) as session:
        if triage_ids:
            for tid in triage_ids:
                for d in session.exec(
                    select(ReminderDelivery).where(
                        ReminderDelivery.source_type == "triage_alert",
                        ReminderDelivery.source_id == tid,
                    )
                ).all():
                    session.delete(d)
                it = session.get(InboxTriageItem, tid)
                if it is not None:
                    session.delete(it)
        if sms_ids:
            for sid in sms_ids:
                s = session.get(SmsInbound, sid)
                if s is not None:
                    session.delete(s)
        if case_ids:
            for cid in case_ids:
                for dl in session.exec(select(Deadline).where(Deadline.case_id == cid)).all():
                    session.delete(dl)
                for ev in session.exec(select(CaseEvent).where(CaseEvent.case_id == cid)).all():
                    session.delete(ev)
                for sc in session.exec(select(ScheduleItem).where(ScheduleItem.case_id == cid)).all():
                    session.delete(sc)
                for doc in session.exec(select(Document).where(Document.case_id == cid)).all():
                    session.delete(doc)
                for m in session.exec(select(CaseMember).where(CaseMember.case_id == cid)).all():
                    session.delete(m)
                for log in session.exec(select(AuditLog).where(AuditLog.case_id == cid)).all():
                    session.delete(log)
                c = session.get(Case, cid)
                if c is not None:
                    session.delete(c)
        session.commit()


def test_auto_create_case_reopen_cleanup_and_global_dedup(
    client: TestClient, isolated_cases_root: Path
) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    admin_headers = _login(client, "partner_admin")
    case_ids: list[int] = []
    triage_ids: list[int] = []
    sms_ids: list[int] = []

    try:
        # 1. Unmatched judicial SMS with clear case_number auto-creates case + deadline + stage
        sms_no = f"（2026）沪01民初{secrets.randbelow(89999) + 10000}号"
        sim1 = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": f"【上海市浦东新区人民法院】请查收{sms_no}民事判决书，请依法行使上诉权利。",
                "sender": "12368",
                "auto_fetch": False,
            },
        )
        assert sim1.status_code == 201, sim1.text
        t1 = sim1.json()
        triage_ids.append(t1["id"])
        if t1["sms_id"]:
            sms_ids.append(t1["sms_id"])
        assert t1["status"] == TriageStatus.CONFIRMED.value
        assert t1["auto_archived"] is True
        assert t1["extracted_payload"]["auto_created_case"] is True
        auto_case_id = t1["matched_case_id"]
        assert auto_case_id is not None
        case_ids.append(auto_case_id)

        cdetail = client.get(f"/api/cases/{auto_case_id}", headers=lawyer_headers).json()
        assert cdetail["case"]["case_number"] == sms_no
        assert cdetail["case"]["current_stage"] == "裁判与上诉期"
        assert any(d["rule_code"] == "CIVIL_APPEAL_JUDGMENT_15D" for d in cdetail["deadlines"])
        auto_folder = isolated_cases_root / cdetail["case"]["folder_rel_path"]
        assert auto_folder.is_dir()

        # 2. Reopen deletes empty auto-created case & directory cleanly
        reopen_resp = client.post(f"/api/inbox/{t1['id']}/reopen", headers=lawyer_headers)
        assert reopen_resp.status_code == 200, reopen_resp.text
        assert reopen_resp.json()["status"] == TriageStatus.PENDING.value
        assert reopen_resp.json()["matched_case_id"] is None
        assert not auto_folder.exists()
        assert client.get(f"/api/cases/{auto_case_id}", headers=lawyer_headers).status_code == 404

        # 3. Global dedup: private case owned by admin blocks lawyer from auto-creating duplicate
        priv_no = f"（2026）沪01民初{secrets.randbelow(89999) + 10000}号"
        priv_case = client.post(
            "/api/cases",
            headers=admin_headers,
            json={
                "case_title": "合伙人绝密案件",
                "case_number": priv_no,
                "case_category": "civil",
                "visibility": "private",
                "client_name": "绝密客户",
            },
        )
        assert priv_case.status_code == 201
        case_ids.append(priv_case.json()["case"]["id"])

        sim_dup = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": f"【上海法院】请查收{priv_no}民事判决书。",
                "sender": "12368",
                "auto_fetch": False,
            },
        )
        assert sim_dup.status_code == 201
        dup_item = sim_dup.json()
        triage_ids.append(dup_item["id"])
        if dup_item["sms_id"]:
            sms_ids.append(dup_item["sms_id"])
        assert dup_item["status"] == TriageStatus.PENDING.value
        assert dup_item["matched_case_id"] is None
        assert "绝密" not in (dup_item["triage_reason"] or "")

        # 4. Criminal detention notice WITHOUT case_number must NOT auto-create a case
        det_sim = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": "【上海市公安局浦东分局】通知：嫌疑人李某已于2026-11-10被刑事拘留，现羁押于浦东新区看守所。",
                "sender": "110",
                "auto_fetch": False,
            },
        )
        assert det_sim.status_code == 201
        det_item = det_sim.json()
        triage_ids.append(det_item["id"])
        if det_item["sms_id"]:
            sms_ids.append(det_item["sms_id"])
        assert det_item["status"] == TriageStatus.PENDING.value
        assert det_item["matched_case_id"] is None

        # 5. Pagination query on /api/inbox
        paged = client.get("/api/inbox?limit=1&offset=0", headers=lawyer_headers)
        assert paged.status_code == 200
        assert len(paged.json()) == 1
    finally:
        _cleanup(case_ids=case_ids, triage_ids=triage_ids, sms_ids=sms_ids)


def test_wecom_quick_confirm_derives_events_deadlines_and_hearing(
    client: TestClient, isolated_cases_root: Path
) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=lawyer_headers).json()
    case_ids: list[int] = []
    triage_ids: list[int] = []

    try:
        c_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "微信确认完整入案测试案",
                "case_category": "civil",
                "client_name": "测试公司",
            },
        )
        assert c_resp.status_code == 201
        cid = c_resp.json()["case"]["id"]
        case_ids.append(cid)

        with Session(engine) as session:
            actor = session.get(User, me["id"])
            assert actor is not None
            item = InboxTriageItem(
                owner_user_id=actor.id,
                matched_case_id=cid,
                item_type="judicial_stage_and_doc",
                status=TriageStatus.PENDING,
                extracted_payload={
                    "event_type_guess": "judgment_served",
                    "event_date": "2026-08-03",
                    "doc_title_hint": "民事判决书",
                    "hearing_time": "2026-08-10T09:30:00+08:00",
                    "hearing_location": "第一法庭",
                },
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            assert item.id is not None
            triage_ids.append(item.id)

            res = execute_triage_quick_action(
                session, item=item, actor_user=actor, action="confirm", case_id=cid
            )
            assert res["ok"] is True
            assert res["generated_deadline_count"] >= 1
            assert res["created_event_id"] is not None
            assert res["created_schedule_id"] is not None

        detail = client.get(f"/api/cases/{cid}", headers=lawyer_headers).json()
        assert detail["case"]["current_stage"] == "裁判与上诉期"
        assert any(d["rule_code"] == "CIVIL_APPEAL_JUDGMENT_15D" for d in detail["deadlines"])
    finally:
        _cleanup(case_ids=case_ids, triage_ids=triage_ids)


def test_doc_classification_mixed_sms_and_retry_merge(
    client: TestClient, isolated_cases_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. 执行异议裁定书 vs 调查令 vs 混合司法+发票短信
    doc_obj = court_fetcher._extract_court_fields_from_text(
        "执行异议裁定书\n（2026）沪01执异15号", "执行异议裁定书.pdf", has_text=True
    )
    assert doc_obj["doc_event_type"] == "enforcement_objection_served"

    doc_inv = court_fetcher._extract_court_fields_from_text(
        "律师调查令\n（2026）沪01执15号", "调查令.pdf", has_text=True
    )
    assert doc_inv["doc_event_type"] == "document_served"

    mixed = parse_sms_regex(
        "【上海法院】送达文书（退费发票说明），请打开 https://zxfw.court.gov.cn/zxfw/#/pagesAjkj/app/wssd/index?qdbh=1&sdbh=2&sdsin=3"
    )
    assert mixed["sms_category"] == "court_service"
    assert mixed["doc_title_hint"] == "退费发票说明"

    # 2. retry-fetch preserves enriched fields instead of clobbering
    lawyer_headers = _login(client, "lead_lawyer")
    triage_ids: list[int] = []
    sms_ids: list[int] = []
    monkeypatch.setenv("AUTO_ARCHIVE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        sim = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={"content": "【上海法院】请查收案件通知，案号待核", "auto_fetch": False},
        )
        assert sim.status_code == 201
        tid = sim.json()["id"]
        triage_ids.append(tid)
        if sim.json()["sms_id"]:
            sms_ids.append(sim.json()["sms_id"])

        with Session(engine) as session:
            it = session.get(InboxTriageItem, tid)
            assert it is not None
            p = dict(it.extracted_payload or {})
            p["hearing_location"] = "人工补充第三法庭"
            it.extracted_payload = p
            session.add(it)
            session.commit()

        retried = client.post(
            f"/api/inbox/{tid}/retry-fetch",
            headers=lawyer_headers,
            json={"phone_last4": "8966"},
        )
        assert retried.status_code == 200
        assert retried.json()["extracted_payload"]["hearing_location"] == "人工补充第三法庭"
    finally:
        get_settings.cache_clear()
        _cleanup(triage_ids=triage_ids, sms_ids=sms_ids)
