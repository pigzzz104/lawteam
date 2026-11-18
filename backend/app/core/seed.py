from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.security import hash_password
from app.models.entities import (
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


def bootstrap_database(session: Session) -> None:
    """Run all idempotent startup seeders."""
    seed_demo_users(session)
    seed_deadline_rules(session)
