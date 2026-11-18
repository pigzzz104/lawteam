"""P2 repair verification tests: enforcement/non_litigation rules, rule batch recalculation, auto-triage policy switches."""

from __future__ import annotations

import secrets

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.database import engine
from app.models.entities import (
    AuditLog,
    Case,
    CaseEvent,
    CaseMember,
    Deadline,
    DeadlineRule,
    Document,
    InboxTriageItem,
    ReminderDelivery,
    ScheduleItem,
    SmsInbound,
)
from app.services.sms_parser import parse_sms_regex


def _login_headers(
    client: TestClient, username: str, password: str = "LawTeam@2026"
) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _cleanup_records(
    *,
    case_ids: list[int] | None = None,
    sms_ids: list[int] | None = None,
    triage_ids: list[int] | None = None,
    rule_ids: list[int] | None = None,
) -> None:
    with Session(engine) as session:
        for tid in triage_ids or []:
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

        for cid in case_ids or []:
            for t in session.exec(
                select(InboxTriageItem).where(InboxTriageItem.matched_case_id == cid)
            ).all():
                if t.sms_id and t.sms_id not in (sms_ids or []):
                    sms_ids = list(sms_ids or []) + [t.sms_id]
                session.delete(t)
            for model in (AuditLog, Deadline, ScheduleItem, Document, CaseEvent, CaseMember):
                for row in session.exec(select(model).where(model.case_id == cid)).all():
                    session.delete(row)

        session.flush()
        for sid in sms_ids or []:
            s = session.get(SmsInbound, sid)
            if s is not None:
                session.delete(s)
        for cid in case_ids or []:
            c = session.get(Case, cid)
            if c is not None:
                session.delete(c)
        for rid in rule_ids or []:
            for log in session.exec(
                select(AuditLog).where(
                    AuditLog.entity_type == "deadline_rule",
                    AuditLog.entity_id == rid,
                )
            ).all():
                session.delete(log)
            r = session.get(DeadlineRule, rid)
            if r is not None and not r.is_system_builtin:
                session.delete(r)
        session.commit()


def test_p2_enforcement_and_non_litigation_rules_seeded_and_derive(client: TestClient) -> None:
    headers = _login_headers(client, "lead_lawyer")
    enf_rules = client.get("/api/rules?case_category=enforcement", headers=headers).json()
    nonlit_rules = client.get("/api/rules?case_category=non_litigation", headers=headers).json()

    enf_codes = {r["rule_code"] for r in enf_rules}
    nonlit_codes = {r["rule_code"] for r in nonlit_rules}
    assert {
        "ENF_PRESERVATION_BANK_1Y",
        "ENF_PRESERVATION_MOVABLE_2Y",
        "ENF_PRESERVATION_REAL_ESTATE_3Y",
        "ENF_NOTICE_REPORT_PROPERTY_10D",
        "ENF_OBJECTION_SUIT_15D",
        "ENF_RECONSIDERATION_10D",
        "ENF_SETTLEMENT_TRACK_30D",
        "ENF_SETTLEMENT_RESUME_2Y",
    }.issubset(enf_codes)
    assert {
        "NONLIT_RETAINER_RENEWAL_1Y",
        "NONLIT_PROJECT_DELIVERY_15D",
        "NONLIT_PAYMENT_DUE_30D",
        "NONLIT_CONTRACT_REVIEW_3D",
    }.issubset(nonlit_codes)

    parsed_obj = parse_sms_regex(
        "【上海法院】案号（2026）沪01执异88号《执行异议裁定书》已电子送达。"
    )
    assert parsed_obj["event_type_guess"] == "enforcement_objection_served"

    case_ids: list[int] = []
    try:
        enf_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": f"P2执行规则测试_{secrets.token_hex(3)}",
                "case_number": f"（2026）沪01执{secrets.randbelow(9000) + 100}号",
                "case_category": "enforcement",
                "client_name": "P2执行委托方",
            },
        )
        assert enf_resp.status_code == 201, enf_resp.text
        enf_case_id = enf_resp.json()["case"]["id"]
        case_ids.append(enf_case_id)

        ev_obj = client.post(
            f"/api/cases/{enf_case_id}/events",
            headers=headers,
            json={"event_type": "enforcement_objection_served", "event_date": "2026-04-01"},
        )
        assert ev_obj.status_code == 201, ev_obj.text
        assert any(
            d["rule_code"] == "ENF_OBJECTION_SUIT_15D" and d["raw_due_date"] == "2026-04-16"
            for d in ev_obj.json()["derived_deadlines"]
        )

        ev_rul = client.post(
            f"/api/cases/{enf_case_id}/events",
            headers=headers,
            json={"event_type": "ruling_served", "event_date": "2026-04-01"},
        )
        assert ev_rul.status_code == 201, ev_rul.text
        assert any(
            d["rule_code"] == "ENF_RECONSIDERATION_10D" and d["raw_due_date"] == "2026-04-11"
            for d in ev_rul.json()["derived_deadlines"]
        )

        nonlit_resp = client.post(
            "/api/cases",
            headers=headers,
            json={
                "case_title": f"P2非诉项目测试_{secrets.token_hex(3)}",
                "case_category": "non_litigation",
                "client_name": "P2法顾客户有限公司",
            },
        )
        assert nonlit_resp.status_code == 201, nonlit_resp.text
        nonlit_case_id = nonlit_resp.json()["case"]["id"]
        case_ids.append(nonlit_case_id)

        ev_contract = client.post(
            f"/api/cases/{nonlit_case_id}/events",
            headers=headers,
            json={"event_type": "contract_review_requested", "event_date": "2026-04-06"},
        )
        assert ev_contract.status_code == 201, ev_contract.text
        assert any(
            d["rule_code"] == "NONLIT_CONTRACT_REVIEW_3D" and d["raw_due_date"] == "2026-04-09"
            for d in ev_contract.json()["derived_deadlines"]
        )
    finally:
        _cleanup_records(case_ids=case_ids)


def test_p2_rule_batch_recalculate_preserves_manual_override(client: TestClient) -> None:
    admin_headers = _login_headers(client, "partner_admin")
    lawyer_headers = _login_headers(client, "lead_lawyer")
    suffix = secrets.token_hex(3).upper()
    rule_code = f"CUSTOM_P2_RECALC_{suffix}"

    case_ids: list[int] = []
    rule_ids: list[int] = []
    try:
        r_resp = client.post(
            "/api/rules",
            headers=admin_headers,
            json={
                "rule_code": rule_code,
                "name": "P2非诉自定义测试期限",
                "case_category": "non_litigation",
                "trigger_event_type": "custom_p2_trigger",
                "condition_match": {"is_foreign_related": False},
                "duration_value": 10,
                "duration_unit": "days",
                "holiday_roll_mode": "calendar_exact",
                "legal_basis": "测试委托合同第5条",
                "risk_level": "high",
                "default_offsets_days": [3, 1, 0],
                "is_enabled": True,
            },
        )
        assert r_resp.status_code == 201, r_resp.text
        rule_id = r_resp.json()["id"]
        rule_ids.append(rule_id)

        # Case A (active, automatic deadline -> will be updated)
        # Case B (active, manual override -> must be skipped)
        # Case C (archived -> must not be recalculated)
        dl_by_case: dict[int, int] = {}
        for idx in range(3):
            c_resp = client.post(
                "/api/cases",
                headers=admin_headers,
                json={
                    "case_title": f"P2重算测试案{idx}_{suffix}",
                    "case_category": "non_litigation",
                    "client_name": f"P2客户{idx}",
                    "is_foreign_related": False,
                },
            )
            assert c_resp.status_code == 201, c_resp.text
            cid = c_resp.json()["case"]["id"]
            case_ids.append(cid)

            ev_resp = client.post(
                f"/api/cases/{cid}/events",
                headers=admin_headers,
                json={"event_type": "custom_p2_trigger", "event_date": "2026-05-01"},
            )
            assert ev_resp.status_code == 201, ev_resp.text
            dl_id = ev_resp.json()["derived_deadlines"][0]["id"]
            dl_by_case[cid] = dl_id

        # Manually lock Case B deadline
        patch_dl = client.patch(
            f"/api/deadlines/{dl_by_case[case_ids[1]]}",
            headers=admin_headers,
            json={
                "due_date": "2026-06-01",
                "is_manual_override": True,
                "override_reason": "律师双方书面协商延期",
            },
        )
        assert patch_dl.status_code == 200, patch_dl.text

        # Archive Case C
        arch_resp = client.patch(
            f"/api/cases/{case_ids[2]}",
            headers=admin_headers,
            json={"is_archived": True},
        )
        assert arch_resp.status_code == 200, arch_resp.text

        # Update rule duration from 10 days -> 20 days
        upd_rule = client.patch(
            f"/api/rules/{rule_id}",
            headers=admin_headers,
            json={"duration_value": 20},
        )
        assert upd_rule.status_code == 200, upd_rule.text

        # Non-admin forbidden to trigger batch recalculate
        forbidden = client.post(f"/api/rules/{rule_id}/recalculate", headers=lawyer_headers)
        assert forbidden.status_code == 403

        # Admin triggers batch recalculation
        recalc = client.post(f"/api/rules/{rule_id}/recalculate", headers=admin_headers)
        assert recalc.status_code == 200, recalc.text
        stats = recalc.json()
        assert stats["affected_cases_count"] == 2
        assert stats["affected_events_count"] == 2
        assert stats["updated_deadlines_count"] == 1
        assert stats["skipped_manual_override_count"] == 1

        # Verify Case A updated to 2026-05-21, Case B stayed 2026-06-01, Case C stayed 2026-05-11
        detail_a = client.get(f"/api/cases/{case_ids[0]}", headers=admin_headers).json()
        detail_b = client.get(f"/api/cases/{case_ids[1]}", headers=admin_headers).json()
        detail_c = client.get(f"/api/cases/{case_ids[2]}", headers=admin_headers).json()
        assert detail_a["deadlines"][0]["due_date"] == "2026-05-21"
        assert detail_b["deadlines"][0]["due_date"] == "2026-06-01"
        assert detail_b["deadlines"][0]["is_manual_override"] is True
        assert detail_c["deadlines"][0]["due_date"] == "2026-05-11"
    finally:
        _cleanup_records(case_ids=case_ids, rule_ids=rule_ids)


def test_p2_auto_triage_policy_switches_enforce_confirm(client: TestClient) -> None:
    admin_headers = _login_headers(client, "partner_admin")
    lawyer_headers = _login_headers(client, "lead_lawyer")

    policies_resp = client.get("/api/rules/auto-triage-policies", headers=lawyer_headers)
    assert policies_resp.status_code == 200, policies_resp.text
    policies = policies_resp.json()
    keys = {p["policy_key"]: p["mode"] for p in policies}
    assert keys.get("judgment_ruling_doc") == "auto_archive"
    assert keys.get("noise_other_sms") == "auto_archive"

    # Non-admin cannot toggle auto-triage policy
    denied = client.patch(
        "/api/rules/auto-triage-policies/judgment_ruling_doc",
        headers=lawyer_headers,
        json={"mode": "require_confirm"},
    )
    assert denied.status_code == 403

    case_ids: list[int] = []
    sms_ids: list[int] = []
    triage_ids: list[int] = []
    suffix = secrets.token_hex(4)
    case_no = f"（2026）沪0104民初{secrets.randbelow(9000) + 1000}号"
    try:
        c_resp = client.post(
            "/api/cases",
            headers=lawyer_headers,
            json={
                "case_title": f"P2分拣策略测试案_{suffix}",
                "case_number": case_no,
                "case_category": "civil",
                "client_name": "P2策略测试委托人",
            },
        )
        assert c_resp.status_code == 201, c_resp.text
        case_ids.append(c_resp.json()["case"]["id"])

        # Switch judgment_ruling_doc & noise_other_sms to require_confirm
        assert (
            client.patch(
                "/api/rules/auto-triage-policies/judgment_ruling_doc",
                headers=admin_headers,
                json={"mode": "require_confirm"},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/api/rules/auto-triage-policies/noise_other_sms",
                headers=admin_headers,
                json={"mode": "require_confirm"},
            ).status_code
            == 200
        )

        # Simulate civil judgment SMS -> must pause in PENDING for human secondary check
        sim_j = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": f"【上海法院】案号{case_no}《民事判决书》已电子送达（{suffix}）。",
                "auto_fetch": False,
            },
        )
        assert sim_j.status_code == 201, sim_j.text
        body_j = sim_j.json()
        triage_ids.append(body_j["id"])
        if body_j.get("sms_id"):
            sms_ids.append(body_j["sms_id"])
        assert body_j["status"] == "pending"
        assert body_j["auto_archived"] is False
        assert body_j["matched_case_id"] == case_ids[0]
        assert "人工二次复核" in (body_j.get("triage_reason") or "")

        # Simulate noise SMS -> must also stay PENDING while policy is require_confirm
        sim_n = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": f"【物业通知】本周六小区停水检修，请提前储水（{suffix}）。",
                "auto_fetch": False,
            },
        )
        assert sim_n.status_code == 201, sim_n.text
        body_n = sim_n.json()
        triage_ids.append(body_n["id"])
        if body_n.get("sms_id"):
            sms_ids.append(body_n["sms_id"])
        assert body_n["status"] == "pending"
        assert "人工二次复核" in (body_n.get("triage_reason") or "")

        # Restore both back to default auto_archive -> new items auto-archive/ignore again
        assert (
            client.patch(
                "/api/rules/auto-triage-policies/judgment_ruling_doc",
                headers=admin_headers,
                json={"mode": "auto_archive"},
            ).status_code
            == 200
        )
        assert (
            client.patch(
                "/api/rules/auto-triage-policies/noise_other_sms",
                headers=admin_headers,
                json={"mode": "auto_archive"},
            ).status_code
            == 200
        )

        sim_j2 = client.post(
            "/api/inbox/simulate",
            headers=lawyer_headers,
            json={
                "content": f"【上海法院】案号{case_no}《民事判决书》二次送达验证（{suffix}）。",
                "auto_fetch": False,
            },
        )
        assert sim_j2.status_code == 201, sim_j2.text
        body_j2 = sim_j2.json()
        triage_ids.append(body_j2["id"])
        if body_j2.get("sms_id"):
            sms_ids.append(body_j2["sms_id"])
        assert body_j2["status"] == "confirmed"
        assert body_j2["auto_archived"] is True
    finally:
        client.patch(
            "/api/rules/auto-triage-policies/judgment_ruling_doc",
            headers=admin_headers,
            json={"mode": "auto_archive"},
        )
        client.patch(
            "/api/rules/auto-triage-policies/noise_other_sms",
            headers=admin_headers,
            json={"mode": "auto_archive"},
        )
        _cleanup_records(case_ids=case_ids, sms_ids=sms_ids, triage_ids=triage_ids)
