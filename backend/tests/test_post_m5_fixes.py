from __future__ import annotations

from datetime import date, datetime, timezone
import secrets
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

from app.core.security import create_triage_action_token
from app.core.url_safety import UnsafeUrlError, validate_external_fetch_url, validate_webhook_url
from app.database import engine
from app.models.entities import (
    AuditLog,
    Case,
    CaseCategory,
    CaseEvent,
    CaseMember,
    Deadline,
    DeadlineRule,
    Document,
    HolidayRollMode,
    InboxTriageItem,
    ReminderDelivery,
    RiskLevel,
    ScheduleItem,
    SmsInbound,
    TriageStatus,
    User,
)
from app.services import reminder_scheduler
from app.services.deadline_engine import derive_deadlines_for_event, rule_matches_case
from app.services.reminder_scheduler import evaluate_due_reminders
from app.services.sms_parser import parse_sms_regex

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


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
    deadline_ids: list[int] | None = None,
    rule_ids: list[int] | None = None,
) -> None:
    with Session(engine) as session:
        if deadline_ids:
            for did in deadline_ids:
                for d in session.exec(
                    select(ReminderDelivery).where(
                        ReminderDelivery.source_type == "deadline",
                        ReminderDelivery.source_id == did,
                    )
                ).all():
                    session.delete(d)
                dl = session.get(Deadline, did)
                if dl is not None:
                    session.delete(dl)
        if schedule_ids:
            for scid in schedule_ids:
                for d in session.exec(
                    select(ReminderDelivery).where(
                        ReminderDelivery.source_type == "schedule",
                        ReminderDelivery.source_id == scid,
                    )
                ).all():
                    session.delete(d)
                sc = session.get(ScheduleItem, scid)
                if sc is not None:
                    session.delete(sc)
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
            session.flush()
        if case_ids:
            for cid in case_ids:
                for it in session.exec(
                    select(InboxTriageItem).where(InboxTriageItem.matched_case_id == cid)
                ).all():
                    it.matched_case_id = None
                    session.add(it)
                session.flush()
                for dl in session.exec(select(Deadline).where(Deadline.case_id == cid)).all():
                    if dl.id is not None:
                        for d in session.exec(
                            select(ReminderDelivery).where(
                                ReminderDelivery.source_type == "deadline",
                                ReminderDelivery.source_id == dl.id,
                            )
                        ).all():
                            session.delete(d)
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
            session.flush()
        if sms_ids:
            for sid in sms_ids:
                s = session.get(SmsInbound, sid)
                if s is not None:
                    session.delete(s)
        if rule_ids:
            for rid in rule_ids:
                r = session.get(DeadlineRule, rid)
                if r is not None:
                    session.delete(r)
        session.commit()


def test_jwt_triage_token_rejected_on_api_endpoints(client: TestClient) -> None:
    admin_headers = _login(client, "partner_admin")
    me = client.get("/api/auth/me", headers=admin_headers).json()
    triage_tok = create_triage_action_token(
        user_id=me["id"],
        triage_id=999999,
        action="confirm",
    )
    bad_headers = {"Authorization": f"Bearer {triage_tok}"}
    assert client.get("/api/auth/me", headers=bad_headers).status_code == 401
    assert client.get("/api/cases", headers=bad_headers).status_code == 401


def test_nullable_field_clearing_and_webhook_ssrf_channel_switch(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    admin_headers = _login(client, "partner_admin")
    orig_me = client.get("/api/auth/me", headers=lawyer_headers).json()
    try:
        # Set secret & phone_last4, then clear them with null
        p1 = client.patch(
            "/api/auth/me",
            headers=lawyer_headers,
            json={
                "sms_webhook_secret": "temp_secret_123",
                "phone_last4": "1234",
                "id_card_last4": "5678",
                "notify_channel": "wecom",
                "notify_target": "http://127.0.0.1:9000/hook",
            },
        )
        assert p1.status_code == 200, p1.text
        assert p1.json()["has_sms_webhook_secret"] is True
        assert p1.json()["phone_last4"] == "1234"

        # Switching notify_channel to webhook without resending unsafe notify_target must fail
        p_ssrf = client.patch(
            "/api/auth/me",
            headers=lawyer_headers,
            json={"notify_channel": "webhook"},
        )
        assert p_ssrf.status_code == 400

        # Clearing nullable fields with null must actually persist None
        p_clear = client.patch(
            "/api/auth/me",
            headers=lawyer_headers,
            json={
                "sms_webhook_secret": None,
                "phone_last4": None,
                "id_card_last4": None,
                "notify_target": None,
            },
        )
        assert p_clear.status_code == 200, p_clear.text
        body = p_clear.json()
        assert body["has_sms_webhook_secret"] is False
        assert body["phone_last4"] is None
        assert body["id_card_last4"] is None
        assert body["notify_target"] is None

        # Admin endpoint must also validate webhook URLs
        users = client.get("/api/users", headers=admin_headers).json()
        lead_uid = next(u["id"] for u in users if u["username"] == "lead_lawyer")
        bad_admin_patch = client.patch(
            f"/api/users/{lead_uid}",
            headers=admin_headers,
            json={"notify_channel": "webhook", "notify_target": "http://100.100.100.200/latest"},
        )
        assert bad_admin_patch.status_code == 400
    finally:
        client.patch(
            "/api/auth/me",
            headers=lawyer_headers,
            json={
                "notify_channel": orig_me["notify_channel"],
                "notify_target": orig_me["notify_target"],
                "phone_last4": orig_me["phone_last4"],
                "id_card_last4": orig_me["id_card_last4"],
                "sms_webhook_secret": None,
            },
        )


def test_inbox_confirm_preserves_criminal_crime_type_and_charge_name(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    case_ids: list[int] = []
    triage_ids: list[int] = []
    sms_ids: list[int] = []
    try:
        sim = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": "【上海市公安局浦东分局】通知：嫌疑人赵某已于2026-11-10被刑事拘留，现羁押于浦东新区看守所。",
                "sender": "110",
                "auto_fetch": False,
            },
        )
        assert sim.status_code == 201, sim.text
        triage = sim.json()
        triage_ids.append(triage["id"])
        if triage["sms_id"]:
            sms_ids.append(triage["sms_id"])
        assert triage["status"] == TriageStatus.PENDING.value

        confirm = client.post(
            f"/api/inbox/{triage['id']}/confirm",
            headers=lawyer_headers,
            json={
                "create_case": {
                    "case_title": "赵某涉嫌流窜盗窃案",
                    "case_category": "criminal",
                    "client_name": "赵某",
                    "crime_type": "流窜/多次/结伙作案",
                    "charge_name": "盗窃罪",
                },
                "event_type": "detention_notified",
                "event_date": "2026-11-10",
            },
        )
        assert confirm.status_code == 200, confirm.text
        cid = confirm.json()["case_id"]
        case_ids.append(cid)

        detail = client.get(f"/api/cases/{cid}", headers=lawyer_headers).json()
        assert detail["case"]["crime_type"] == "流窜/多次/结伙作案"
        assert detail["case"]["charge_name"] == "盗窃罪"
        rule_codes = {d["rule_code"] for d in detail["deadlines"]}
        assert "CRIM_DETENTION_SUBMIT_ARREST_30D" in rule_codes
        assert "CRIM_DETENTION_GOLDEN_37D" in rule_codes
        assert "CRIM_DETENTION_SUBMIT_ARREST_7D" not in rule_codes
    finally:
        _cleanup(case_ids=case_ids, triage_ids=triage_ids, sms_ids=sms_ids)


def test_deliveries_endpoint_handles_bare_triage_alert_without_500(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=lawyer_headers).json()
    triage_ids: list[int] = []
    try:
        with Session(engine) as session:
            item = InboxTriageItem(
                owner_user_id=me["id"],
                item_type="judicial_stage_and_doc",
                status=TriageStatus.MANUAL_FETCH_REQUIRED,
                extracted_payload={},
            )
            session.add(item)
            session.commit()
            session.refresh(item)
            assert item.id is not None
            triage_ids.append(item.id)

            deliv = ReminderDelivery(
                idempotency_key=f"test_bare_triage:{item.id}:{secrets.token_hex(4)}",
                source_type="triage_alert",
                source_id=item.id,
                recipient_user_ids=[me["id"]],
                scheduled_at=datetime.now(timezone.utc),
                status="sent",
            )
            session.add(deliv)
            session.commit()

        resp = client.get("/api/reminders/deliveries?limit=20", headers=lawyer_headers)
        assert resp.status_code == 200, resp.text
        matched = [d for d in resp.json() if d["source_type"] == "triage_alert" and d["source_id"] == triage_ids[0]]
        assert matched
        assert matched[0]["source_title"] == "待确认分拣通知"
    finally:
        _cleanup(triage_ids=triage_ids)


def test_archived_case_sms_never_auto_archives(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    case_ids: list[int] = []
    triage_ids: list[int] = []
    sms_ids: list[int] = []
    try:
        case_no = f"（2026）沪01民初{secrets.randbelow(89999) + 10000}号"
        c_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "已归档测试案件",
                "case_number": case_no,
                "case_category": "civil",
                "client_name": "归档测试客户",
            },
        )
        assert c_resp.status_code == 201
        cid = c_resp.json()["case"]["id"]
        case_ids.append(cid)

        arch = client.patch(
            f"/api/cases/{cid}",
            headers=lawyer_headers,
            json={"is_archived": True},
        )
        assert arch.status_code == 200

        sim = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": f"【上海法院】请查收{case_no}民事判决书。",
                "sender": "12368",
                "auto_fetch": False,
            },
        )
        assert sim.status_code == 201, sim.text
        item = sim.json()
        triage_ids.append(item["id"])
        if item["sms_id"]:
            sms_ids.append(item["sms_id"])
        assert item["status"] == TriageStatus.PENDING.value
        assert item["auto_archived"] is False
        assert "已归档" in (item["triage_reason"] or "")
    finally:
        _cleanup(case_ids=case_ids, triage_ids=triage_ids, sms_ids=sms_ids)


def test_private_transition_strips_personal_alerts_and_scheduler_blocks_them(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    assist_headers = _login(client, "legal_assistant")
    users = {u["username"]: u["id"] for u in client.get("/api/users", headers=lawyer_headers).json()}
    lead_id = users["lead_lawyer"]
    assist_id = users["legal_assistant"]
    case_ids: list[int] = []
    try:
        c_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "团队转私密闹钟清理案",
                "case_category": "civil",
                "visibility": "team",
                "client_name": "测试方",
                "members": [
                    {"user_id": lead_id, "role_in_case": "lead"},
                    {"user_id": assist_id, "role_in_case": "co"},
                ],
            },
        )
        assert c_resp.status_code == 201
        cid = c_resp.json()["case"]["id"]
        case_ids.append(cid)

        dl_resp = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": cid,
                "title": "私密隔离测试期限",
                "due_date": "2026-12-28",
                "risk_level": "high",
                "reminder_offsets_days": [],
            },
        )
        assert dl_resp.status_code == 201
        dl_id = dl_resp.json()["id"]

        fire_iso = datetime(2026, 12, 26, 10, 0, tzinfo=SHANGHAI_TZ).isoformat()
        assert (
            client.post(
                f"/api/reminders/deadlines/{dl_id}/personal-alerts",
                headers=assist_headers,
                json={"fire_at": fire_iso},
            ).status_code
            == 200
        )

        # Transition case to PRIVATE -> assistant's personal alert must be stripped
        priv_resp = client.patch(
            f"/api/cases/{cid}",
            headers=lawyer_headers,
            json={"visibility": "private"},
        )
        assert priv_resp.status_code == 200
        with Session(engine) as session:
            dl_db = session.get(Deadline, dl_id)
            assert dl_db is not None
            assert all(a.get("user_id") == lead_id for a in (dl_db.personal_custom_alerts or []))
            # Even if a stale alert exists in JSON, scheduler must skip non-owner for PRIVATE case
            dl_db.personal_custom_alerts = [{"user_id": assist_id, "fire_at": fire_iso}]
            session.add(dl_db)
            session.commit()

            summary = evaluate_due_reminders(
                session,
                now=datetime(2026, 12, 26, 10, 5, tzinfo=SHANGHAI_TZ),
            )
            assert not any(f"deadline_personal:{dl_id}:u{assist_id}" in k for k in summary.delivery_keys)
    finally:
        _cleanup(case_ids=case_ids)


def test_patch_schedule_does_not_shift_naive_utc_db_times_by_8h(client: TestClient) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    me = client.get("/api/auth/me", headers=lawyer_headers).json()
    sched_ids: list[int] = []
    try:
        start_utc = datetime(2099, 11, 20, 1, 30, tzinfo=timezone.utc)  # 09:30 Shanghai
        end_utc = datetime(2099, 11, 20, 3, 0, tzinfo=timezone.utc)    # 11:00 Shanghai
        r = client.post(
            "/api/schedules",
            headers=lawyer_headers,
            json={
                "category": "meeting",
                "title": "无时区漂移校验会议",
                "start_time": start_utc.isoformat(),
                "end_time": end_utc.isoformat(),
                "notify_target_type": "self",
            },
        )
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        sched_ids.append(sid)

        # Patch only is_completed without touching start_time/end_time
        patched = client.patch(
            f"/api/schedules/{sid}",
            headers=lawyer_headers,
            json={"is_completed": True},
        )
        assert patched.status_code == 200, patched.text
        with Session(engine) as session:
            row = session.get(ScheduleItem, sid)
            assert row is not None
            db_start = row.start_time.replace(tzinfo=timezone.utc) if row.start_time.tzinfo is None else row.start_time.astimezone(timezone.utc)
            db_end = row.end_time.replace(tzinfo=timezone.utc) if row.end_time.tzinfo is None else row.end_time.astimezone(timezone.utc)
            assert db_start == start_utc
            assert db_end == end_utc
            assert row.owner_id == me["id"]
    finally:
        _cleanup(schedule_ids=sched_ids)


def test_ssrf_blocks_alibaba_metadata_and_cgnat() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_external_fetch_url("http://100.100.100.200/latest/meta-data/")
    with pytest.raises(UnsafeUrlError):
        validate_external_fetch_url("http://100.64.0.1/internal")
    with pytest.raises(UnsafeUrlError):
        validate_webhook_url("http://100.100.100.200/hook")


def test_forwarded_sms_header_not_parsed_as_hearing_time() -> None:
    content = (
        "张晨律师 2026年06月12日 17:03 "
        "【上海市第一中级人民法院】请查收（2026）沪01民初888号民事判决书，点击查阅。"
    )
    parsed = parse_sms_regex(content)
    assert parsed["event_type_guess"] == "judgment_served"
    assert parsed["hearing_time"] is None


def test_priority_redesign_custom_deadline_escalation_ics_and_authority_specifiable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lawyer_headers = _login(client, "lead_lawyer")
    users = {u["username"]: u["id"] for u in client.get("/api/users", headers=lawyer_headers).json()}
    lead_id = users["lead_lawyer"]
    assist_id = users["legal_assistant"]
    admin_id = users["partner_admin"]

    with Session(engine) as session:
        orig_user_channels: dict[int, tuple[str, str | None]] = {}
        for uid in (lead_id, assist_id, admin_id):
            u = session.get(User, uid)
            assert u is not None
            orig_user_channels[uid] = (u.notify_channel, u.notify_target)
            u.notify_channel = "wecom"
            if not u.wecom_userid:
                u.wecom_userid = u.username
            session.add(u)
        session.commit()

    case_ids: list[int] = []
    rule_ids: list[int] = []
    try:
        c_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": "重要程度与指定期限测试案",
                "case_category": "civil",
                "client_name": "优先权测试客户",
                "members": [
                    {"user_id": lead_id, "role_in_case": "lead"},
                    {"user_id": admin_id, "role_in_case": "supervisor"},
                ],
            },
        )
        assert c_resp.status_code == 201
        cid = c_resp.json()["case"]["id"]
        case_ids.append(cid)

        # 1) Custom HIGH deadline with explicit escalate_to_supervisor=True and SELF notify_owner_id=assist_id
        dl_high = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": cid,
                "title": "重要自定义补证",
                "due_date": "2026-12-20",
                "risk_level": "high",
                "notify_target_type": "case_team",
                "escalate_to_supervisor": True,
                "reminder_offsets_days": [1],
            },
        )
        assert dl_high.status_code == 201, dl_high.text
        dl_high_id = dl_high.json()["id"]
        assert dl_high.json()["escalate_to_supervisor"] is True

        dl_self = client.post(
            "/api/deadlines",
            headers=lawyer_headers,
            json={
                "case_id": cid,
                "title": "指定助理单独提醒",
                "due_date": "2026-12-22",
                "risk_level": "normal",
                "notify_target_type": "self",
                "notify_owner_id": assist_id,
                "reminder_offsets_days": [0],
            },
        )
        assert dl_self.status_code == 201, dl_self.text
        assert dl_self.json()["notify_owner_id"] == assist_id
        assert dl_self.json()["escalate_to_supervisor"] is False

        # Trigger reminder sweep at T-1 for dl_high: HIGH deadline with escalate_to_supervisor=True MUST escalate!
        captured_bodies: list[str] = []
        orig_dispatch = reminder_scheduler.dispatch_notification_to_users

        def _spy_dispatch(**kwargs):  # type: ignore[no-untyped-def]
            captured_bodies.append(str(kwargs.get("markdown_body", "")))
            return orig_dispatch(**kwargs)

        monkeypatch.setattr(reminder_scheduler, "dispatch_notification_to_users", _spy_dispatch)

        with Session(engine) as session:
            summary = evaluate_due_reminders(
                session,
                now=datetime(2026, 12, 19, 9, 5, tzinfo=SHANGHAI_TZ),
            )
            assert summary.sent_count == 1
            deliv = session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.idempotency_key == f"deadline:{dl_high_id}:offset:1:2026-12-20"
                )
            ).first()
            assert deliv is not None
            assert set(deliv.recipient_user_ids) == {lead_id, admin_id}
            assert captured_bodies
            assert "（重要）" in captured_bodies[0]
            assert "CRITICAL" not in captured_bodies[0]
            assert "致命死线" not in captured_bodies[0]

        # Check .ics labels use new priority vocabulary (紧急/重要/常规/备忘)
        me = client.get("/api/auth/me", headers=lawyer_headers).json()
        ics_resp = client.get(f"/api/calendar/ics/{me['ics_feed_token']}.ics?scope=my")
        assert ics_resp.status_code == 200
        assert "致命死线" not in ics_resp.text
        assert "[重要]" in ics_resp.text
        assert "[常规]" in ics_resp.text

        # 2) Rule with {"authority_specifiable": True} must match case & apply authority_specified_days
        with Session(engine) as session:
            rule = DeadlineRule(
                rule_code=f"TEST_SPEC_{secrets.token_hex(4).upper()}",
                name="测试指定期限规则",
                case_category=CaseCategory.CIVIL,
                trigger_event_type="pleading_served",
                condition_match={"authority_specifiable": True},
                duration_value=15,
                duration_unit="days",
                holiday_roll_mode=HolidayRollMode.ROLL_FORWARD_WORKDAY,
                legal_basis="测试依据",
                risk_level=RiskLevel.HIGH,
                default_offsets_days=[3, 1, 0],
                is_system_builtin=False,
                is_enabled=True,
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            assert rule.id is not None
            rule_ids.append(rule.id)

            case_obj = session.get(Case, cid)
            assert case_obj is not None
            assert rule_matches_case(rule, case_obj) is True

            ev = CaseEvent(
                case_id=cid,
                event_type="pleading_served",
                event_date=date(2026, 12, 1),
                service_method="electronic",
                authority_specified_days=7,
                created_by=lead_id,
            )
            session.add(ev)
            session.commit()
            session.refresh(ev)

            derived = derive_deadlines_for_event(session, case_obj, ev)
            matched_dl = next(d for d in derived if d.rule_code == rule.rule_code)
            assert matched_dl.raw_due_date == date(2026, 12, 8)
            assert matched_dl.escalate_to_supervisor is False
            session.commit()
    finally:
        _cleanup(case_ids=case_ids, rule_ids=rule_ids)
        with Session(engine) as session:
            for uid, (ch, tgt) in orig_user_channels.items():
                u = session.get(User, uid)
                if u is not None:
                    u.notify_channel = ch
                    u.notify_target = tgt
                    session.add(u)
            session.commit()
