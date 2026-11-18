from __future__ import annotations

from datetime import datetime, timezone
import secrets

from fastapi.testclient import TestClient
import pytest
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
    InboxTriageItem,
    ReminderDelivery,
    ScheduleItem,
    SmsInbound,
    User,
)
from app.services.sms_parser import parse_sms_regex, suggest_stage_for_event


def _login(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": "LawTeam@2026"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup(
    *,
    case_ids: list[int] | None = None,
    triage_ids: list[int] | None = None,
    sms_ids: list[int] | None = None,
    schedule_ids: list[int] | None = None,
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
                item = session.get(InboxTriageItem, tid)
                if item is not None:
                    session.delete(item)
        if sms_ids:
            for sid in sms_ids:
                sms = session.get(SmsInbound, sid)
                if sms is not None:
                    session.delete(sms)
        if schedule_ids:
            for scid in schedule_ids:
                sc = session.get(ScheduleItem, scid)
                if sc is not None:
                    session.delete(sc)
        if case_ids:
            for cid in case_ids:
                for dl in session.exec(select(Deadline).where(Deadline.case_id == cid)).all():
                    session.delete(dl)
                for ev in session.exec(select(CaseEvent).where(CaseEvent.case_id == cid)).all():
                    session.delete(ev)
                for sc in session.exec(select(ScheduleItem).where(ScheduleItem.case_id == cid)).all():
                    session.delete(sc)
                for m in session.exec(select(CaseMember).where(CaseMember.case_id == cid)).all():
                    session.delete(m)
                for log in session.exec(select(AuditLog).where(AuditLog.case_id == cid)).all():
                    session.delete(log)
                c = session.get(Case, cid)
                if c is not None:
                    session.delete(c)
        session.commit()


def test_prod_jwt_secret_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.is_default_jwt_secret() is True
    finally:
        get_settings.cache_clear()


def test_wecom_callback_requires_token_and_rejects_plaintext(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WECOM_CALLBACK_TOKEN", "")
    monkeypatch.setenv("WECOM_ENCODING_AES_KEY", "")
    get_settings.cache_clear()
    try:
        resp_no_tok = client.post(
            "/api/webhooks/wecom/callback",
            content="<xml><FromUserName>zhang_partner</FromUserName><EventKey>triage:ignore:1</EventKey></xml>",
        )
        assert resp_no_tok.status_code == 503

        monkeypatch.setenv("WECOM_CALLBACK_TOKEN", "test-cb-token")
        monkeypatch.setenv("WECOM_ENCODING_AES_KEY", "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")
        get_settings.cache_clear()
        from app.services.wecom_callback import compute_wecom_signature

        sig = compute_wecom_signature("test-cb-token", "1700000000", "nonce1", "")
        resp_plain = client.post(
            "/api/webhooks/wecom/callback",
            params={"msg_signature": sig, "timestamp": "1700000000", "nonce": "nonce1"},
            content="<xml><FromUserName>zhang_partner</FromUserName><EventKey>triage:ignore:1</EventKey></xml>",
        )
        assert resp_plain.status_code == 400
    finally:
        get_settings.cache_clear()


def test_private_case_isolation_and_rbac_hardening(client: TestClient) -> None:
    admin_headers = _login(client, "partner_admin")
    lawyer_headers = _login(client, "lead_lawyer")
    assist_headers = _login(client, "legal_assistant")

    case_ids: list[int] = []
    triage_ids: list[int] = []
    sms_ids: list[int] = []
    schedule_ids: list[int] = []

    try:
        # 1. Cannot add other members to PRIVATE case
        priv_bad = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "私密案禁成员",
                "case_category": "civil",
                "visibility": "private",
                "client_name": "私密客户",
                "members": [{"user_id": 1, "role_in_case": "co"}],
            },
        )
        assert priv_bad.status_code == 400

        priv_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "律师私密案件M1",
                "case_number": "（2026）沪01民初9911号",
                "case_category": "civil",
                "visibility": "private",
                "client_name": "保密委托人甲",
            },
        )
        assert priv_resp.status_code == 201
        priv_case_id = priv_resp.json()["case"]["id"]
        case_ids.append(priv_case_id)

        # 2. SMS matching lead_lawyer's PRIVATE case must NOT be visible to ADMIN in inbox
        lawyer_me = client.get("/api/auth/me", headers=lawyer_headers).json()
        with Session(engine) as session:
            u = session.get(User, lawyer_me["id"])
            assert u is not None
            webhook_tok = u.sms_webhook_token
        wh = client.post(
            f"/api/webhooks/sms/{webhook_tok}",
            json={
                "content": "【上海法院】请查收（2026）沪01民初9911号案件材料",
                "dedup_key": f"m1-priv-{secrets.token_hex(6)}",
            },
        )
        assert wh.status_code == 202
        triage_id = wh.json()["triage_id"]
        triage_ids.append(triage_id)
        sms_ids.append(wh.json()["sms_id"])

        admin_inbox = client.get("/api/inbox", headers=admin_headers)
        assert admin_inbox.status_code == 200
        assert all(item["id"] != triage_id for item in admin_inbox.json())

        admin_get_triage = client.get(
            f"/api/inbox/{triage_id}/files/download?rel_path=missing",
            headers=admin_headers,
        )
        assert admin_get_triage.status_code in (403, 404)

        # 3. Co-counsel cannot change TEAM case visibility or reopen triage
        team_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "团队权限测试案",
                "case_category": "civil",
                "visibility": "team",
                "client_name": "团队客户",
                "members": [{"user_id": 3, "role_in_case": "co"}],
            },
        )
        assert team_resp.status_code == 201
        team_case_id = team_resp.json()["case"]["id"]
        case_ids.append(team_case_id)

        co_flip = client.patch(
            f"/api/cases/{team_case_id}",
            headers=assist_headers,
            json={"visibility": "private"},
        )
        assert co_flip.status_code == 403

        # 4. ADMIN cannot edit or delete lawyer's personal schedule (case_id=None)
        sched_resp = client.post(
            "/api/schedules",
            headers=lawyer_headers,
            json={
                "title": "律师私人行程",
                "category": "task",
                "start_time": "2026-11-25T10:00:00+08:00",
                "end_time": "2026-11-25T11:00:00+08:00",
            },
        )
        assert sched_resp.status_code == 201
        sched_id = sched_resp.json()["id"]
        schedule_ids.append(sched_id)

        assert (
            client.patch(
                f"/api/schedules/{sched_id}",
                headers=admin_headers,
                json={"title": "越权修改"},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/schedules/{sched_id}",
                headers=admin_headers,
            ).status_code
            == 403
        )

        # 5. Non-admin cannot see colleague notify_target; admin cannot demote self
        users_as_lawyer = client.get("/api/users", headers=lawyer_headers).json()
        admin_row = next(u for u in users_as_lawyer if u["username"] == "partner_admin")
        assert admin_row["notify_target"] is None

        admin_me = client.get("/api/auth/me", headers=admin_headers).json()
        demote_self = client.patch(
            f"/api/users/{admin_me['id']}",
            headers=admin_headers,
            json={"system_role": "lawyer"},
        )
        assert demote_self.status_code == 400

        # 6. Reopen invalidates prior quick-action links
        confirm_resp = client.post(
            f"/api/inbox/{triage_id}/confirm",
            headers=lawyer_headers,
            json={"case_id": priv_case_id},
        )
        assert confirm_resp.status_code == 200
        old_tok = create_triage_action_token(
            user_id=lawyer_me["id"], triage_id=triage_id, action="ignore", action_epoch=0
        )
        assert client.post(f"/api/inbox/{triage_id}/reopen", headers=lawyer_headers).status_code == 200
        replay = client.post(f"/api/webhooks/triage-action?token={old_tok}&format=json")
        assert replay.status_code == 409
    finally:
        _cleanup(
            case_ids=case_ids,
            triage_ids=triage_ids,
            sms_ids=sms_ids,
            schedule_ids=schedule_ids,
        )


def test_sms_parsing_foreign_ruling_and_rule_cleanup(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=lawyer_headers).json()
    case_ids: list[int] = []
    sms_ids: list[int] = []
    triage_ids: list[int] = []
    orig_secret: str | None = None

    try:
        # 1. Plain-text SMS containing multi-param URL must NOT trigger 422 form parser
        with Session(engine) as session:
            u = session.get(User, me["id"])
            assert u is not None
            webhook_tok = u.sms_webhook_token
            orig_secret = u.sms_webhook_secret
            u.sms_webhook_secret = "my-secret"
            session.add(u)
            session.commit()

        # Non-ASCII signature in JSON sign field returns 401 cleanly (never 500 TypeError)
        bad_sig = client.post(
            f"/api/webhooks/sms/{webhook_tok}",
            json={"content": "测试", "sign": "非ASCII签名"},
        )
        assert bad_sig.status_code == 401

        with Session(engine) as session:
            u = session.get(User, me["id"])
            assert u is not None
            u.sms_webhook_secret = None
            session.add(u)
            session.commit()

        plain_url_sms = client.post(
            f"/api/webhooks/sms/{webhook_tok}",
            content="【上海法院】请查阅 https://zxfw.court.gov.cn/zxfw/#/wssd?qdbh=1&sdbh=2&sdsin=3",
            headers={"Content-Type": "text/plain"},
        )
        assert plain_url_sms.status_code == 202
        triage_ids.append(plain_url_sms.json()["triage_id"])
        sms_ids.append(plain_url_sms.json()["sms_id"])

        # 2. Future hearing date in SMS body must NOT become event_date
        parsed_hearing = parse_sms_regex(
            "【上海一中院】案号（2026）沪01民终6604号，定于2026年12月20日上午9时开庭。",
            received_at=datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc),
        )
        assert parsed_hearing["event_date"] == "2026-11-18"

        # 3. Foreign civil ruling 30d + pruning obsolete deadlines when is_foreign_related toggles
        c_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "涉外裁定联动案",
                "case_category": "civil",
                "procedure_subtype": "一审",
                "client_name": "海外公司",
                "is_foreign_related": True,
            },
        )
        assert c_resp.status_code == 201
        cid = c_resp.json()["case"]["id"]
        case_ids.append(cid)

        ev_resp = client.post(
            f"/api/cases/{cid}/events",
            headers=lawyer_headers,
            json={"event_type": "ruling_served", "event_date": "2026-08-03"},
        )
        assert ev_resp.status_code == 201
        dls = ev_resp.json()["derived_deadlines"]
        assert [d["rule_code"] for d in dls] == ["CIVIL_APPEAL_FOREIGN_RULING_30D"]

        # Toggle to domestic -> prunes 30d foreign rule and creates 10d domestic rule
        patch_resp = client.patch(
            f"/api/cases/{cid}",
            headers=lawyer_headers,
            json={"is_foreign_related": False},
        )
        assert patch_resp.status_code == 200
        after_codes = [d["rule_code"] for d in patch_resp.json()["deadlines"]]
        assert after_codes == ["CIVIL_APPEAL_RULING_10D"]

        # 4. Monotonic stage advancement respects procedure_subtype
        assert (
            suggest_stage_for_event(
                "civil",
                "hearing_scheduled",
                procedure_subtype="二审",
                current_stage="裁判生效",
            )
            is None
        )
        assert (
            suggest_stage_for_event(
                "civil",
                "hearing_scheduled",
                procedure_subtype="二审",
                current_stage="已立案",
            )
            == "开庭审理"
        )

        # 5. Criminal case without explicit crime_type defaults to 一般犯罪 and matches detention rules
        crim_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "刑案默认类型测试",
                "case_category": "criminal",
                "client_name": "李某",
                "custody_status": "在押",
            },
        )
        assert crim_resp.status_code == 201
        crim_id = crim_resp.json()["case"]["id"]
        case_ids.append(crim_id)
        assert crim_resp.json()["case"]["crime_type"] == "一般犯罪"

        det_ev = client.post(
            f"/api/cases/{crim_id}/events",
            headers=lawyer_headers,
            json={"event_type": "detention_notified", "event_date": "2026-09-24"},
        )
        assert det_ev.status_code == 201
        det_codes = {d["rule_code"] for d in det_ev.json()["derived_deadlines"]}
        assert "CRIM_DETENTION_SUBMIT_ARREST_3D" in det_codes
        assert "CRIM_DETENTION_SUBMIT_ARREST_7D" in det_codes
    finally:
        with Session(engine) as session:
            u = session.get(User, me["id"])
            if u is not None:
                u.sms_webhook_secret = orig_secret
                session.add(u)
                session.commit()
        _cleanup(case_ids=case_ids, triage_ids=triage_ids, sms_ids=sms_ids)
