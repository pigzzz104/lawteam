from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import secrets

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.core.url_safety import UnsafeUrlError, safe_async_get, validate_external_fetch_url, validate_webhook_url
from app.database import engine
from app.main import app
from app.models.entities import (
    Case,
    CaseCategory,
    CaseMember,
    CaseVisibility,
    Deadline,
    InboxTriageItem,
    NotifyTargetType,
    ReminderDelivery,
    ScheduleItem,
    SmsInbound,
    SystemRole,
    TriageStatus,
    User,
)
from app.services import court_fetcher, invoice_fetcher, reminder_scheduler
from app.services.reminder_scheduler import (
    evaluate_due_reminders,
    push_manual_fetch_alert,
    resolve_deadline_recipients,
)
from app.services.wecom_notifier import DispatchResult


def _login(client: TestClient, username: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": "LawTeam@2026"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup_m3(
    *,
    case_ids: list[int] | None = None,
    schedule_ids: list[int] | None = None,
    triage_ids: list[int] | None = None,
    sms_ids: list[int] | None = None,
    delivery_keys: list[str] | None = None,
    user_ids: list[int] | None = None,
) -> None:
    with Session(engine) as session:
        if delivery_keys:
            for key in delivery_keys:
                for d in session.exec(
                    select(ReminderDelivery).where(ReminderDelivery.idempotency_key == key)
                ).all():
                    session.delete(d)
        if schedule_ids:
            for sid in schedule_ids:
                for d in session.exec(
                    select(ReminderDelivery).where(
                        ReminderDelivery.source_type == "schedule",
                        ReminderDelivery.source_id == sid,
                    )
                ).all():
                    session.delete(d)
                s = session.get(ScheduleItem, sid)
                if s is not None:
                    session.delete(s)
        if triage_ids:
            for tid in triage_ids:
                for d in session.exec(
                    select(ReminderDelivery).where(
                        ReminderDelivery.source_type == "triage_alert",
                        ReminderDelivery.source_id == tid,
                    )
                ).all():
                    session.delete(d)
                tr = session.get(InboxTriageItem, tid)
                if tr is not None:
                    session.delete(tr)
        if sms_ids:
            for sid in sms_ids:
                sms = session.get(SmsInbound, sid)
                if sms is not None:
                    session.delete(sms)
        if case_ids:
            for cid in case_ids:
                dls = session.exec(select(Deadline).where(Deadline.case_id == cid)).all()
                dl_ids = [dl.id for dl in dls if dl.id is not None]
                if dl_ids:
                    for rd in session.exec(
                        select(ReminderDelivery).where(
                            ReminderDelivery.source_type == "deadline",
                            ReminderDelivery.source_id.in_(dl_ids),  # type: ignore[union-attr]
                        )
                    ).all():
                        session.delete(rd)
                for dl in dls:
                    session.delete(dl)
                for m in session.exec(select(CaseMember).where(CaseMember.case_id == cid)).all():
                    session.delete(m)
                c = session.get(Case, cid)
                if c is not None:
                    session.delete(c)
        if user_ids:
            for uid in user_ids:
                u = session.get(User, uid)
                if u is not None:
                    session.delete(u)
        session.commit()


def test_self_notify_owner_id_and_personal_default_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    created_cases: list[int] = []
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.username == "partner_admin")).first()
        lawyer = session.exec(select(User).where(User.username == "lead_lawyer")).first()
        assert admin is not None and admin.id is not None
        assert lawyer is not None and lawyer.id is not None
        admin_id = admin.id
        lawyer_id = lawyer.id
        orig_offsets = list(lawyer.default_deadline_offsets_days or [])

    dispatched_calls: list[tuple[list[int], str]] = []

    def fake_dispatch(*, users, title, **kwargs):  # type: ignore[no-untyped-def]
        uids = [u.id for u in users if u.id is not None]
        dispatched_calls.append((uids, title))
        return DispatchResult(
            ok=True,
            dry_run=True,
            succeeded_user_ids=uids,
            failed_user_ids=[],
        )

    monkeypatch.setattr(reminder_scheduler, "dispatch_notification_to_users", fake_dispatch)

    try:
        with Session(engine) as session:
            lawyer_row = session.get(User, lawyer_id)
            assert lawyer_row is not None
            lawyer_row.default_deadline_offsets_days = [10, 3]
            session.add(lawyer_row)

            due = date(2099, 6, 20)
            case = Case(
                case_title="M3 测试个人负责与默认追加提醒",
                case_number=f"（2099）M3字{secrets.randbelow(9000)}号",
                case_category=CaseCategory.CIVIL,
                procedure_subtype="first_instance",
                current_stage="first_instance",
                visibility=CaseVisibility.TEAM,
                owner_id=admin_id,
                client_name="测试委托人",
                folder_rel_path=f"test/m3_{secrets.token_hex(4)}",
            )
            session.add(case)
            session.commit()
            session.refresh(case)
            assert case.id is not None
            created_cases.append(case.id)

            dl = Deadline(
                case_id=case.id,
                rule_code="CUSTOM_M3",
                title="M3 自选负责人死线",
                base_date=due,
                due_date=due,
                raw_due_date=due,
                notify_target_type=NotifyTargetType.SELF,
                notify_owner_id=lawyer_id,
                reminder_offsets_days=[3, 1],
            )
            session.add(dl)
            session.commit()
            session.refresh(dl)

            resolved, escalated = resolve_deadline_recipients(session, case, dl, 3)
            assert resolved == [lawyer_id]
            assert escalated is False

            # Sweep at due - 10 days 10:00 Shanghai -> triggers extra personal offset 10 for lawyer only
            sweep_10 = datetime(2099, 6, 10, 10, 0, tzinfo=reminder_scheduler.SHANGHAI_TZ).astimezone(
                timezone.utc
            )
            res_10 = evaluate_due_reminders(session, now=sweep_10)
            assert any(k == f"deadline_pref:{dl.id}:u{lawyer_id}:offset:10:2099-06-20" for k in res_10.delivery_keys)
            assert any(uids == [lawyer_id] and "个人默认期限提醒" in title for uids, title in dispatched_calls)

            # Sweep at due - 3 days 10:00 Shanghai -> fires rule offset [3] ONCE (no duplicate deadline_pref for 3)
            dispatched_calls.clear()
            sweep_3 = datetime(2099, 6, 17, 10, 0, tzinfo=reminder_scheduler.SHANGHAI_TZ).astimezone(
                timezone.utc
            )
            res_3 = evaluate_due_reminders(session, now=sweep_3)
            assert f"deadline:{dl.id}:offset:3:2099-06-20" in res_3.delivery_keys
            assert f"deadline_pref:{dl.id}:u{lawyer_id}:offset:3:2099-06-20" not in res_3.delivery_keys
            assert len(dispatched_calls) == 1
            assert dispatched_calls[0][0] == [lawyer_id]
    finally:
        with Session(engine) as session:
            lawyer_row = session.get(User, lawyer_id)
            if lawyer_row is not None:
                lawyer_row.default_deadline_offsets_days = orig_offsets
                session.add(lawyer_row)
                session.commit()
        _cleanup_m3(case_ids=created_cases)


def test_partial_failure_retry_backoff_and_max_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    created_cases: list[int] = []
    with Session(engine) as session:
        u1 = session.exec(select(User).where(User.username == "lead_lawyer")).first()
        u2 = session.exec(select(User).where(User.username == "legal_assistant")).first()
        assert u1 is not None and u1.id is not None
        assert u2 is not None and u2.id is not None
        u1_id, u2_id = u1.id, u2.id

    attempts: list[list[int]] = []
    mode = {"u2_ok": False}

    def fake_dispatch(*, users, **kwargs):  # type: ignore[no-untyped-def]
        uids = [u.id for u in users if u.id is not None]
        attempts.append(uids)
        if mode["u2_ok"]:
            return DispatchResult(ok=True, dry_run=True, succeeded_user_ids=uids, failed_user_ids=[])
        succ = [uid for uid in uids if uid == u1_id]
        fail = [uid for uid in uids if uid != u1_id]
        return DispatchResult(
            ok=False,
            dry_run=True,
            succeeded_user_ids=succ,
            failed_user_ids=fail,
            error_msg="u2 timeout",
        )

    monkeypatch.setattr(reminder_scheduler, "dispatch_notification_to_users", fake_dispatch)

    try:
        with Session(engine) as session:
            due = date(2099, 7, 10)
            case = Case(
                case_title="M3 分人重试与退避测试",
                case_number=f"（2099）M3重试{secrets.randbelow(9000)}号",
                case_category=CaseCategory.CIVIL,
                procedure_subtype="first_instance",
                current_stage="first_instance",
                visibility=CaseVisibility.TEAM,
                owner_id=u1_id,
                client_name="客户A",
                folder_rel_path=f"test/m3r_{secrets.token_hex(4)}",
            )
            session.add(case)
            session.commit()
            session.refresh(case)
            assert case.id is not None
            created_cases.append(case.id)

            dl = Deadline(
                case_id=case.id,
                rule_code="CUSTOM_RETRY",
                title="部分失败重试死线",
                base_date=due,
                due_date=due,
                raw_due_date=due,
                notify_target_type=NotifyTargetType.CUSTOM_USERS,
                custom_notify_user_ids=[u1_id, u2_id],
                reminder_offsets_days=[1],
            )
            session.add(dl)
            session.commit()
            session.refresh(dl)

            key = f"deadline:{dl.id}:offset:1:2099-07-10"
            t0 = datetime(2099, 7, 9, 10, 0, tzinfo=reminder_scheduler.SHANGHAI_TZ).astimezone(timezone.utc)
            s1 = evaluate_due_reminders(session, now=t0)
            assert s1.failed_count == 1
            assert attempts[-1] == sorted([u1_id, u2_id])

            rd = session.exec(select(ReminderDelivery).where(ReminderDelivery.idempotency_key == key)).first()
            assert rd is not None
            assert rd.sent_user_ids == [u1_id]
            assert rd.retry_count == 1
            assert rd.next_retry_at is not None

            # Within backoff (+2 min < +5 min) -> skipped
            s_too_early = evaluate_due_reminders(session, now=t0 + timedelta(minutes=2))
            assert s_too_early.failed_count == 0
            assert len(attempts) == 1

            # After +6 min -> retries ONLY u2_id (u1_id not re-sent!)
            mode["u2_ok"] = True
            s2 = evaluate_due_reminders(session, now=t0 + timedelta(minutes=6))
            assert s2.sent_count == 1
            assert attempts[-1] == [u2_id]

            rd = session.exec(select(ReminderDelivery).where(ReminderDelivery.idempotency_key == key)).first()
            assert rd is not None
            assert rd.status == "sent"
            assert rd.sent_user_ids == sorted([u1_id, u2_id])

            # Test 3-attempt hard cap on a second deadline
            mode["u2_ok"] = False
            dl2 = Deadline(
                case_id=case.id,
                rule_code="CUSTOM_CAP",
                title="重试上限死线",
                base_date=due,
                due_date=due,
                raw_due_date=due,
                notify_target_type=NotifyTargetType.CUSTOM_USERS,
                custom_notify_user_ids=[u2_id],
                reminder_offsets_days=[1],
            )
            session.add(dl2)
            session.commit()
            session.refresh(dl2)
            key2 = f"deadline:{dl2.id}:offset:1:2099-07-10"

            attempts.clear()
            evaluate_due_reminders(session, now=t0)
            evaluate_due_reminders(session, now=t0 + timedelta(minutes=6))
            evaluate_due_reminders(session, now=t0 + timedelta(minutes=20))
            assert len(attempts) == 3

            # 4th sweep even much later -> blocked by MAX_DELIVERY_RETRIES=3
            evaluate_due_reminders(session, now=t0 + timedelta(minutes=120))
            assert len(attempts) == 3
            rd2 = session.exec(select(ReminderDelivery).where(ReminderDelivery.idempotency_key == key2)).first()
            assert rd2 is not None and rd2.retry_count == 3 and rd2.status == "failed"
    finally:
        _cleanup_m3(case_ids=created_cases)


def test_archived_case_inactive_user_schedule_burst_and_single_manual_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_cases: list[int] = []
    created_schedules: list[int] = []
    created_triage: list[int] = []
    created_sms: list[int] = []
    created_users: list[int] = []
    sent_keys: list[tuple[str, list[int]]] = []

    def fake_dispatch(*, users, title, **kwargs):  # type: ignore[no-untyped-def]
        uids = [u.id for u in users if u.id is not None]
        sent_keys.append((title, uids))
        return DispatchResult(ok=True, dry_run=True, succeeded_user_ids=uids, failed_user_ids=[])

    monkeypatch.setattr(reminder_scheduler, "dispatch_notification_to_users", fake_dispatch)

    try:
        with Session(engine) as session:
            lead = session.exec(select(User).where(User.username == "lead_lawyer")).first()
            assert lead is not None and lead.id is not None

            inactive = User(
                username=f"inactive_{secrets.token_hex(4)}",
                full_name="离职律师",
                hashed_password="x",
                system_role=SystemRole.LAWYER,
                is_active=False,
            )
            session.add(inactive)
            session.commit()
            session.refresh(inactive)
            assert inactive.id is not None
            created_users.append(inactive.id)

            archived_case = Case(
                case_title="已归档测试案件",
                case_number=f"（2099）归档{secrets.randbelow(9000)}号",
                case_category=CaseCategory.CIVIL,
                procedure_subtype="first_instance",
                current_stage="first_instance",
                visibility=CaseVisibility.TEAM,
                owner_id=lead.id,
                client_name="归档客户",
                folder_rel_path=f"test/arch_{secrets.token_hex(4)}",
                is_archived=True,
            )
            session.add(archived_case)
            session.commit()
            session.refresh(archived_case)
            assert archived_case.id is not None
            created_cases.append(archived_case.id)

            due = date(2099, 8, 10)
            session.add(
                Deadline(
                    case_id=archived_case.id,
                    rule_code="ARCH_RULE",
                    title="已归档死线应跳过",
                    base_date=due,
                    due_date=due,
                    raw_due_date=due,
                    reminder_offsets_days=[1],
                )
            )

            # Schedule starting 20 mins after sweep with [1440, 120, 30] -> only 30 should fire!
            sched_start = datetime(2099, 8, 9, 10, 20, tzinfo=reminder_scheduler.SHANGHAI_TZ).astimezone(
                timezone.utc
            )
            sched = ScheduleItem(
                case_id=None,
                owner_id=lead.id,
                title="临近日程防连环轰炸",
                category="hearing",
                start_time=sched_start,
                end_time=sched_start + timedelta(hours=1),
                notify_target_type=NotifyTargetType.CUSTOM_USERS,
                custom_notify_user_ids=[lead.id, inactive.id],
                reminder_offsets_minutes=[1440, 120, 30],
            )
            session.add(sched)
            session.commit()
            session.refresh(sched)
            assert sched.id is not None
            created_schedules.append(sched.id)

            sweep_now = datetime(2099, 8, 9, 10, 0, tzinfo=reminder_scheduler.SHANGHAI_TZ).astimezone(
                timezone.utc
            )
            s1 = evaluate_due_reminders(session, now=sweep_now)
            assert s1.sent_count == 1
            assert s1.delivery_keys == [f"schedule:{sched.id}:min:30:{sched_start.isoformat()}"]
            assert sent_keys == [("【LawTeam 日程提醒】临近日程防连环轰炸", [lead.id])]

            # Second sweep must NOT fire 1440 or 120 either!
            s2 = evaluate_due_reminders(session, now=sweep_now + timedelta(minutes=5))
            assert s2.sent_count == 0
            assert s2.already_processed_count >= 1

            # Two MANUAL_FETCH_REQUIRED triage items -> push_manual_fetch_alert must push ONLY target item!
            sms1 = SmsInbound(
                receiver_user_id=lead.id,
                content="sms1",
                content_hash=secrets.token_hex(16),
                dedup_key=secrets.token_hex(16),
            )
            sms2 = SmsInbound(
                receiver_user_id=lead.id,
                content="sms2",
                content_hash=secrets.token_hex(16),
                dedup_key=secrets.token_hex(16),
            )
            session.add(sms1)
            session.add(sms2)
            session.commit()
            session.refresh(sms1)
            session.refresh(sms2)
            assert sms1.id is not None and sms2.id is not None
            created_sms.extend([sms1.id, sms2.id])

            tr1 = InboxTriageItem(
                sms_id=sms1.id,
                owner_user_id=lead.id,
                item_type="judicial_stage_and_doc",
                status=TriageStatus.MANUAL_FETCH_REQUIRED,
                extracted_payload={"case_number": "（2099）目标1号", "agency": "广州中院"},
                error_or_manual_reason="需要图形验证码",
            )
            tr2 = InboxTriageItem(
                sms_id=sms2.id,
                owner_user_id=lead.id,
                item_type="judicial_stage_and_doc",
                status=TriageStatus.MANUAL_FETCH_REQUIRED,
                extracted_payload={"case_number": "（2099）无关2号", "agency": "深圳中院"},
                error_or_manual_reason="需要图形验证码",
            )
            session.add(tr1)
            session.add(tr2)
            session.commit()
            session.refresh(tr1)
            session.refresh(tr2)
            assert tr1.id is not None and tr2.id is not None
            created_triage.extend([tr1.id, tr2.id])

            single_res = push_manual_fetch_alert(session, tr1)
            assert single_res.delivery_keys == [f"triage_manual:{tr1.id}"]
            assert session.exec(
                select(ReminderDelivery).where(
                    ReminderDelivery.idempotency_key == f"triage_manual:{tr2.id}"
                )
            ).first() is None
    finally:
        _cleanup_m3(
            case_ids=created_cases,
            schedule_ids=created_schedules,
            triage_ids=created_triage,
            sms_ids=created_sms,
            user_ids=created_users,
        )


def test_deliveries_endpoint_readable_fields_and_private_isolation() -> None:
    created_cases: list[int] = []
    delivery_keys: list[str] = []
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.username == "partner_admin")).first()
        lawyer = session.exec(select(User).where(User.username == "lead_lawyer")).first()
        assert admin is not None and admin.id is not None
        assert lawyer is not None and lawyer.id is not None

        priv_case = Case(
            case_title="M3 私人保密案件",
            case_number=f"（2099）私密{secrets.randbelow(9000)}号",
            case_category=CaseCategory.CRIMINAL,
            procedure_subtype="first_instance",
            current_stage="first_instance",
            visibility=CaseVisibility.PRIVATE,
            owner_id=lawyer.id,
            client_name="私密委托人",
            folder_rel_path=f"test/m3p_{secrets.token_hex(4)}",
        )
        team_case = Case(
            case_title="M3 团队可见案件",
            case_number=f"（2099）团队{secrets.randbelow(9000)}号",
            case_category=CaseCategory.CIVIL,
            procedure_subtype="first_instance",
            current_stage="first_instance",
            visibility=CaseVisibility.TEAM,
            owner_id=lawyer.id,
            client_name="团队委托人",
            folder_rel_path=f"test/m3t_{secrets.token_hex(4)}",
        )
        session.add(priv_case)
        session.add(team_case)
        session.commit()
        session.refresh(priv_case)
        session.refresh(team_case)
        assert priv_case.id is not None and team_case.id is not None
        team_case_id = team_case.id
        lawyer_name = lawyer.full_name
        created_cases.extend([priv_case.id, team_case.id])

        dl_priv = Deadline(
            case_id=priv_case.id,
            rule_code="PRIV_DL",
            title="私密案件死线",
            base_date=date(2099, 9, 1),
            due_date=date(2099, 9, 1),
            raw_due_date=date(2099, 9, 1),
        )
        dl_team = Deadline(
            case_id=team_case.id,
            rule_code="TEAM_DL",
            title="团队案件上诉届满",
            base_date=date(2099, 9, 2),
            due_date=date(2099, 9, 2),
            raw_due_date=date(2099, 9, 2),
        )
        session.add(dl_priv)
        session.add(dl_team)
        session.commit()
        session.refresh(dl_priv)
        session.refresh(dl_team)
        assert dl_priv.id is not None and dl_team.id is not None

        k_priv = f"test_priv_{secrets.token_hex(6)}"
        k_team = f"test_team_{secrets.token_hex(6)}"
        delivery_keys.extend([k_priv, k_team])
        session.add(
            ReminderDelivery(
                idempotency_key=k_priv,
                source_type="deadline",
                source_id=dl_priv.id,
                recipient_user_ids=[lawyer.id],
                sent_user_ids=[lawyer.id],
                scheduled_at=datetime.now(timezone.utc),
                status="sent",
            )
        )
        session.add(
            ReminderDelivery(
                idempotency_key=k_team,
                source_type="deadline",
                source_id=dl_team.id,
                recipient_user_ids=[lawyer.id],
                sent_user_ids=[lawyer.id],
                scheduled_at=datetime.now(timezone.utc),
                status="sent",
            )
        )
        session.commit()

    client = TestClient(app)
    try:
        admin_hdr = _login(client, "partner_admin")
        lawyer_hdr = _login(client, "lead_lawyer")

        r_admin = client.get("/api/reminders/deliveries?limit=50", headers=admin_hdr)
        assert r_admin.status_code == 200
        admin_rows = r_admin.json()
        assert all(row["idempotency_key"] != k_priv for row in admin_rows)
        team_row = next((row for row in admin_rows if row["idempotency_key"] == k_team), None)
        assert team_row is not None
        assert team_row["case_id"] == team_case_id
        assert team_row["case_title"] == "M3 团队可见案件"
        assert team_row["source_title"] == "团队案件上诉届满"
        assert lawyer_name in team_row["recipient_names"]

        r_lawyer = client.get("/api/reminders/deliveries?limit=50", headers=lawyer_hdr)
        assert r_lawyer.status_code == 200
        assert any(row["idempotency_key"] == k_priv for row in r_lawyer.json())
    finally:
        _cleanup_m3(case_ids=created_cases, delivery_keys=delivery_keys)


@pytest.mark.asyncio
async def test_ssrf_webhook_and_sms_allowlist_and_redirect_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_settings()
    for blocked in (
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
        "http://127.0.0.1:8000/api/health",
    ):
        with pytest.raises(UnsafeUrlError):
            validate_webhook_url(blocked, settings=cfg)

    lan_settings = Settings(allow_internal_webhook_hosts="im-bot.internal")
    assert validate_webhook_url("https://im-bot.internal/hook", settings=lan_settings) == "https://im-bot.internal/hook"
    with pytest.raises(UnsafeUrlError):
        validate_webhook_url("http://169.254.169.254/latest", settings=lan_settings)

    with pytest.raises(UnsafeUrlError):
        validate_external_fetch_url("https://evil-phishing.example.com/dl", require_portal_allowlist=True)
    assert (
        validate_external_fetch_url("https://sd.gdems.com/test", require_portal_allowlist=True)
        == "https://sd.gdems.com/test"
    )

    # Redirect hop landing on 127.0.0.1 must be blocked
    class FakeResp:
        status_code = 302
        headers = {"location": "http://127.0.0.1:8080/internal"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, url: str):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    with pytest.raises(UnsafeUrlError):
        await safe_async_get("https://zxfw.court.gov.cn/short", require_portal_allowlist=True)

    # Tier-2 Playwright and invoice fetcher must refuse non-allowlisted domain safely
    t2_res, t2_err, _ = await court_fetcher._tier2_playwright_fetch(
        {"url": "https://evil.example.com/hack", "domain": "evil.example.com"},
        batch_key="k1",
        case_number=None,
        received_date="2026-01-01",
        phone_last4="1234",
        id_card_last4=None,
        extraction_code=None,
        confirm_sign=True,
    )
    assert t2_res is None
    assert "白名单" in t2_err

    inv_res = await invoice_fetcher.fetch_and_stage_invoice(
        {"invoice_links": [{"url": "https://evil.example.com/inv.pdf"}]}
    )
    assert inv_res.status == "manual_required"
    assert inv_res.manual_reason and "白名单" in inv_res.manual_reason
