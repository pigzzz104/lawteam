from __future__ import annotations

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import Settings
from app.database import engine
from app.models.entities import (
    CaseMemberRole,
    InboxTriageItem,
    NotifyTargetType,
    ReminderDelivery,
    RiskLevel,
    TriageStatus,
    User,
)
from app.services.reminder_scheduler import evaluate_due_reminders, push_manual_fetch_alert
from app.services.wecom_notifier import clear_wecom_token_cache, dispatch_notification_to_users
from tests.test_phase1 import _cleanup_case, _login

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _cleanup_deliveries_for_sources(source_type: str, source_ids: list[int]) -> None:
    if not source_ids:
        return
    with Session(engine) as session:
        rows = session.exec(
            select(ReminderDelivery).where(
                ReminderDelivery.source_type == source_type,
                ReminderDelivery.source_id.in_(source_ids),  # type: ignore[union-attr]
            )
        ).all()
        for r in rows:
            session.delete(r)
        session.commit()


def _cleanup_triage(triage_ids: list[int]) -> None:
    if not triage_ids:
        return
    _cleanup_deliveries_for_sources("triage_alert", triage_ids)
    with Session(engine) as session:
        rows = session.exec(
            select(InboxTriageItem).where(InboxTriageItem.id.in_(triage_ids))  # type: ignore[union-attr]
        ).all()
        for r in rows:
            session.delete(r)
        session.commit()


def _create_team_case(client: TestClient, headers: dict[str, str], members: list[dict]) -> int:
    resp = client.post(
        "/api/cases",
        headers=headers,
        json={
            "case_title": "Phase4 提醒引擎测试案",
            "case_number": "(2026)沪01民初9999号",
            "case_category": "civil",
            "procedure_subtype": "一审",
            "client_name": "测试委托人",
            "visibility": "team",
            "members": members,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["case"]["id"]


def test_multi_channel_wechat_and_wecom_dispatch(client: TestClient):
    """Verify dry-run and live HTTP dispatch for WeCom + personal WeChat gateways."""
    lawyer_headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=lawyer_headers).json()
    orig_channel = me["notify_channel"]
    orig_target = me["notify_target"]

    try:
        # 1) Configure user to personal ServerChan and verify test-push endpoint (dry-run mode)
        patch = client.patch(
            "/api/auth/me",
            headers=lawyer_headers,
            json={"notify_channel": "serverchan", "notify_target": "SCT123456DEMOKEY"},
        )
        assert patch.status_code == 200, patch.text
        assert patch.json()["notify_channel"] == "serverchan"

        test_resp = client.post("/api/reminders/test-push", headers=lawyer_headers)
        assert test_resp.status_code == 200, test_resp.text
        body = test_resp.json()
        assert body["ok"] is True
        assert body["dry_run"] is True
        assert body["channel"] == "serverchan"
        assert body["attempts"][0]["target_summary"].startswith("SCT1")

        # 2) Live HTTP dispatch across all 5 channels via MockTransport
        captured_calls: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8")) if request.content else {}
            captured_calls.append((str(request.url), payload))
            url = str(request.url)
            if "gettoken" in url:
                return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "access_token": "tok_live_123", "expires_in": 7200})
            if "message/send" in url:
                return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "msgid": "msg_1"})
            if "sctapi.ftqq.com" in url:
                return httpx.Response(200, json={"code": 0, "message": "success"})
            if "wxpusher.zjiecode.com" in url:
                return httpx.Response(200, json={"code": 1000, "success": True, "msg": "处理成功"})
            if "pushplus.plus" in url:
                return httpx.Response(200, json={"code": 200, "msg": "请求成功"})
            if "hooks.example.com" in url:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(404)

        clear_wecom_token_cache()
        live_settings = Settings(
            wecom_corp_id="ww123",
            wecom_agent_id="100001",
            wecom_secret="sec_abc",
            wecom_dry_run=False,
            wxpusher_app_token="AT_APP_TOKEN",
        )
        mock_users = [
            User(id=1, username="u_wecom", full_name="企微律师", hashed_password="x", wecom_userid="zhang_wc", notify_channel="wecom"),
            User(id=2, username="u_sc", full_name="Server酱律师", hashed_password="x", notify_channel="serverchan", notify_target="SCT_KEY_999"),
            User(id=3, username="u_wxp", full_name="WxPusher律师", hashed_password="x", notify_channel="wxpusher", notify_target="UID_abc123"),
            User(id=4, username="u_pp", full_name="PushPlus律师", hashed_password="x", notify_channel="pushplus", notify_target="pp_tok_888"),
            User(id=5, username="u_wh", full_name="Webhook律师", hashed_password="x", notify_channel="webhook", notify_target="https://hooks.example.com/notify"),
        ]
        with httpx.Client(transport=httpx.MockTransport(handler)) as hc:
            res = dispatch_notification_to_users(
                users=mock_users,
                title="开庭提醒",
                markdown_body="明天上午9点开庭",
                settings=live_settings,
                http_client=hc,
            )
            assert res.ok is True
            assert res.dry_run is False
            assert len(res.attempts) == 5

            # Call a second time to verify WeCom access_token caching avoids second gettoken call
            captured_calls.clear()
            res2 = dispatch_notification_to_users(
                users=[mock_users[0]],
                title="二次缓存验证",
                markdown_body="验证 token cache",
                settings=live_settings,
                http_client=hc,
            )
            assert res2.ok is True
            assert not any("gettoken" in url for url, _ in captured_calls)
            assert any("message/send" in url for url, _ in captured_calls)
    finally:
        client.patch(
            "/api/auth/me",
            headers=lawyer_headers,
            json={"notify_channel": orig_channel, "notify_target": orig_target or ""},
        )


def test_notify_target_modes_and_supervisor_escalation_and_idempotency(client: TestClient):
    """Test SELF, CUSTOM_USERS, BY_ROLE, <=1 day CRITICAL SUPERVISOR escalation, and idempotency."""
    lawyer_headers = _login(client, "lead_lawyer")
    users = {u["username"]: u["id"] for u in client.get("/api/users", headers=lawyer_headers).json()}
    lead_id = users["lead_lawyer"]
    assist_id = users["legal_assistant"]
    admin_id = users["partner_admin"]

    case_id = _create_team_case(
        client,
        lawyer_headers,
        [
            {"user_id": lead_id, "role_in_case": CaseMemberRole.LEAD.value},
            {"user_id": assist_id, "role_in_case": CaseMemberRole.CO_COUNSEL.value},
            {"user_id": admin_id, "role_in_case": CaseMemberRole.SUPERVISOR.value},
        ],
    )
    due = date(2026, 12, 20)
    dl_ids: list[int] = []
    try:
        # 1) BY_ROLE deadline with offsets [3, 1] and escalate_to_supervisor=True
        r_role = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": case_id,
                "title": "上诉期届满",
                "due_date": due.isoformat(),
                "risk_level": RiskLevel.CRITICAL.value,
                "notify_target_type": NotifyTargetType.BY_ROLE.value,
                "escalate_to_supervisor": True,
                "reminder_offsets_days": [3, 1],
            },
        )
        assert r_role.status_code == 201, r_role.text
        dl_role_id = r_role.json()["id"]
        dl_ids.append(dl_role_id)

        # 2) SELF deadline (offset 3)
        r_self = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": case_id,
                "title": "个人自设草稿校对",
                "due_date": due.isoformat(),
                "risk_level": RiskLevel.LOW.value,
                "notify_target_type": NotifyTargetType.SELF.value,
                "escalate_to_supervisor": False,
                "reminder_offsets_days": [3],
            },
        )
        assert r_self.status_code == 201, r_self.text
        dl_self_id = r_self.json()["id"]
        dl_ids.append(dl_self_id)

        # 3) CUSTOM_USERS deadline (offset 3 -> only legal_assistant)
        r_custom = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": case_id,
                "title": "助理寄送材料",
                "due_date": due.isoformat(),
                "risk_level": RiskLevel.HIGH.value,
                "notify_target_type": NotifyTargetType.CUSTOM_USERS.value,
                "custom_notify_user_ids": [assist_id],
                "escalate_to_supervisor": False,
                "reminder_offsets_days": [3],
            },
        )
        assert r_custom.status_code == 201, r_custom.text
        dl_custom_id = r_custom.json()["id"]
        dl_ids.append(dl_custom_id)

        # Sweep at T-3 09:05 Shanghai: BY_ROLE should NOT include supervisor yet
        t_minus_3 = datetime(2026, 12, 17, 9, 5, tzinfo=SHANGHAI_TZ)
        with Session(engine) as session:
            s1 = evaluate_due_reminders(session, now=t_minus_3)
            assert s1.sent_count == 3

            # Verify idempotency: running again at same timestamp does not double-send
            s1_repeat = evaluate_due_reminders(session, now=t_minus_3)
            assert s1_repeat.sent_count == 0
            assert s1_repeat.already_processed_count >= 3

            deliv_role_3 = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.idempotency_key == f"deadline:{dl_role_id}:offset:3:{due.isoformat()}"
                )
            ).first()
            assert deliv_role_3 is not None
            assert set(deliv_role_3.recipient_user_ids) == {lead_id, assist_id}
            assert admin_id not in deliv_role_3.recipient_user_ids

            deliv_self_3 = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.idempotency_key == f"deadline:{dl_self_id}:offset:3:{due.isoformat()}"
                )
            ).first()
            assert deliv_self_3 is not None
            assert deliv_self_3.recipient_user_ids == [lead_id]

            deliv_custom_3 = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.idempotency_key == f"deadline:{dl_custom_id}:offset:3:{due.isoformat()}"
                )
            ).first()
            assert deliv_custom_3 is not None
            assert deliv_custom_3.recipient_user_ids == [assist_id]

        # Sweep at T-1 09:05 Shanghai: CRITICAL BY_ROLE <= 1 day MUST escalate to SUPERVISOR!
        t_minus_1 = datetime(2026, 12, 19, 9, 5, tzinfo=SHANGHAI_TZ)
        with Session(engine) as session:
            s2 = evaluate_due_reminders(session, now=t_minus_1)
            assert s2.sent_count == 1
            deliv_role_1 = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.idempotency_key == f"deadline:{dl_role_id}:offset:1:{due.isoformat()}"
                )
            ).first()
            assert deliv_role_1 is not None
            assert set(deliv_role_1.recipient_user_ids) == {lead_id, assist_id, admin_id}
    finally:
        _cleanup_deliveries_for_sources("deadline", dl_ids)
        _cleanup_case(case_id)


def test_personal_custom_alerts_completed_skip_and_schedule_reminders(client: TestClient):
    """Test personal_custom_alerts, skipped_completed ledger, ScheduleItem offsets, and RBAC deliveries."""
    lawyer_headers = _login(client, "lead_lawyer")
    assist_headers = _login(client, "legal_assistant")
    admin_headers = _login(client, "partner_admin")
    users = {u["username"]: u["id"] for u in client.get("/api/users", headers=lawyer_headers).json()}
    lead_id = users["lead_lawyer"]
    assist_id = users["legal_assistant"]

    case_id = _create_team_case(
        client,
        lawyer_headers,
        [
            {"user_id": lead_id, "role_in_case": CaseMemberRole.LEAD.value},
            {"user_id": assist_id, "role_in_case": CaseMemberRole.CO_COUNSEL.value},
        ],
    )
    due = date(2026, 12, 25)
    dl_ids: list[int] = []
    sched_ids: list[int] = []
    try:
        # Active deadline + personal custom alert by legal_assistant
        r_active = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": case_id,
                "title": "举证期限届满",
                "due_date": due.isoformat(),
                "risk_level": RiskLevel.CRITICAL.value,
                "reminder_offsets_days": [],
            },
        )
        assert r_active.status_code == 201
        dl_active_id = r_active.json()["id"]
        dl_ids.append(dl_active_id)

        fire_at = datetime(2026, 12, 21, 20, 0, tzinfo=SHANGHAI_TZ)
        p_resp = client.post(
            f"/api/reminders/deadlines/{dl_active_id}/personal-alerts",
            headers=assist_headers,
            json={"fire_at": fire_at.isoformat()},
        )
        assert p_resp.status_code == 200, p_resp.text
        assert len(p_resp.json()["personal_custom_alerts"]) == 1

        # Completed deadline at offset 0 -> must record skipped_completed instead of sending
        r_done = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": case_id,
                "title": "已缴纳诉讼费",
                "due_date": date(2026, 12, 21).isoformat(),
                "risk_level": RiskLevel.HIGH.value,
                "reminder_offsets_days": [0],
            },
        )
        assert r_done.status_code == 201
        dl_done_id = r_done.json()["id"]
        dl_ids.append(dl_done_id)
        client.patch(f"/api/deadlines/{dl_done_id}", headers=lawyer_headers, json={"is_completed": True})

        # ScheduleItem starting at 2026-12-21 21:30 Shanghai with 120-min reminder (fires at 19:30)
        sched_start = datetime(2026, 12, 21, 21, 30, tzinfo=SHANGHAI_TZ)
        sched_end = datetime(2026, 12, 21, 22, 30, tzinfo=SHANGHAI_TZ)
        r_sched = client.post(
            "/api/schedules",
            headers=lawyer_headers,
            json={
                "case_id": case_id,
                "category": "hearing",
                "title": "夜间庭前辅导",
                "start_time": sched_start.isoformat(),
                "end_time": sched_end.isoformat(),
                "location": "会议室A",
                "notify_target_type": NotifyTargetType.CASE_TEAM.value,
                "reminder_offsets_minutes": [120],
            },
        )
        assert r_sched.status_code == 201, r_sched.text
        sched_id = r_sched.json()["id"]
        sched_ids.append(sched_id)

        sweep_now = datetime(2026, 12, 21, 20, 5, tzinfo=SHANGHAI_TZ)
        with Session(engine) as session:
            summary = evaluate_due_reminders(session, now=sweep_now)
            assert summary.sent_count == 2  # personal alert + schedule 120m
            assert summary.skipped_completed_count == 1  # completed deadline

            dl_personal = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.source_type == "deadline",
                    ReminderDelivery.source_id == dl_active_id,
                )
            ).first()
            assert dl_personal is not None
            assert dl_personal.status == "sent"
            assert dl_personal.recipient_user_ids == [assist_id]

            dl_skipped = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.source_type == "deadline",
                    ReminderDelivery.source_id == dl_done_id,
                )
            ).first()
            assert dl_skipped is not None
            assert dl_skipped.status == "skipped_completed"
            assert dl_skipped.sent_at is None

        # Verify RBAC on GET /api/reminders/deliveries & POST /api/reminders/run-once
        admin_delivs = client.get("/api/reminders/deliveries", headers=admin_headers)
        assert admin_delivs.status_code == 200
        assert len(admin_delivs.json()) >= 3

        assist_delivs = client.get("/api/reminders/deliveries", headers=assist_headers)
        assert assist_delivs.status_code == 200
        assert any(d["source_id"] == dl_active_id for d in assist_delivs.json())

        run_once = client.post("/api/reminders/run-once", headers=lawyer_headers)
        assert run_once.status_code == 200
        assert "sent_count" in run_once.json()
    finally:
        _cleanup_deliveries_for_sources("deadline", dl_ids)
        _cleanup_deliveries_for_sources("schedule", sched_ids)
        _cleanup_case(case_id)


def test_manual_fetch_required_alert_push(client: TestClient):
    """Test immediate push_manual_fetch_alert and idempotency for MANUAL_FETCH_REQUIRED triage items."""
    lawyer_headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=lawyer_headers).json()
    triage_ids: list[int] = []
    try:
        with Session(engine) as session:
            item = InboxTriageItem(
                owner_user_id=me["id"],
                item_type="judicial_stage_and_doc",
                status=TriageStatus.MANUAL_FETCH_REQUIRED,
                extracted_payload={
                    "agency": "上海市浦东新区人民法院",
                    "case_number": "(2026)沪0115民初555号",
                    "court_links": ["https://zxfw.court.gov.cn/demo"],
                },
                error_or_manual_reason="需输入手机号后4位验证码",
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            assert item.id is not None
            triage_ids.append(item.id)

            res1 = push_manual_fetch_alert(session, item)
            assert res1.sent_count == 1

            # Second sweep must be idempotent
            res2 = evaluate_due_reminders(session, now=datetime.now(timezone.utc))
            assert f"triage_manual:{item.id}" not in res2.delivery_keys

            ledger = session.exec(
                select(ReminderDelivery).where(ReminderDelivery.idempotency_key == f"triage_manual:{item.id}")
            ).first()
            assert ledger is not None
            assert ledger.status == "sent"
            assert ledger.recipient_user_ids == [me["id"]]
    finally:
        _cleanup_triage(triage_ids)
