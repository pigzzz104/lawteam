from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.security import get_current_user, record_audit_log, require_admin
from app.core.seed import load_stage_templates, seed_auto_triage_policies
from app.database import get_session
from app.models.entities import (
    AutoTriagePolicy,
    CaseCategory,
    DeadlineRule,
    HolidayRollMode,
    RiskLevel,
    User,
    utcnow,
)
from app.services.deadline_engine import recalculate_rule_deadlines

router = APIRouter(prefix="/api/rules", tags=["rules"])


class DeadlineRuleCreate(BaseModel):
    rule_code: str
    name: str
    case_category: CaseCategory
    trigger_event_type: str
    condition_match: dict[str, Any] = {}
    duration_value: int
    duration_unit: str = "days"
    holiday_roll_mode: HolidayRollMode = HolidayRollMode.ROLL_FORWARD_WORKDAY
    legal_basis: str
    risk_level: RiskLevel = RiskLevel.HIGH
    default_offsets_days: list[int] = [7, 3, 1, 0]
    is_enabled: bool = True


class DeadlineRuleUpdate(BaseModel):
    name: Optional[str] = None
    case_category: Optional[CaseCategory] = None
    trigger_event_type: Optional[str] = None
    condition_match: Optional[dict[str, Any]] = None
    duration_value: Optional[int] = None
    duration_unit: Optional[str] = None
    holiday_roll_mode: Optional[HolidayRollMode] = None
    legal_basis: Optional[str] = None
    risk_level: Optional[RiskLevel] = None
    default_offsets_days: Optional[list[int]] = None
    is_enabled: Optional[bool] = None


class RuleRecalculateResponse(BaseModel):
    rule_id: int
    rule_code: str
    affected_cases_count: int
    affected_events_count: int
    updated_deadlines_count: int
    created_deadlines_count: int
    skipped_manual_override_count: int
    skipped_completed_count: int


class AutoTriagePolicyUpdate(BaseModel):
    mode: Literal["auto_archive", "require_confirm"]


@router.get("/auto-triage-policies", response_model=list[AutoTriagePolicy])
def list_auto_triage_policies(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[AutoTriagePolicy]:
    """List team-wide auto-archive vs manual-confirm policy toggles."""
    seed_auto_triage_policies(session)
    return list(
        session.exec(
            select(AutoTriagePolicy).order_by(
                AutoTriagePolicy.sort_order, AutoTriagePolicy.id
            )
        ).all()
    )


@router.patch("/auto-triage-policies/{policy_key}", response_model=AutoTriagePolicy)
def update_auto_triage_policy(
    policy_key: str,
    payload: AutoTriagePolicyUpdate,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> AutoTriagePolicy:
    """Admin-only toggle for per-category auto-archive vs human confirmation."""
    seed_auto_triage_policies(session)
    policy = session.exec(
        select(AutoTriagePolicy).where(AutoTriagePolicy.policy_key == policy_key)
    ).first()
    if policy is None:
        raise HTTPException(status_code=404, detail="Auto-triage policy not found")

    policy.mode = payload.mode
    policy.updated_by = admin_user.id
    policy.updated_at = utcnow()
    session.add(policy)
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="triage_policy.update",
        entity_type="auto_triage_policy",
        entity_id=policy.id,
        detail={"policy_key": policy.policy_key, "mode": policy.mode},
    )
    session.commit()
    session.refresh(policy)
    return policy


@router.get("/stage-templates")
def get_stage_templates(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return multi-domain procedure stage templates from stage_templates.json."""
    return load_stage_templates()


@router.get("", response_model=list[DeadlineRule])
def list_rules(
    case_category: Optional[CaseCategory] = Query(default=None),
    trigger_event_type: Optional[str] = Query(default=None),
    enabled_only: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[DeadlineRule]:
    """List multi-domain statutory & custom deadline rules."""
    stmt = select(DeadlineRule)
    if case_category is not None:
        stmt = stmt.where(DeadlineRule.case_category == case_category)
    if trigger_event_type is not None:
        stmt = stmt.where(DeadlineRule.trigger_event_type == trigger_event_type)
    if enabled_only:
        stmt = stmt.where(DeadlineRule.is_enabled == True)  # noqa: E712
    stmt = stmt.order_by(DeadlineRule.case_category, DeadlineRule.id)
    return list(session.exec(stmt).all())


@router.post("", response_model=DeadlineRule, status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: DeadlineRuleCreate,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> DeadlineRule:
    """Admin-only creation of custom deadline rules."""
    if payload.duration_unit not in ("days", "months", "years"):
        raise HTTPException(status_code=400, detail="duration_unit must be days | months | years")
    if payload.duration_value < 0:
        raise HTTPException(status_code=400, detail="duration_value must be non-negative")

    code = payload.rule_code.strip().upper()
    existing = session.exec(select(DeadlineRule).where(DeadlineRule.rule_code == code)).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Rule code '{code}' already exists")

    rule = DeadlineRule(
        rule_code=code,
        name=payload.name.strip(),
        case_category=payload.case_category,
        trigger_event_type=payload.trigger_event_type.strip(),
        condition_match=payload.condition_match,
        duration_value=payload.duration_value,
        duration_unit=payload.duration_unit,
        holiday_roll_mode=payload.holiday_roll_mode,
        legal_basis=payload.legal_basis.strip(),
        risk_level=payload.risk_level,
        default_offsets_days=payload.default_offsets_days,
        is_system_builtin=False,
        is_enabled=payload.is_enabled,
    )
    session.add(rule)
    session.flush()
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="rule.create",
        entity_type="deadline_rule",
        entity_id=rule.id,
        detail={"rule_code": rule.rule_code},
    )
    session.commit()
    session.refresh(rule)
    return rule


@router.patch("/{rule_id}", response_model=DeadlineRule)
def update_rule(
    rule_id: int,
    payload: DeadlineRuleUpdate,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> DeadlineRule:
    """Admin-only edit or enable/disable toggle for a deadline rule."""
    rule = session.get(DeadlineRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Deadline rule not found")

    if payload.duration_unit is not None and payload.duration_unit not in ("days", "months", "years"):
        raise HTTPException(status_code=400, detail="duration_unit must be days | months | years")
    if payload.duration_value is not None and payload.duration_value < 0:
        raise HTTPException(status_code=400, detail="duration_value must be non-negative")

    updates = payload.model_dump(exclude_none=True)
    for field, value in updates.items():
        setattr(rule, field, value)

    session.add(rule)
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="rule.update",
        entity_type="deadline_rule",
        entity_id=rule.id,
        detail=updates,
    )
    session.commit()
    session.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: int,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    """Admin-only delete for custom rules; built-in rules can only be disabled."""
    rule = session.get(DeadlineRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Deadline rule not found")
    if rule.is_system_builtin:
        raise HTTPException(
            status_code=400,
            detail="System built-in statutory rules cannot be deleted; disable them instead.",
        )

    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="rule.delete",
        entity_type="deadline_rule",
        entity_id=rule.id,
        detail={"rule_code": rule.rule_code},
    )
    session.delete(rule)
    session.commit()


@router.post("/{rule_id}/recalculate", response_model=RuleRecalculateResponse)
def recalculate_rule_across_cases(
    rule_id: int,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> RuleRecalculateResponse:
    """Recalculate open, non-overridden deadlines on active cases affected by ``rule_id``."""
    rule = session.get(DeadlineRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Deadline rule not found")

    stats = recalculate_rule_deadlines(session, rule)
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="rule.recalculate",
        entity_type="deadline_rule",
        entity_id=rule.id,
        detail={"rule_code": rule.rule_code, **stats},
    )
    session.commit()
    return RuleRecalculateResponse(
        rule_id=rule.id or rule_id,
        rule_code=rule.rule_code,
        **stats,
    )
