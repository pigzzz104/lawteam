from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

import chinese_calendar
from sqlmodel import Session, select

from app.models.entities import (
    Case,
    CaseEvent,
    Deadline,
    DeadlineRule,
    HolidayRollMode,
    NotifyTargetType,
    RiskLevel,
)


@dataclass(frozen=True)
class DeadlineCalculation:
    """Deterministic breakdown of a legal deadline calculation."""

    base_date: date
    raw_due_date: date
    due_date: date
    effective_duration_value: int
    roll_reason: Optional[str] = None


def is_china_workday(target_date: date) -> tuple[bool, Optional[str], bool]:
    """
    Determine whether ``target_date`` is an official PRC workday.

    Returns ``(is_workday, holiday_name, used_weekend_fallback)``.
    Official make-up workdays (补班, e.g. 2026-02-14 Saturday) return ``True``.
    """
    try:
        is_holiday_flag, holiday_name = chinese_calendar.get_holiday_detail(target_date)
        workday_flag = chinese_calendar.is_workday(target_date)
        if not workday_flag and not is_holiday_flag and holiday_name is None:
            holiday_name = "周末"
        return workday_flag, holiday_name, False
    except NotImplementedError:
        is_wkday = target_date.weekday() < 5
        return is_wkday, None if is_wkday else "周末(超出节假日表年份)", True


def add_months_clamped(base: date, months: int) -> date:
    """
    Add calendar months with month-end clamping (民法典§202 / 刑诉法解释§202).

    Example: ``2026-01-31 + 1 month -> 2026-02-28``.
    """
    total_months = (base.month - 1) + months
    target_year = base.year + (total_months // 12)
    target_month = (total_months % 12) + 1
    last_day = calendar.monthrange(target_year, target_month)[1]
    return date(target_year, target_month, min(base.day, last_day))


def add_years_clamped(base: date, years: int) -> date:
    """Add calendar years with leap-day clamping (e.g. Feb 29 -> Feb 28 in non-leap year)."""
    target_year = base.year + years
    last_day = calendar.monthrange(target_year, base.month)[1]
    return date(target_year, base.month, min(base.day, last_day))


def compute_base_date(event_date: date, service_method: str) -> tuple[date, Optional[str]]:
    """Shift base date when service is via public announcement (公告送达)."""
    method = (service_method or "electronic").strip().lower()
    if method == "announcement_30":
        shifted = event_date + timedelta(days=30)
        return shifted, f"公告送达(30日)满视为送达，起算基准日推至 {shifted.isoformat()}"
    if method == "announcement_60":
        shifted = event_date + timedelta(days=60)
        return shifted, f"涉外公告送达(60日)满视为送达，起算基准日推至 {shifted.isoformat()}"
    return event_date, None


def calculate_due_date(
    *,
    event_date: date,
    duration_value: int,
    duration_unit: str,
    holiday_roll_mode: HolidayRollMode,
    service_method: str = "electronic",
    authority_specified_days: Optional[int] = None,
) -> DeadlineCalculation:
    """
    Calculate a legal deadline deterministically in pure Python.

    No LLM calls or heuristics are ever used here.
    """
    notes: list[str] = []
    base_date, service_note = compute_base_date(event_date, service_method)
    if service_note:
        notes.append(service_note)

    unit = (duration_unit or "days").strip().lower()
    effective_value = duration_value
    if (
        authority_specified_days is not None
        and authority_specified_days > 0
        and unit == "days"
    ):
        effective_value = authority_specified_days
        if effective_value != duration_value:
            notes.append(f"采用办案机关特别指定天数 {effective_value} 日")

    if unit == "days":
        raw_due_date = base_date + timedelta(days=effective_value)
    elif unit == "months":
        raw_due_date = add_months_clamped(base_date, effective_value)
    elif unit == "years":
        raw_due_date = add_years_clamped(base_date, effective_value)
    else:
        raise ValueError(f"Unsupported duration_unit: {duration_unit}")

    due_date = raw_due_date
    raw_is_workday, raw_label, _ = is_china_workday(raw_due_date)

    if holiday_roll_mode == HolidayRollMode.ROLL_FORWARD_WORKDAY:
        if raw_is_workday:
            if raw_due_date.weekday() >= 5:
                notes.append(
                    f"到期日 {raw_due_date.isoformat()} 为法定调休补班工作日，依法不顺延"
                )
        else:
            cursor = raw_due_date
            for _ in range(60):
                is_wk, _, _ = is_china_workday(cursor)
                if is_wk:
                    due_date = cursor
                    break
                cursor += timedelta(days=1)
            label_desc = raw_label or "非工作日"
            notes.append(
                f"原到期日 {raw_due_date.isoformat()}（{label_desc}），"
                f"依法顺延至首个工作日 {due_date.isoformat()}"
            )
    elif holiday_roll_mode == HolidayRollMode.NO_ROLL_CUSTODY:
        notes.append("刑事在押期间依法不因节假日顺延（刑诉法第105条第3款）")
    elif holiday_roll_mode == HolidayRollMode.CALENDAR_EXACT:
        notes.append("自然日精确到期，不顺延")

    return DeadlineCalculation(
        base_date=base_date,
        raw_due_date=raw_due_date,
        due_date=due_date,
        effective_duration_value=effective_value,
        roll_reason="；".join(notes) if notes else None,
    )


def _match_condition_entry(case: Case, key: str, expected: Any) -> bool:
    negated = key.endswith("_not")
    field_name = key[:-4] if negated else key
    actual = getattr(case, field_name, None)
    if field_name == "crime_type" and not actual:
        actual = "一般犯罪"

    if negated:
        if isinstance(expected, list):
            return actual not in expected
        return actual != expected

    if isinstance(expected, list):
        return actual in expected
    return actual == expected


def _allows_authority_specified_days(rule: DeadlineRule, event: CaseEvent) -> bool:
    if rule.duration_unit != "days":
        return False
    if rule.rule_code == "CIVIL_EVIDENCE" or event.event_type == "evidence_notice_served":
        return True
    return bool((rule.condition_match or {}).get("authority_specifiable"))


_RULE_METADATA_KEYS = frozenset({"authority_specifiable"})


def rule_matches_case(rule: DeadlineRule, case: Case) -> bool:
    """Enforce procedure-category and case-attribute isolation for deadline rules."""
    if not rule.is_enabled:
        return False
    if rule.case_category != case.case_category:
        return False
    conditions = rule.condition_match or {}
    for key, expected in conditions.items():
        if key in _RULE_METADATA_KEYS:
            continue
        if not _match_condition_entry(case, key, expected):
            return False
    return True


def derive_deadlines_for_event(
    session: Session, case: Case, event: CaseEvent
) -> list[Deadline]:
    """
    Derive or recalculate statutory deadlines for ``event`` on ``case``.

    Protects any existing Deadline with ``is_manual_override=True`` from being
    overwritten.
    """
    if case.id is None or event.id is None:
        raise ValueError("Case and CaseEvent must be persisted before deriving deadlines")

    candidate_rules = list(
        session.exec(
            select(DeadlineRule)
            .where(
                DeadlineRule.case_category == case.case_category,
                DeadlineRule.trigger_event_type == event.event_type,
                DeadlineRule.is_enabled == True,  # noqa: E712
            )
            .order_by(DeadlineRule.id)
        ).all()
    )

    matching_rules = [rule for rule in candidate_rules if rule_matches_case(rule, case)]
    matching_codes = {rule.rule_code for rule in matching_rules}

    existing_rows = list(
        session.exec(
            select(Deadline).where(
                Deadline.case_id == case.id,
                Deadline.source_event_id == event.id,
            )
        ).all()
    )
    existing_by_code = {row.rule_code: row for row in existing_rows}

    # Prune obsolete auto-derived deadlines whose rule is disabled or no longer matches
    for row in existing_rows:
        if (
            row.rule_code != "CUSTOM"
            and row.rule_code not in matching_codes
            and not row.is_manual_override
            and not row.is_completed
        ):
            session.delete(row)

    derived: list[Deadline] = []
    for rule in matching_rules:
        eff_authority_days = (
            event.authority_specified_days
            if _allows_authority_specified_days(rule, event)
            else None
        )
        calc = calculate_due_date(
            event_date=event.event_date,
            duration_value=rule.duration_value,
            duration_unit=rule.duration_unit,
            holiday_roll_mode=rule.holiday_roll_mode,
            service_method=event.service_method,
            authority_specified_days=eff_authority_days,
        )

        existing = existing_by_code.get(rule.rule_code)
        if existing is not None:
            # Iron Rule #1: Manual overrides are immutable to automatic recalculation.
            if not existing.is_manual_override and not existing.is_completed:
                existing.title = rule.name
                existing.legal_basis = rule.legal_basis
                existing.base_date = calc.base_date
                existing.raw_due_date = calc.raw_due_date
                existing.due_date = calc.due_date
                existing.holiday_roll_mode = rule.holiday_roll_mode
                existing.roll_reason = calc.roll_reason
                existing.risk_level = rule.risk_level
                session.add(existing)
            derived.append(existing)
            continue

        deadline = Deadline(
            case_id=case.id,
            source_event_id=event.id,
            rule_code=rule.rule_code,
            title=rule.name,
            legal_basis=rule.legal_basis,
            base_date=calc.base_date,
            raw_due_date=calc.raw_due_date,
            due_date=calc.due_date,
            holiday_roll_mode=rule.holiday_roll_mode,
            roll_reason=calc.roll_reason,
            risk_level=rule.risk_level,
            is_manual_override=False,
            is_completed=False,
            notify_target_type=NotifyTargetType.CASE_TEAM,
            custom_notify_user_ids=[],
            escalate_to_supervisor=(rule.risk_level == RiskLevel.CRITICAL),
            reminder_offsets_days=list(rule.default_offsets_days or [7, 3, 1, 0]),
            personal_custom_alerts=[],
        )
        session.add(deadline)
        derived.append(deadline)

    session.flush()
    return derived


def recalculate_rule_deadlines(session: Session, rule: DeadlineRule) -> dict[str, int]:
    """
    Recalculate open, non-overridden deadlines on active cases affected by ``rule``.

    Deadlines with ``is_manual_override=True`` or ``is_completed=True`` are never altered.
    Obsolete open deadlines that no longer match ``rule`` (or when disabled) are pruned.
    """
    stats = {
        "affected_cases_count": 0,
        "affected_events_count": 0,
        "updated_deadlines_count": 0,
        "created_deadlines_count": 0,
        "skipped_manual_override_count": 0,
        "skipped_completed_count": 0,
    }

    # Prune obsolete open deadlines for this rule_code across all non-archived cases
    # (even when rule.case_category or rule.trigger_event_type changed or rule was disabled)
    existing_rule_deadlines = list(
        session.exec(
            select(Deadline).where(
                Deadline.rule_code == rule.rule_code,
                Deadline.is_manual_override == False,  # noqa: E712
                Deadline.is_completed == False,  # noqa: E712
            )
        ).all()
    )
    ref_case_ids = {dl.case_id for dl in existing_rule_deadlines}
    ref_event_ids = {
        dl.source_event_id for dl in existing_rule_deadlines if dl.source_event_id is not None
    }
    ref_cases = (
        {
            c.id: c
            for c in session.exec(select(Case).where(Case.id.in_(ref_case_ids))).all()  # type: ignore[union-attr]
            if c.id is not None
        }
        if ref_case_ids
        else {}
    )
    ref_events = (
        {
            ev.id: ev
            for ev in session.exec(select(CaseEvent).where(CaseEvent.id.in_(ref_event_ids))).all()  # type: ignore[union-attr]
            if ev.id is not None
        }
        if ref_event_ids
        else {}
    )
    for dl in existing_rule_deadlines:
        case_obj = ref_cases.get(dl.case_id)
        if case_obj is None or case_obj.is_archived:
            continue
        ev_obj = ref_events.get(dl.source_event_id) if dl.source_event_id is not None else None
        if (
            not rule.is_enabled
            or not rule_matches_case(rule, case_obj)
            or (ev_obj is not None and ev_obj.event_type != rule.trigger_event_type)
        ):
            session.delete(dl)

    if not rule.is_enabled:
        session.flush()
        return stats

    active_cases = list(
        session.exec(
            select(Case)
            .where(
                Case.case_category == rule.case_category,
                Case.is_archived == False,  # noqa: E712
            )
            .order_by(Case.id)
        ).all()
    )

    for case in active_cases:
        if case.id is None or not rule_matches_case(rule, case):
            continue
        events = list(
            session.exec(
                select(CaseEvent)
                .where(
                    CaseEvent.case_id == case.id,
                    CaseEvent.event_type == rule.trigger_event_type,
                )
                .order_by(CaseEvent.id)
            ).all()
        )
        if not events:
            continue
        stats["affected_cases_count"] += 1

        for event in events:
            if event.id is None:
                continue
            stats["affected_events_count"] += 1
            existing = session.exec(
                select(Deadline).where(
                    Deadline.case_id == case.id,
                    Deadline.source_event_id == event.id,
                    Deadline.rule_code == rule.rule_code,
                )
            ).first()
            if existing is not None:
                if existing.is_manual_override:
                    stats["skipped_manual_override_count"] += 1
                    continue
                if existing.is_completed:
                    stats["skipped_completed_count"] += 1
                    continue

            derived = derive_deadlines_for_event(session, case, event)
            matched = [d for d in derived if d.rule_code == rule.rule_code]
            if matched:
                if existing is None:
                    stats["created_deadlines_count"] += len(matched)
                else:
                    stats["updated_deadlines_count"] += len(matched)

    return stats
