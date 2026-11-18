from __future__ import annotations

from datetime import date
from pathlib import Path

import chinese_calendar
import icalendar
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
    HolidayRollMode,
    InboxTriageItem,
    ScheduleItem,
    TriageStatus,
)
from app.services.deadline_engine import add_months_clamped, calculate_due_date


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


def _cleanup_cases_and_orphans(
    case_ids: list[int],
    schedule_ids: list[int] | None = None,
    triage_ids: list[int] | None = None,
) -> None:
    with Session(engine) as session:
        for tid in triage_ids or []:
            row = session.get(InboxTriageItem, tid)
            if row is not None:
                session.delete(row)
        for sid in schedule_ids or []:
            row = session.get(ScheduleItem, sid)
            if row is not None:
                session.delete(row)
        for cid in case_ids:
            for model in (InboxTriageItem, AuditLog, Deadline, ScheduleItem, Document, CaseEvent, CaseMember):
                if model is InboxTriageItem:
                    rows = session.exec(select(model).where(model.matched_case_id == cid)).all()
                else:
                    rows = session.exec(select(model).where(model.case_id == cid)).all()
                for r in rows:
                    session.delete(r)
            case = session.get(Case, cid)
            if case is not None:
                session.delete(case)
        session.commit()


def test_deterministic_date_math_holidays_makeup_and_clamping():
    # 1. National Day 2026-10-01 roll forward to first official workday
    nat_calc = calculate_due_date(
        event_date=date(2026, 9, 16),
        duration_value=15,
        duration_unit="days",
        holiday_roll_mode=HolidayRollMode.ROLL_FORWARD_WORKDAY,
    )
    assert nat_calc.raw_due_date == date(2026, 10, 1)
    assert nat_calc.due_date > date(2026, 10, 1)
    assert chinese_calendar.is_workday(nat_calc.due_date) is True
    assert nat_calc.roll_reason and "顺延" in nat_calc.roll_reason

    # 2. Spring Festival 2026 make-up workday (2026-02-14 Saturday) MUST NOT roll forward
    makeup_calc = calculate_due_date(
        event_date=date(2026, 1, 30),
        duration_value=15,
        duration_unit="days",
        holiday_roll_mode=HolidayRollMode.ROLL_FORWARD_WORKDAY,
    )
    assert makeup_calc.raw_due_date == date(2026, 2, 14)
    assert makeup_calc.due_date == date(2026, 2, 14)
    assert makeup_calc.roll_reason and "补班" in makeup_calc.roll_reason

    # 3. Month-end clamping: Jan 31 + 1m -> Feb 28 (non-leap) / Feb 29 (leap)
    assert add_months_clamped(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months_clamped(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months_clamped(date(2026, 1, 31), 3) == date(2026, 4, 30)

    # 4. Public announcement service (announcement_30) shifts base_date by +30d
    ann_calc = calculate_due_date(
        event_date=date(2026, 5, 1),
        duration_value=15,
        duration_unit="days",
        holiday_roll_mode=HolidayRollMode.CALENDAR_EXACT,
        service_method="announcement_30",
    )
    assert ann_calc.base_date == date(2026, 5, 31)
    assert ann_calc.raw_due_date == date(2026, 6, 15)
    assert ann_calc.due_date == date(2026, 6, 15)


def test_procedure_isolation_foreign_criminal_and_authority_days(client: TestClient):
    headers = _login(client, "lead_lawyer")
    created_ids: list[int] = []
    try:
        # 1. Foreign-related civil case -> matches 30-day defense, NOT 15-day domestic defense
        c_foreign = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "涉外合同纠纷案",
                "case_category": "civil",
                "client_name": "海外贸易公司",
                "is_foreign_related": True,
            },
        )
        assert c_foreign.status_code == 201, c_foreign.text
        foreign_id = c_foreign.json()["case"]["id"]
        created_ids.append(foreign_id)

        ev_pleading = client.post(
            f"/api/cases/{foreign_id}/events",
            headers=headers,
            json={"event_type": "pleading_served", "event_date": "2026-08-03"},
        )
        assert ev_pleading.status_code == 201, ev_pleading.text
        codes = {d["rule_code"] for d in ev_pleading.json()["derived_deadlines"]}
        assert "CIVIL_DEFENSE_FOREIGN_30D" in codes
        assert "CIVIL_DEFENSE_15D" not in codes

        # Authority-specified 20-day evidence period overrides 15-day default
        ev_evidence = client.post(
            f"/api/cases/{foreign_id}/events",
            headers=headers,
            json={
                "event_type": "evidence_notice_served",
                "event_date": "2026-08-03",
                "authority_specified_days": 20,
            },
        )
        assert ev_evidence.status_code == 201, ev_evidence.text
        ev_deadlines = ev_evidence.json()["derived_deadlines"]
        assert len(ev_deadlines) == 1
        assert ev_deadlines[0]["rule_code"] == "CIVIL_EVIDENCE"
        assert ev_deadlines[0]["raw_due_date"] == "2026-08-23"

        # 2. Criminal case (一般犯罪, 在押): detention_notified on 2026-09-24
        # +7d = 2026-10-01 MUST NOT roll forward; appeal (+10d = 2026-10-01) MUST roll forward
        c_crim = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "张三涉嫌盗窃罪案",
                "case_category": "criminal",
                "procedure_subtype": "侦查",
                "client_name": "张三",
                "custody_status": "在押",
                "crime_type": "一般犯罪",
            },
        )
        assert c_crim.status_code == 201, c_crim.text
        crim_id = c_crim.json()["case"]["id"]
        created_ids.append(crim_id)

        ev_det = client.post(
            f"/api/cases/{crim_id}/events",
            headers=headers,
            json={"event_type": "detention_notified", "event_date": "2026-09-24"},
        )
        assert ev_det.status_code == 201, ev_det.text
        det_by_code = {d["rule_code"]: d for d in ev_det.json()["derived_deadlines"]}
        assert "CRIM_DETENTION_SUBMIT_ARREST_7D" in det_by_code
        assert "CRIM_DETENTION_GOLDEN_37D" in det_by_code
        assert "CRIM_DETENTION_SUBMIT_ARREST_30D" not in det_by_code
        assert det_by_code["CRIM_DETENTION_SUBMIT_ARREST_7D"]["due_date"] == "2026-10-01"

        ev_crim_judg = client.post(
            f"/api/cases/{crim_id}/events",
            headers=headers,
            json={"event_type": "judgment_served", "event_date": "2026-09-21"},
        )
        assert ev_crim_judg.status_code == 201, ev_crim_judg.text
        crim_appeal = ev_crim_judg.json()["derived_deadlines"][0]
        assert crim_appeal["rule_code"] == "CRIM_APPEAL_JUDGMENT_10D"
        assert crim_appeal["raw_due_date"] == "2026-10-01"
        assert date.fromisoformat(crim_appeal["due_date"]) > date(2026, 10, 1)

        # 3. Commercial arbitration award -> ARB_SET_ASIDE_3M with month-end clamping
        c_arb = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "股权回购仲裁案",
                "case_category": "commercial_arb",
                "client_name": "某投资基金",
            },
        )
        assert c_arb.status_code == 201, c_arb.text
        arb_id = c_arb.json()["case"]["id"]
        created_ids.append(arb_id)

        ev_award = client.post(
            f"/api/cases/{arb_id}/events",
            headers=headers,
            json={"event_type": "arbitration_award_served", "event_date": "2025-11-30"},
        )
        assert ev_award.status_code == 201, ev_award.text
        arb_dl = ev_award.json()["derived_deadlines"][0]
        assert arb_dl["rule_code"] == "ARB_SET_ASIDE_3M"
        assert arb_dl["raw_due_date"] == "2026-02-28"
    finally:
        _cleanup_cases_and_orphans(created_ids)


def test_manual_override_protection_and_audit_log(client: TestClient):
    headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=headers).json()
    created_ids: list[int] = []
    try:
        c_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": "死线人工锁定测试案",
                "case_category": "civil",
                "client_name": "测试客户",
            },
        )
        assert c_resp.status_code == 201
        case_id = c_resp.json()["case"]["id"]
        created_ids.append(case_id)

        ev_resp = client.post(
            f"/api/cases/{case_id}/events",
            headers=headers,
            json={"event_type": "judgment_served", "event_date": "2026-08-03"},
        )
        assert ev_resp.status_code == 201
        event_id = ev_resp.json()["event"]["id"]
        dl_id = ev_resp.json()["derived_deadlines"][0]["id"]
        assert ev_resp.json()["derived_deadlines"][0]["due_date"] == "2026-08-18"

        # Manually override due_date & attach personal_custom_alerts
        patch_resp = client.patch(
            f"/api/deadlines/{dl_id}",
            headers=headers,
            json={
                "due_date": "2026-08-25",
                "override_reason": "法官书面同意延长上诉提交核验期",
                "personal_custom_alerts": [
                    {"user_id": me["id"], "fire_at": "2026-08-24T20:00:00+08:00"}
                ],
            },
        )
        assert patch_resp.status_code == 200, patch_resp.text
        patched = patch_resp.json()
        assert patched["due_date"] == "2026-08-25"
        assert patched["is_manual_override"] is True
        assert patched["override_by"] == me["id"]
        assert len(patched["personal_custom_alerts"]) == 1

        # Re-run deterministic recalculation -> overridden deadline MUST NOT be overwritten
        recalc_resp = client.post(
            f"/api/cases/{case_id}/events/{event_id}/recalculate",
            headers=headers,
        )
        assert recalc_resp.status_code == 200, recalc_resp.text
        recalc_dl = recalc_resp.json()["derived_deadlines"][0]
        assert recalc_dl["due_date"] == "2026-08-25"
        assert recalc_dl["is_manual_override"] is True

        # Verify AuditLog captured deadline.override
        with Session(engine) as session:
            audits = session.exec(
                select(AuditLog).where(
                    AuditLog.case_id == case_id,
                    AuditLog.action == "deadline.override",
                )
            ).all()
            assert len(audits) == 1
            assert audits[0].detail["new_due_date"] == "2026-08-25"
    finally:
        _cleanup_cases_and_orphans(created_ids)


def test_calendar_scope_conflicts_tentative_and_ics_feed(client: TestClient):
    lawyer_headers = _login(client, "lead_lawyer")
    admin_headers = _login(client, "partner_admin")
    lawyer_me = client.get("/api/auth/me", headers=lawyer_headers).json()

    case_ids: list[int] = []
    sched_ids: list[int] = []
    triage_ids: list[int] = []

    try:
        # Case A: TEAM owned by lead_lawyer
        c_team = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "团队协同日历案A",
                "case_number": "(2026)粤0304民初101号",
                "case_category": "civil",
                "visibility": "team",
                "client_name": "甲公司",
            },
        ).json()["case"]
        case_ids.append(c_team["id"])

        # Case B: PRIVATE owned by lead_lawyer
        c_priv = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "私密日历案B",
                "case_category": "civil",
                "visibility": "private",
                "client_name": "私密客户",
            },
        ).json()["case"]
        case_ids.append(c_priv["id"])

        # Case C: TEAM owned by partner_admin (lead_lawyer NOT a member)
        c_admin = client.post(
            "/api/cases",
            headers=admin_headers,
            json={
                "case_title": "合伙人团队案C",
                "case_category": "administrative",
                "visibility": "team",
                "client_name": "行政相对人",
            },
        ).json()["case"]
        case_ids.append(c_admin["id"])

        # Custom deadlines on each case
        dl_a = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": c_team["id"],
                "title": "提交补充代理词",
                "due_date": "2026-11-25",
                "risk_level": "critical",
            },
        )
        assert dl_a.status_code == 201
        dl_c = client.post(
            "/api/deadlines",
            headers=admin_headers,
            json={
                "case_id": c_admin["id"],
                "title": "行政复议提交截止",
                "due_date": "2026-11-26",
                "risk_level": "high",
            },
        )
        assert dl_c.status_code == 201

        # Overlapping schedules for lead_lawyer on 2026-11-24 morning
        s1 = client.post(
            "/api/schedules",
            headers=lawyer_headers,
            json={
                "case_id": c_team["id"],
                "category": "hearing",
                "title": "福田法院第三法庭开庭",
                "start_time": "2026-11-24T09:30:00+08:00",
                "end_time": "2026-11-24T11:00:00+08:00",
                "location": "福田法院第三法庭",
            },
        )
        assert s1.status_code == 201, s1.text
        sched_ids.append(s1.json()["id"])

        s2 = client.post(
            "/api/schedules",
            headers=lawyer_headers,
            json={
                "case_id": c_priv["id"],
                "category": "meeting",
                "title": "当事人证据核对会谈",
                "start_time": "2026-11-24T10:30:00+08:00",
                "end_time": "2026-11-24T11:30:00+08:00",
            },
        )
        assert s2.status_code == 201, s2.text
        sched_ids.append(s2.json()["id"])

        # Insert tentative PENDING InboxTriageItem directly for calendar merge verification
        with Session(engine) as session:
            triage = InboxTriageItem(
                owner_user_id=lawyer_me["id"],
                matched_case_id=c_team["id"],
                item_type="judicial_stage_and_doc",
                status=TriageStatus.PENDING,
                extracted_payload={
                    "title": "法院短信排期谈话",
                    "hearing_time": "2026-11-27T14:30:00+08:00",
                    "court_room": "第五调解室",
                },
            )
            session.add(triage)
            session.commit()
            session.refresh(triage)
            assert triage.id is not None
            triage_ids.append(triage.id)

        # scope=my for lead_lawyer: includes Case A/B + tentative triage + conflict, NOT Case C
        my_cal = client.get(
            "/api/calendar/events",
            headers=lawyer_headers,
            params={"scope": "my", "start": "2026-11-20", "end": "2026-11-30"},
        )
        assert my_cal.status_code == 200, my_cal.text
        my_data = my_cal.json()
        my_titles = {item["title"] for item in my_data["items"]}
        assert "提交补充代理词" in my_titles
        assert "行政复议提交截止" not in my_titles
        assert any(item["is_tentative"] is True and "待确认" in item["title"] for item in my_data["items"])
        assert len(my_data["conflicts"]) == 1
        assert my_data["conflicts"][0]["user_id"] == lawyer_me["id"]

        # scope=team for partner_admin: includes Case A & Case C, NEVER lead_lawyer's PRIVATE Case B
        admin_team = client.get(
            "/api/calendar/events",
            headers=admin_headers,
            params={"scope": "team", "start": "2026-11-20", "end": "2026-11-30"},
        )
        assert admin_team.status_code == 200
        admin_titles = {item["title"] for item in admin_team.json()["items"]}
        assert "提交补充代理词" in admin_titles
        assert "行政复议提交截止" in admin_titles
        assert "当事人证据核对会谈" not in admin_titles

        # ICS subscription feed without Bearer token
        ics_resp = client.get(f"/api/calendar/ics/{lawyer_me['ics_feed_token']}.ics")
        assert ics_resp.status_code == 200
        assert "text/calendar" in ics_resp.headers["content-type"]
        parsed_cal = icalendar.Calendar.from_ical(ics_resp.content)
        comp_names = [comp.name for comp in parsed_cal.subcomponents]
        assert "VEVENT" in comp_names
        assert "VTODO" in comp_names
        summaries = [str(comp.get("SUMMARY", "")) for comp in parsed_cal.subcomponents]
        assert any("提交补充代理词" in s for s in summaries)
        assert any("福田法院第三法庭开庭" in s for s in summaries)
    finally:
        _cleanup_cases_and_orphans(case_ids, sched_ids, triage_ids)
