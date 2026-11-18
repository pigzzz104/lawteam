"""Phase 5 backend API verification (case audit log timeline + RBAC)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_phase1 import _cleanup_case, _login


def test_case_audit_logs_endpoint_and_rbac(client: TestClient) -> None:
    lead_headers = _login(client, "lead_lawyer")
    assistant_headers = _login(client, "legal_assistant")
    admin_headers = _login(client, "partner_admin")

    private_case_id: int | None = None
    team_case_id: int | None = None
    try:
        create_resp = client.post(
            "/api/cases",
            headers=lead_headers,
            json={
                "case_title": "审计日志私密验证案",
                "case_category": "civil",
                "visibility": "private",
                "client_name": "甲公司",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        private_case_id = create_resp.json()["case"]["id"]

        ev_resp = client.post(
            f"/api/cases/{private_case_id}/events",
            headers=lead_headers,
            json={
                "event_type": "民事一审判决书送达",
                "event_date": "2026-10-10",
                "service_method": "electronic",
            },
        )
        assert ev_resp.status_code == 201, ev_resp.text

        logs_resp = client.get(f"/api/cases/{private_case_id}/audit-logs", headers=lead_headers)
        assert logs_resp.status_code == 200
        actions = [row["action"] for row in logs_resp.json()]
        assert "case.create" in actions
        assert "case.event_create" in actions
        assert logs_resp.json()[0]["actor_name"] == "李主办律师"

        # Strictly private cases deny both unrelated assistants and admins
        assert client.get(f"/api/cases/{private_case_id}/audit-logs", headers=admin_headers).status_code == 403
        assert client.get(f"/api/cases/{private_case_id}/audit-logs", headers=assistant_headers).status_code == 403

        # Team case grants access to admin
        team_resp = client.post(
            "/api/cases",
            headers=lead_headers,
            json={
                "case_title": "审计日志团队验证案",
                "case_category": "civil",
                "visibility": "team",
                "client_name": "乙公司",
            },
        )
        assert team_resp.status_code == 201, team_resp.text
        team_case_id = team_resp.json()["case"]["id"]
        assert client.get(f"/api/cases/{team_case_id}/audit-logs", headers=admin_headers).status_code == 200
    finally:
        if private_case_id is not None:
            _cleanup_case(private_case_id)
        if team_case_id is not None:
            _cleanup_case(team_case_id)


def test_case_patch_clears_nullable_fields_and_records_upload_entity_id(client: TestClient) -> None:
    lead_headers = _login(client, "lead_lawyer")
    case_id: int | None = None
    try:
        create_resp = client.post(
            "/api/cases",
            headers=lead_headers,
            json={
                "case_title": "刑案字段清空与上传审计验证",
                "case_category": "criminal",
                "visibility": "team",
                "client_name": "张三",
                "custody_status": "在押",
                "custody_place": "浦东新区看守所",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        case_id = create_resp.json()["case"]["id"]

        # Nullable fields can be explicitly cleared to null
        clear_resp = client.patch(
            f"/api/cases/{case_id}",
            headers=lead_headers,
            json={"custody_status": None, "custody_place": None},
        )
        assert clear_resp.status_code == 200, clear_resp.text
        assert clear_resp.json()["case"]["custody_status"] is None
        assert clear_resp.json()["case"]["custody_place"] is None

        # Non-nullable required fields reject explicit null
        bad_resp = client.patch(
            f"/api/cases/{case_id}",
            headers=lead_headers,
            json={"client_name": None},
        )
        assert bad_resp.status_code == 400

        # File upload audit log records non-null entity_id
        up_resp = client.post(
            f"/api/cases/{case_id}/files/upload",
            headers=lead_headers,
            data={"subfolder": "02_诉讼文书与法律意见"},
            files={"file": ("opinion.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
        assert up_resp.status_code == 201, up_resp.text
        doc_id = up_resp.json()["id"]

        logs = client.get(f"/api/cases/{case_id}/audit-logs", headers=lead_headers).json()
        upload_log = next((row for row in logs if row["action"] == "case.file_upload"), None)
        assert upload_log is not None
        assert upload_log["entity_id"] == doc_id
    finally:
        if case_id is not None:
            _cleanup_case(case_id)
