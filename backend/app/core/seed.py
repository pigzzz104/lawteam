from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.security import hash_password
from app.models.entities import (
    AutoTriagePolicy,
    CaseCategory,
    DeadlineRule,
    HolidayRollMode,
    RiskLevel,
    SystemRole,
    User,
)

CORE_DIR = Path(__file__).resolve().parent
SEED_RULES_PATH = CORE_DIR / "deadline_rules_seed.json"
STAGE_TEMPLATES_PATH = CORE_DIR / "stage_templates.json"
SMS_PATTERNS_PATH = CORE_DIR / "sms_patterns.json"

DEMO_USERS_SPEC = [
    {
        "username": "partner_admin",
        "full_name": "张合伙人",
        "password": "LawTeam@2026",
        "system_role": SystemRole.ADMIN,
        "wecom_userid": "zhang_partner",
        "phone_os": "ios",
        "phone_last4": "8801",
        "id_card_last4": "101X",
        "calendar_color": "#dc2626",
    },
    {
        "username": "lead_lawyer",
        "full_name": "李主办律师",
        "password": "LawTeam@2026",
        "system_role": SystemRole.LAWYER,
        "wecom_userid": "li_lawyer",
        "phone_os": "ios",
        "phone_last4": "6602",
        "id_card_last4": "2024",
        "calendar_color": "#2563eb",
    },
    {
        "username": "legal_assistant",
        "full_name": "王法务助理",
        "password": "LawTeam@2026",
        "system_role": SystemRole.ASSISTANT,
        "wecom_userid": "wang_assistant",
        "phone_os": "android",
        "phone_last4": "9903",
        "id_card_last4": "3038",
        "calendar_color": "#16a34a",
    },
]


def load_stage_templates() -> dict[str, Any]:
    """Load stage_templates.json from backend/app/core/."""
    return json.loads(STAGE_TEMPLATES_PATH.read_text(encoding="utf-8"))


def resolve_default_subtype_and_stage(
    category: CaseCategory,
    requested_subtype: str | None = None,
    requested_stage: str | None = None,
) -> tuple[str, str]:
    """Resolve default procedure subtype and initial stage from stage_templates.json."""
    templates = load_stage_templates().get("categories", {})
    cat_cfg = templates.get(category.value, {})
    subtype = requested_subtype or cat_cfg.get("default_subtype") or "默认"
    subtypes_map = cat_cfg.get("subtypes", {})
    stages = subtypes_map.get(subtype) or cat_cfg.get("stages") or ["准备阶段"]
    stage = requested_stage or stages[0]
    return subtype, stage


def load_sms_patterns() -> dict[str, Any]:
    """Load sms_patterns.json from backend/app/core/."""
    return json.loads(SMS_PATTERNS_PATH.read_text(encoding="utf-8"))


def seed_demo_users(session: Session) -> dict[str, User]:
    """Idempotently create the 3 demo users if they do not yet exist."""
    result: dict[str, User] = {}
    created_any = False

    for spec in DEMO_USERS_SPEC:
        existing = session.exec(
            select(User).where(User.username == spec["username"])
        ).first()
        if existing is not None:
            result[existing.username] = existing
            continue

        user = User(
            username=spec["username"],
            full_name=spec["full_name"],
            hashed_password=hash_password(spec["password"]),
            system_role=spec["system_role"],
            wecom_userid=spec["wecom_userid"],
            phone_os=spec["phone_os"],
            phone_last4=spec["phone_last4"],
            id_card_last4=spec["id_card_last4"],
            calendar_color=spec["calendar_color"],
            is_active=True,
        )
        session.add(user)
        result[user.username] = user
        created_any = True

    if created_any:
        session.commit()
        for uname, u in list(result.items()):
            session.refresh(u)
    return result


def seed_deadline_rules(session: Session) -> int:
    """Idempotently load deadline rules from deadline_rules_seed.json without overwriting existing edits."""
    payload = json.loads(SEED_RULES_PATH.read_text(encoding="utf-8"))
    rules_data = payload.get("rules", [])
    existing_codes = set(session.exec(select(DeadlineRule.rule_code)).all())

    inserted = 0
    for item in rules_data:
        if item["rule_code"] in existing_codes:
            continue
        rule = DeadlineRule(
            rule_code=item["rule_code"],
            name=item["name"],
            case_category=CaseCategory(item["case_category"]),
            trigger_event_type=item["trigger_event_type"],
            condition_match=item.get("condition_match") or {},
            duration_value=int(item["duration_value"]),
            duration_unit=item.get("duration_unit", "days"),
            holiday_roll_mode=HolidayRollMode(item["holiday_roll_mode"]),
            legal_basis=item["legal_basis"],
            risk_level=RiskLevel(item["risk_level"]),
            default_offsets_days=item.get("default_offsets_days") or [7, 3, 1, 0],
            is_system_builtin=bool(item.get("is_system_builtin", True)),
            is_enabled=bool(item.get("is_enabled", True)),
        )
        session.add(rule)
        inserted += 1

    if inserted > 0:
        session.commit()
    return inserted


DEFAULT_AUTO_TRIAGE_POLICIES = [
    {
        "policy_key": "invoice_archive",
        "label": "电子发票与数电票",
        "description": "匹配唯一在办案件（或发票含明确案号时自动建案）并归档至「05_财务与发票」；重名或无案号暂存待认领。",
        "mode": "auto_archive",
        "sort_order": 1,
    },
    {
        "policy_key": "judgment_ruling_doc",
        "label": "裁判与裁决类文书（判决书/裁定书/仲裁裁决/复议决定）",
        "description": "识别明确案号且命中期限规则时自动入案（无对应在办案时自动新建案件）并生成上诉/异议/撤裁法定期限。",
        "mode": "auto_archive",
        "sort_order": 2,
    },
    {
        "policy_key": "hearing_notice",
        "label": "开庭传票与庭审时间通知",
        "description": "识别明确案号与开庭时间时自动入案（无对应在办案时自动新建案件）并写入团队与个人日历。",
        "mode": "auto_archive",
        "sort_order": 3,
    },
    {
        "policy_key": "procedural_deadline_doc",
        "label": "其他触发法定期限文书（起诉状/举证通知/刑拘逮捕/执行通知）",
        "description": "识别明确案号且命中规则时自动入案与派生期限；无案号刑拘或扫描件未提天数时转人工核对。",
        "mode": "auto_archive",
        "sort_order": 4,
    },
    {
        "policy_key": "routine_doc",
        "label": "常规无期限程序文书（缴费通知/签收回执等）",
        "description": "识别明确案号且已取回文书附件时，自动归档至案件「01_司法与程序文书」。",
        "mode": "auto_archive",
        "sort_order": 5,
    },
    {
        "policy_key": "noise_other_sms",
        "label": "非涉案杂项与营销通知短信",
        "description": "未含司法链接、发票链接、案号或程序关键词的杂项短信默认自动忽略降噪。",
        "mode": "auto_archive",
        "sort_order": 6,
    },
]


def seed_auto_triage_policies(session: Session) -> int:
    """Idempotently create default auto-triage policy toggles without overwriting user edits."""
    existing_keys = set(session.exec(select(AutoTriagePolicy.policy_key)).all())
    inserted = 0
    for spec in DEFAULT_AUTO_TRIAGE_POLICIES:
        if spec["policy_key"] in existing_keys:
            continue
        session.add(AutoTriagePolicy(**spec))
        inserted += 1
    if inserted > 0:
        session.commit()
    return inserted


def seed_initial_admin(session: Session) -> User | None:
    """Create the first ADMIN account in non-dev environments when users table is empty."""
    from app.config import get_settings

    settings = get_settings()
    username = settings.initial_admin_username.strip()
    password = settings.initial_admin_password.strip()
    if not username or not password:
        return None
    existing_any = session.exec(select(User.id)).first()
    if existing_any is not None:
        return None
    admin = User(
        username=username,
        full_name=settings.initial_admin_full_name.strip() or "系统管理员",
        hashed_password=hash_password(password),
        system_role=SystemRole.ADMIN,
        calendar_color="#dc2626",
        is_active=True,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def bootstrap_database(session: Session) -> None:
    """Run all idempotent startup seeders."""
    from app.config import get_settings

    if get_settings().app_env.strip().lower() == "development":
        seed_demo_users(session)
    else:
        seed_initial_admin(session)
    seed_deadline_rules(session)
    seed_auto_triage_policies(session)
