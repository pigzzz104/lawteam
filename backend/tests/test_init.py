import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.services.llm_client import UniversalLLMClient

CORE_DIR = Path(__file__).resolve().parents[1] / "app" / "core"


def test_core_seed_json_files_valid():
    for filename in ("sms_patterns.json", "stage_templates.json", "deadline_rules_seed.json"):
        data = json.loads((CORE_DIR / filename).read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    rules = json.loads((CORE_DIR / "deadline_rules_seed.json").read_text(encoding="utf-8"))["rules"]
    rule_codes = {r["rule_code"] for r in rules}
    assert "CIVIL_APPEAL_JUDGMENT_15D" in rule_codes
    assert "CRIM_APPEAL_JUDGMENT_10D" in rule_codes
    assert "CRIM_APPEAL_RULING_5D" in rule_codes
    assert "ARB_SET_ASIDE_3M" in rule_codes


def test_universal_llm_config_modes():
    disabled = UniversalLLMClient(Settings(llm_provider="none"))
    assert disabled.is_enabled is False

    openai_cfg = UniversalLLMClient(
        Settings(
            llm_provider="openai_compatible",
            llm_base_url="https://api.deepseek.com/v1",
            llm_api_key="sk-test",
            llm_model="deepseek-chat",
        )
    )
    assert openai_cfg.is_enabled is True

    anthropic_cfg = UniversalLLMClient(
        Settings(
            llm_provider="anthropic",
            llm_base_url="https://api.anthropic.com",
            llm_api_key="sk-ant-test",
            llm_model="claude-3-5-sonnet-20241022",
        )
    )
    assert anthropic_cfg.is_enabled is True


def test_health_endpoint_connects_to_postgres():
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["database"] == "ok"
    assert payload["timezone"] == "Asia/Shanghai"
