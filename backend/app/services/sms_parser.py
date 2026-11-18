"""Multi-agency SMS classification, regex extraction, optional LLM fallback, and case matching.

Supports Court (12368/zxfw), Procuratorate (12309), Public Security/Detention Center,
Arbitration Commission, and E-Invoice SMS without trusting sender numbers alone.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.core.security import get_accessible_case_ids_filter
from app.core.seed import load_sms_patterns, load_stage_templates
from app.models.entities import Case, CaseCategory, User
from app.services.llm_client import UniversalLLMClient

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_CASE_NO_NORMALIZE_TRANS = str.maketrans({"(": "（", ")": "）", "[": "（", "]": "）", "〔": "（", "〕": "）"})
_TRAILING_PUNCT = "，。；！!）,.)）\"'、\r\n"

_CASE_CATEGORY_ORDER = (
    "criminal",
    "administrative",
    "labor_arbitration",
    "commercial_arb",
    "enforcement",
    "civil",
)

_SUGGESTED_STAGE_BY_EVENT: dict[tuple[str, str], str] = {
    ("civil", "pleading_served"): "举证答辩",
    ("civil", "evidence_notice_served"): "举证答辩",
    ("civil", "hearing_scheduled"): "待开庭/审理",
    ("civil", "judgment_served"): "裁判与上诉期",
    ("civil", "ruling_served"): "裁判与上诉期",
    ("criminal", "detention_notified"): "侦查阶段(刑拘)",
    ("criminal", "case_transferred_arrest"): "审查批捕(黄金37天)",
    ("criminal", "arrest_executed"): "继续侦查(逮捕后)",
    ("criminal", "prosecution_review_started"): "审查起诉(阅卷/量刑)",
    ("criminal", "hearing_scheduled"): "刑事一审",
    ("criminal", "judgment_served"): "刑事一审",
    ("administrative", "pleading_served"): "被告举证与答辩(15日盯防)",
    ("administrative", "hearing_scheduled"): "开庭审理",
    ("administrative", "judgment_served"): "裁判与上诉期",
    ("labor_arbitration", "pleading_served"): "举证与答辩",
    ("labor_arbitration", "hearing_scheduled"): "仲裁开庭",
    ("labor_arbitration", "arbitration_award_served"): "裁决与15日起诉期",
    ("commercial_arb", "pleading_served"): "组庭与答辩举证",
    ("commercial_arb", "hearing_scheduled"): "仲裁开庭",
    ("commercial_arb", "arbitration_award_served"): "裁决与3个月撤裁期",
    ("enforcement", "enforcement_notice_served"): "网络查控与财产调查",
}


@lru_cache
def _patterns() -> dict[str, Any]:
    return load_sms_patterns()


@lru_cache
def _stage_templates() -> dict[str, Any]:
    return load_stage_templates()


def normalize_case_number(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = re.sub(r"\s+", "", raw.strip()).translate(_CASE_NO_NORMALIZE_TRANS)
    return cleaned or None


def _extract_url_params(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    query_str = (
        parsed.fragment.split("?", 1)[1]
        if "#" in url and "?" in parsed.fragment
        else parsed.query
    )
    qs = parse_qs(query_str)
    params: dict[str, str] = {}
    for k, vals in qs.items():
        if vals and vals[0]:
            params[k] = vals[0]
    return params


def _extract_hearing_time_iso(content: str, patterns: dict[str, Any]) -> Optional[str]:
    for pat in patterns.get("hearing_time_patterns", []):
        m = re.search(pat, content)
        if not m:
            continue
        raw = m.group(1)
        nums = re.findall(r"\d+", raw)
        if len(nums) >= 4:
            year, month, day, hour = (int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3]))
            minute = int(nums[4]) if len(nums) >= 5 else 0
            if "下午" in raw and hour < 12:
                hour += 12
            try:
                dt = datetime(year, month, day, hour, minute, tzinfo=SHANGHAI_TZ)
                return dt.isoformat()
            except ValueError:
                continue
    return None


def _extract_event_date_iso(content: str, fallback_dt: Optional[datetime] = None) -> str:
    # Check explicit header/notice date such as 2026年06月12日 or 2026-06-12
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", content)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d.isoformat()
        except ValueError:
            pass
    base_dt = fallback_dt or datetime.now(SHANGHAI_TZ)
    if base_dt.tzinfo is None:
        base_dt = base_dt.replace(tzinfo=SHANGHAI_TZ)
    return base_dt.astimezone(SHANGHAI_TZ).date().isoformat()


def _extract_doc_title_hint(content: str) -> Optional[str]:
    # e.g. 送达文书（上诉缴费单） or 送达调查令
    m_paren = re.search(r"送达文书[（(]([^）)]+)[）)]", content)
    if m_paren:
        return m_paren.group(1).strip()
    m_sub = re.search(r"送达([\u4e00-\u9fa5]{2,14})", content)
    if m_sub:
        return m_sub.group(1).strip()
    for title_kw in (
        "民事判决书",
        "刑事判决书",
        "行政判决书",
        "裁定书",
        "仲裁裁决书",
        "行政复议决定书",
        "举证通知书",
        "应诉通知书",
        "起诉状副本",
        "上诉状副本",
        "开庭传票",
        "传票",
        "调查令",
        "上诉缴费单",
        "拘留通知书",
        "逮捕通知书",
        "起诉意见书",
    ):
        if title_kw in content:
            return title_kw
    if "送达文书" in content:
        return "送达文书"
    return None


def _extract_client_hint(content: str) -> Optional[str]:
    # e.g. 上海馨成达会展科技有限公司代理人：
    m_agent = re.search(r"[】\]\s]([\u4e00-\u9fa5A-Za-z0-9()（）]{2,40})代理人[：:]", content)
    if m_agent:
        return m_agent.group(1).strip()
    return None


def _classify_sms_category(content: str, patterns: dict[str, Any]) -> str:
    rules: dict[str, list[str]] = patterns.get("sms_category_rules", {})
    priority = ("invoice", "procuratorate_12309", "detention_police", "arbitration", "court_service")
    for category in priority:
        for kw in rules.get(category, []):
            if kw in content:
                return category
    return "other"


def _guess_case_category(content: str, case_number: Optional[str], patterns: dict[str, Any]) -> Optional[str]:
    search_space = f"{case_number or ''} {content}"
    cat_keywords: dict[str, list[str]] = patterns.get("case_category_keywords", {})
    for cat in _CASE_CATEGORY_ORDER:
        for pat in cat_keywords.get(cat, []):
            try:
                if re.search(pat, search_space):
                    return cat
            except re.error:
                if pat in search_space:
                    return cat
    return None


def _guess_event_type(content: str, doc_title_hint: Optional[str], patterns: dict[str, Any]) -> Optional[str]:
    search_space = f"{doc_title_hint or ''} {content}"
    ev_keywords: dict[str, list[str]] = patterns.get("event_type_keywords", {})
    # Check specific procedural docs before generic hearing/service
    priority = (
        "judgment_served",
        "ruling_served",
        "arbitration_award_served",
        "reconsideration_decision_served",
        "evidence_notice_served",
        "pleading_served",
        "detention_notified",
        "case_transferred_arrest",
        "arrest_executed",
        "prosecution_review_started",
        "hearing_scheduled",
    )
    for ev in priority:
        for kw in ev_keywords.get(ev, []):
            if kw in search_space:
                return ev
    if "执行" in search_space and any(k in search_space for k in ("调查令", "执行通知书", "限期履行")):
        return "enforcement_notice_served"
    if "送达文书" in content or "查收" in content:
        return "document_served"
    return None


def parse_sms_regex(
    content: str,
    *,
    received_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Deterministically parse SMS text using ``sms_patterns.json``."""
    patterns = _patterns()
    clean_text = content.strip()

    # 1. Case number
    case_no_raw: Optional[str] = None
    case_no_pat = patterns.get("case_number_pattern")
    if case_no_pat:
        m_case = re.search(case_no_pat, clean_text)
        if m_case:
            case_no_raw = normalize_case_number(m_case.group(0))

    # 2. SMS category & triage item_type
    sms_category = _classify_sms_category(clean_text, patterns)
    item_type = "invoice_archive" if sms_category == "invoice" else "judicial_stage_and_doc"

    # 3. Court & invoice links
    court_links: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for link_rule in patterns.get("download_link_patterns", []):
        pat = link_rule.get("pattern")
        if not pat:
            continue
        for m in re.finditer(pat, clean_text):
            url = m.group(0).rstrip(_TRAILING_PUNCT)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            court_links.append(
                {
                    "domain": link_rule.get("canonical_domain") or link_rule.get("domain"),
                    "description": link_rule.get("description"),
                    "tier": link_rule.get("tier", 2),
                    "download_strategy": link_rule.get("download_strategy", "playwright"),
                    "url": url,
                    "params": _extract_url_params(url),
                }
            )

    invoice_links: list[dict[str, Any]] = []
    for inv_rule in patterns.get("invoice_link_patterns", []):
        pat = inv_rule.get("pattern")
        if not pat:
            continue
        for m in re.finditer(pat, clean_text):
            url = m.group(0).rstrip(_TRAILING_PUNCT)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            invoice_links.append(
                {
                    "provider": inv_rule.get("provider"),
                    "description": inv_rule.get("description"),
                    "url": url,
                }
            )

    # Generic http link fallback if invoice SMS has a link not matching known patterns
    if sms_category == "invoice" and not invoice_links:
        for m in re.finditer(r"https?://[^\s]+", clean_text):
            url = m.group(0).rstrip(_TRAILING_PUNCT)
            if url not in seen_urls:
                seen_urls.add(url)
                invoice_links.append({"provider": "generic", "description": "电子发票链接", "url": url})

    # 4. Extraction code / account credentials
    extraction_code: Optional[str] = None
    for pat in patterns.get("extraction_code_patterns", []):
        m_code = re.search(pat, clean_text)
        if m_code:
            extraction_code = m_code.group(1).strip()
            break

    account: Optional[str] = None
    password: Optional[str] = None
    m_acct = re.search(r"账号\s*(\d{12,20})", clean_text)
    if m_acct:
        account = m_acct.group(1)
    m_pwd = re.search(r"(?:默认密码|密码)[：:]\s*([0-9A-Za-z]+)", clean_text)
    if m_pwd:
        password = m_pwd.group(1)

    # 5. Agency
    agency: Optional[str] = None
    m_sig = re.match(r"^\s*【([^】]{2,30})】", clean_text)
    if m_sig:
        agency = m_sig.group(1).strip()
    if not agency:
        for pat in patterns.get("agency_patterns", []):
            m_ag = re.search(pat, clean_text)
            if m_ag:
                agency = m_ag.group(1).strip()
                break

    doc_title_hint = _extract_doc_title_hint(clean_text)
    client_hint = _extract_client_hint(clean_text)
    case_category_guess = _guess_case_category(clean_text, case_no_raw, patterns)
    event_type_guess = _guess_event_type(clean_text, doc_title_hint, patterns)
    hearing_time = _extract_hearing_time_iso(clean_text, patterns)
    event_date = _extract_event_date_iso(clean_text, fallback_dt=received_at)

    suggested_stage = None
    if case_category_guess and event_type_guess:
        suggested_stage = _SUGGESTED_STAGE_BY_EVENT.get((case_category_guess, event_type_guess))

    return {
        "sms_category": sms_category,
        "item_type": item_type,
        "case_number": case_no_raw,
        "case_category_guess": case_category_guess,
        "agency": agency,
        "event_type_guess": event_type_guess,
        "event_date": event_date,
        "hearing_time": hearing_time,
        "doc_title_hint": doc_title_hint,
        "client_hint": client_hint,
        "suggested_stage": suggested_stage,
        "court_links": court_links,
        "invoice_links": invoice_links,
        "extraction_code": extraction_code,
        "account": account,
        "password": password,
        "llm_assisted": False,
    }


async def parse_sms_with_optional_llm(
    content: str,
    *,
    received_at: Optional[datetime] = None,
    llm_client: Optional[UniversalLLMClient] = None,
) -> dict[str, Any]:
    """Run deterministic regex extraction first, falling back to LLM only for missing facts."""
    parsed = parse_sms_regex(content, received_at=received_at)

    needs_llm = parsed["sms_category"] != "invoice" and (
        parsed["event_type_guess"] is None or parsed["case_number"] is None
    )
    if not needs_llm:
        return parsed

    client = llm_client or UniversalLLMClient()
    if not client.is_enabled:
        return parsed

    system_prompt = (
        "你是中国律所案件分拣助手。只做客观事实抽取，禁止推算任何法定截止日。"
        "严格输出单个 JSON 对象，可包含字段："
        "case_number, case_category_guess (civil|criminal|administrative|labor_arbitration|commercial_arb|enforcement|non_litigation), "
        "agency, event_type_guess, hearing_time (ISO8601 或 null), client_hint, doc_title_hint, suggested_stage。"
        "无法确认的字段填 null。"
    )
    llm_data = await client.extract_json(system_prompt=system_prompt, user_prompt=content)
    if not llm_data:
        return parsed

    if not parsed["case_number"] and llm_data.get("case_number"):
        parsed["case_number"] = normalize_case_number(str(llm_data["case_number"]))
    for key in (
        "case_category_guess",
        "agency",
        "event_type_guess",
        "hearing_time",
        "client_hint",
        "doc_title_hint",
        "suggested_stage",
    ):
        if not parsed.get(key) and llm_data.get(key):
            val = llm_data[key]
            if isinstance(val, (str, int, float)):
                parsed[key] = str(val).strip()
    if parsed.get("case_category_guess") not in {c.value for c in CaseCategory}:
        parsed["case_category_guess"] = None
    parsed["llm_assisted"] = True
    return parsed


def match_case_for_user(
    session: Session,
    user: User,
    parsed: dict[str, Any],
    extra_text: str = "",
) -> Optional[Case]:
    """Find the best matching accessible ``Case`` for a triage item."""
    accessible_ids = get_accessible_case_ids_filter(user, session)
    if not accessible_ids:
        return None

    stmt = select(Case).where(Case.id.in_(accessible_ids))
    candidates = session.exec(stmt).all()
    if not candidates:
        return None

    target_case_no = normalize_case_number(parsed.get("case_number"))
    if target_case_no:
        for c in candidates:
            if normalize_case_number(c.case_number) == target_case_no:
                return c

    # Secondary match via client_hint or case_number/client_name in combined text
    combined = f"{parsed.get('client_hint') or ''} {extra_text}".strip()
    if combined:
        best_case: Optional[Case] = None
        best_score = 0
        for c in candidates:
            score = 0
            norm_no = normalize_case_number(c.case_number)
            if norm_no and norm_no in normalize_case_number(combined):
                score += 100
            if c.client_name and len(c.client_name.strip()) >= 2 and c.client_name.strip() in combined:
                score += 20
            if c.opponent_name and len(c.opponent_name.strip()) >= 2 and c.opponent_name.strip() in combined:
                score += 10
            if score > best_score:
                best_score = score
                best_case = c
        if best_score > 0:
            return best_case

    return None


def suggest_stage_options(category: Optional[str]) -> list[str]:
    """Return ordered stage list for a category from ``stage_templates.json``."""
    templates = _stage_templates()
    cats = templates.get("categories", {})
    if category and category in cats:
        return list(cats[category].get("stages", []))
    return []
