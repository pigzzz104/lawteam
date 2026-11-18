from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.core.seed import bootstrap_database
from app.database import engine
from app.main import app
from app.models.entities import (
    AuditLog,
    Case,
    CaseEvent,
    CaseMember,
    Deadline,
    DeadlineRule,
    Document,
    ScheduleItem,
    User,
)
from app.services import file_bridge


@pytest.fixture
def isolated_cases_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CASES_ROOT to a temporary directory for filesystem tests."""
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
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _cleanup_case(case_id: int) -> None:
    with Session(engine) as session:
        for model in (AuditLog, Deadline, ScheduleItem, Document, CaseEvent, CaseMember):
            rows = session.exec(select(model).where(model.case_id == case_id)).all()
            for r in rows:
                session.delete(r)
        case = session.get(Case, case_id)
        if case is not None:
            session.delete(case)
        session.commit()


def test_seed_idempotency_and_demo_users(client: TestClient):
    resp = client.get("/api/rules/stage-templates", headers=_login(client, "lead_lawyer"))
    assert resp.status_code == 200
    assert "criminal" in resp.json()["categories"]

    with Session(engine) as session:
        bootstrap_database(session)
        users = session.exec(select(User)).all()
        usernames = {u.username for u in users}
        assert {"partner_admin", "lead_lawyer", "legal_assistant"}.issubset(usernames)
        rules = session.exec(select(DeadlineRule)).all()
        assert len(rules) >= 34


def test_auth_me_and_profile_update(client: TestClient):
    headers = _login(client, "lead_lawyer")
    me_resp = client.get("/api/auth/me", headers=headers)
    assert me_resp.status_code == 200
    me = me_resp.json()
    assert me["username"] == "lead_lawyer"
    assert me["system_role"] == "lawyer"
    old_sms_token = me["sms_webhook_token"]

    patch_resp = client.patch(
        "/api/auth/me",
        headers=headers,
        json={"phone_last4": "7788", "rotate_sms_token": True},
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["phone_last4"] == "7788"
    assert updated["sms_webhook_token"] != old_sms_token


def test_rules_rbac_and_custom_crud(client: TestClient):
    lawyer_headers = _login(client, "lead_lawyer")
    admin_headers = _login(client, "partner_admin")

    # Lawyer cannot create custom rules
    forbidden = client.post(
        "/api/rules",
        headers=lawyer_headers,
        json={
            "rule_code": f"TEST_FORBIDDEN_{secrets.token_hex(3)}",
            "name": "测试禁用创建",
            "case_category": "civil",
            "trigger_event_type": "judgment_served",
            "duration_value": 10,
            "legal_basis": "test",
        },
    )
    assert forbidden.status_code == 403

    # Admin creates and edits a custom rule, then deletes it
    custom_code = f"CUSTOM_TEST_{secrets.token_hex(3).upper()}"
    created = client.post(
        "/api/rules",
        headers=admin_headers,
        json={
            "rule_code": custom_code,
            "name": "自定义保全续封提示",
            "case_category": "enforcement",
            "trigger_event_type": "seizure_executed",
            "duration_value": 12,
            "duration_unit": "months",
            "holiday_roll_mode": "calendar_exact",
            "legal_basis": "民诉法解释第485条",
            "risk_level": "critical",
            "default_offsets_days": [30, 15, 7, 1],
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]

    toggled = client.patch(
        f"/api/rules/{rule_id}",
        headers=admin_headers,
        json={"is_enabled": False},
    )
    assert toggled.status_code == 200
    assert toggled.json()["is_enabled"] is False

    # Cannot delete built-in rules
    builtin_rules = client.get("/api/rules", headers=admin_headers).json()
    builtin_id = next(r["id"] for r in builtin_rules if r["is_system_builtin"])
    del_builtin = client.delete(f"/api/rules/{builtin_id}", headers=admin_headers)
    assert del_builtin.status_code == 400

    # Can delete custom rules
    del_custom = client.delete(f"/api/rules/{rule_id}", headers=admin_headers)
    assert del_custom.status_code == 204


def test_case_visibility_rbac_and_file_bridge(client: TestClient, isolated_cases_root: Path):
    lawyer_headers = _login(client, "lead_lawyer")
    admin_headers = _login(client, "partner_admin")
    assistant_headers = _login(client, "legal_assistant")

    # 1. Lead lawyer creates a PRIVATE criminal case
    priv_resp = client.post(
        "/api/cases",
        headers=lawyer_headers,
        json={
            "case_title": "张三涉嫌非法经营案(私密)",
            "case_number": "(2026)粤0304刑初101号",
            "case_category": "criminal",
            "visibility": "private",
            "client_name": "张三",
            "custody_status": "在押",
            "custody_place": "深圳市福田区看守所",
            "crime_type": "一般犯罪",
            "trial_mode": "普通程序",
            "handling_agency": "福田区人民检察院",
        },
    )
    assert priv_resp.status_code == 201, priv_resp.text
    priv_id = priv_resp.json()["case"]["id"]
    priv_folder = priv_resp.json()["case"]["folder_rel_path"]

    try:
        # Verify 5 subfolders exist on disk
        case_dir = isolated_cases_root / priv_folder
        assert case_dir.is_dir()
        for sub in file_bridge.STANDARD_CASE_SUBFOLDERS:
            assert (case_dir / sub).is_dir()

        # Admin & assistant CANNOT see or open PRIVATE case
        assert client.get(f"/api/cases/{priv_id}", headers=admin_headers).status_code == 403
        assert client.get(f"/api/cases/{priv_id}", headers=assistant_headers).status_code == 403
        admin_case_ids = {c["id"] for c in client.get("/api/cases", headers=admin_headers).json()}
        assert priv_id not in admin_case_ids
    finally:
        _cleanup_case(priv_id)

    # 2. Lead lawyer creates a TEAM civil case
    team_resp = client.post(
        "/api/cases",
        headers=lawyer_headers,
        json={
            "case_title": "某科技公司买卖合同纠纷",
            "case_number": "(2026)粤0304民初888号",
            "case_category": "civil",
            "visibility": "team",
            "client_name": "某科技公司",
            "is_foreign_related": True,
        },
    )
    assert team_resp.status_code == 201, team_resp.text
    team_id = team_resp.json()["case"]["id"]

    try:
        # Admin can view TEAM case; unassigned assistant gets 403
        assert client.get(f"/api/cases/{team_id}", headers=admin_headers).status_code == 200
        assert client.get(f"/api/cases/{team_id}", headers=assistant_headers).status_code == 403

        # Assign legal_assistant to the TEAM case
        users = client.get("/api/users", headers=lawyer_headers).json()
        by_name = {u["username"]: u["id"] for u in users}
        assign = client.put(
            f"/api/cases/{team_id}/members",
            headers=lawyer_headers,
            json=[
                {"user_id": by_name["lead_lawyer"], "role_in_case": "lead"},
                {"user_id": by_name["legal_assistant"], "role_in_case": "co"},
                {"user_id": by_name["partner_admin"], "role_in_case": "supervisor"},
            ],
        )
        assert assign.status_code == 200
        assert client.get(f"/api/cases/{team_id}", headers=assistant_headers).status_code == 200

        # Upload & collision-safe download inside subfolder
        up1 = client.post(
            f"/api/cases/{team_id}/files/upload",
            headers=assistant_headers,
            data={"subfolder": "01_司法与程序文书", "doc_subtype": "应诉通知书"},
            files={"file": ("notice.pdf", b"%PDF-1.4 sample-1", "application/pdf")},
        )
        assert up1.status_code == 201, up1.text
        up2 = client.post(
            f"/api/cases/{team_id}/files/upload",
            headers=assistant_headers,
            data={"subfolder": "01_司法与程序文书"},
            files={"file": ("notice.pdf", b"%PDF-1.4 sample-2", "application/pdf")},
        )
        assert up2.status_code == 201
        assert up2.json()["file_name"] == "notice (2).pdf"

        dl = client.get(
            f"/api/cases/{team_id}/files/download",
            headers=lawyer_headers,
            params={"rel_path": up1.json()["rel_file_path"]},
        )
        assert dl.status_code == 200
        assert dl.content == b"%PDF-1.4 sample-1"

        # Path traversal attempt must fail
        trav = client.get(
            f"/api/cases/{team_id}/files/download",
            headers=lawyer_headers,
            params={"rel_path": "../../etc/passwd"},
        )
        assert trav.status_code == 403

        # Stage progression & CaseEvent recording
        stage_upd = client.patch(
            f"/api/cases/{team_id}",
            headers=lawyer_headers,
            json={"current_stage": "举证答辩"},
        )
        assert stage_upd.status_code == 200
        assert stage_upd.json()["case"]["current_stage"] == "举证答辩"

        ev = client.post(
            f"/api/cases/{team_id}/events",
            headers=lawyer_headers,
            json={
                "event_type": "pleading_served",
                "event_date": "2026-11-17",
                "service_method": "electronic",
                "notes": "法院电子送达起诉状副本",
            },
        )
        assert ev.status_code == 201, ev.text
        assert ev.json()["event"]["event_type"] == "pleading_served"
    finally:
        _cleanup_case(team_id)


def test_file_bridge_security_guards(isolated_cases_root: Path):
    with pytest.raises(file_bridge.PathSecurityError):
        file_bridge.resolve_safe_path("../outside")
    with pytest.raises(file_bridge.PathSecurityError):
        file_bridge.resolve_safe_path("/tmp/abs")
