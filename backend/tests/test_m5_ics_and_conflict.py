from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.database import engine
from app.main import app
from app.models.entities import (
    Case,
    CaseCategory,
    CaseMember,
    CaseMemberRole,
    CaseVisibility,
    NotifyTargetType,
    ScheduleItem,
    User,
)


def _login(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": "LawTeam@2026"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_ics_has_vtimezone_dtstamp_and_by_role_conflict_detection() -> None:
    created_cases: list[int] = []
    created_schedules: list[int] = []

    with Session(engine) as session:
        lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
        admin = session.exec(select(User).where(User.username == "partner_admin")).first()
        assert lead is not None and lead.id is not None
        assert admin is not None and admin.id is not None

        case = Case(
            case_title="M5 日程冲突与ICS规范测试",
            case_number=f"（2099）M5字{secrets.randbelow(9000)}号",
            case_category=CaseCategory.CIVIL,
            procedure_subtype="first_instance",
            current_stage="first_instance",
            visibility=CaseVisibility.TEAM,
            owner_id=admin.id,
            client_name="测试客户",
            folder_rel_path=f"test/m5_{secrets.token_hex(4)}",
        )
        session.add(case)
        session.commit()
        session.refresh(case)
        assert case.id is not None
        created_cases.append(case.id)

        session.add(
            CaseMember(case_id=case.id, user_id=lead.id, role_in_case=CaseMemberRole.LEAD)
        )
        start_t = datetime(2099, 10, 15, 2, 0, tzinfo=timezone.utc)
        end_t = start_t + timedelta(hours=2)

        # Schedule A owned by lead (SELF), Schedule B owned by admin with BY_ROLE -> both involve lead!
        s_a = ScheduleItem(
            case_id=case.id,
            owner_id=lead.id,
            category="hearing",
            title="M5 主办律师开庭A",
            start_time=start_t,
            end_time=end_t,
            notify_target_type=NotifyTargetType.SELF,
        )
        s_b = ScheduleItem(
            case_id=case.id,
            owner_id=admin.id,
            category="hearing",
            title="M5 团队按角色开庭B",
            start_time=start_t + timedelta(minutes=30),
            end_time=end_t + timedelta(minutes=30),
            notify_target_type=NotifyTargetType.BY_ROLE,
        )
        session.add(s_a)
        session.add(s_b)
        session.commit()
        session.refresh(s_a)
        session.refresh(s_b)
        assert s_a.id is not None and s_b.id is not None
        created_schedules.extend([s_a.id, s_b.id])
        lead_id = lead.id
        ics_token = lead.ics_feed_token

    client = TestClient(app)
    try:
        lead_hdr = _login(client, "lead_lawyer")
        cal_resp = client.get("/api/calendar/events?scope=my", headers=lead_hdr)
        assert cal_resp.status_code == 200
        conflicts = cal_resp.json()["conflicts"]
        assert any(
            c["user_id"] == lead_id
            and {c["schedule_id_a"], c["schedule_id_b"]} == set(created_schedules)
            for c in conflicts
        )

        ics_resp = client.get(f"/api/calendar/ics/{ics_token}.ics?scope=my")
        assert ics_resp.status_code == 200
        body = ics_resp.text
        assert "BEGIN:VTIMEZONE" in body
        assert "TZID:Asia/Shanghai" in body
        assert "DTSTAMP:" in body
    finally:
        with Session(engine) as session:
            for sid in created_schedules:
                row = session.get(ScheduleItem, sid)
                if row is not None:
                    session.delete(row)
            for cid in created_cases:
                for m in session.exec(select(CaseMember).where(CaseMember.case_id == cid)).all():
                    session.delete(m)
                c_row = session.get(Case, cid)
                if c_row is not None:
                    session.delete(c_row)
            session.commit()
