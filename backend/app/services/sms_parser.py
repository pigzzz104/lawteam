"""Multi-agency SMS classification, regex extraction, optional LLM fallback, and case matching.

Supports Court (12368/zxfw), Procuratorate (12309), Public Security/Detention Center,
Arbitration Commission, and E-Invoice SMS without trusting sender numbers alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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

_CASE_NO_NORMALIZE_TRANS = str.maketrans(
    {"(": "（", ")": "）", "[": "（", "]": "）", "〔": "（", "〕": "）", "【": "（", "】": "）"}
)
_TRAILING_PUNCT = "，。；！!）,.)）\"'、\r\n"

_COMPANY_LEGAL_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "集团有限公司",
    "控股有限公司",
    "合伙企业",
    "有限合伙",
    "普通合伙",
    "有限公司",
    "股份公司",
    "集团公司",
    "工作室",
    "事务所",
    "研究院",
    "中心",
    "集团",
    "公司",
)
_COMPANY_INDUSTRY_SUFFIXES = (
    "电子商务",
    "信息技术",
    "机电工程",
    "网络科技",
    "文化传播",
    "投资管理",
    "企业管理",
    "管理",
    "投资",
    "科技",
    "信息",
    "网络",
    "软件",
    "电子",
    "机电",
    "工程",
    "会展",
    "咨询",
    "服务",
    "实业",
    "商贸",
    "贸易",
    "物流",
    "建筑",
    "装饰",
    "医药",
    "能源",
    "汽车",
    "文化",
)
_REGION_PREFIXES = (
    "北京市",
    "上海市",
    "天津市",
    "重庆市",
    "深圳市",
    "广州市",
    "杭州市",
    "成都市",
    "武汉市",
    "南京市",
    "苏州市",
    "中国",
    "北京",
    "上海",
    "天津",
    "重庆",
    "深圳",
    "广州",
    "杭州",
    "成都",
    "武汉",
    "南京",
    "苏州",
    "宁波",
    "厦门",
    "青岛",
    "长沙",
    "郑州",
    "西安",
    "广东",
    "江苏",
    "浙江",
    "四川",
    "湖北",
    "山东",
    "福建",
)

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
    ("enforcement", "preservation_bank_done"): "查封处置/续封盯防",
    ("enforcement", "preservation_movable_done"): "查封处置/续封盯防",
    ("enforcement", "preservation_real_estate_done"): "查封处置/续封盯防",
    ("enforcement", "enforcement_settlement_signed"): "终本/执行完毕",
    ("non_litigation", "retainer_started"): "尽调/起草阶段",
    ("non_litigation", "project_milestone_started"): "谈判/交付",
    ("non_litigation", "payment_notice_issued"): "项目完结",
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


def _case_number_match_keys(raw: Optional[str]) -> set[str]:
    norm = normalize_case_number(raw)
    if not norm:
        return set()
    keys = {norm}
    no_di = re.sub(r"第(\d+号?)$", r"\1", norm)
    keys.add(no_di)
    m_pref = re.match(r"^([\u4e00-\u9fa5]+)(（\d{4}）)(.+)$", no_di)
    if m_pref:
        keys.add(f"{m_pref.group(2)}{m_pref.group(1)}{m_pref.group(3)}")
    return {k for k in keys if len(k) >= 6}


def _strip_company_suffixes(name: str) -> str:
    val = re.sub(r"[\s()（）]+", "", (name or "").strip())
    changed = True
    while changed and val:
        changed = False
        for sfx in _COMPANY_LEGAL_SUFFIXES:
            if val.endswith(sfx) and len(val) > len(sfx) + 1:
                val = val[: -len(sfx)]
                changed = True
                break
    return val


def _client_short_aliases(client_name: str) -> list[str]:
    """Generate distinct short corporate aliases (>=3 chars) for tolerant matching."""
    base = _strip_company_suffixes(client_name)
    if len(base) < 3:
        return []

    variants: list[str] = [base]
    cur = base
    for _ in range(2):
        trimmed = cur
        for ind in _COMPANY_INDUSTRY_SUFFIXES:
            if cur.endswith(ind) and len(cur) - len(ind) >= 3:
                trimmed = cur[: -len(ind)]
                break
        if trimmed != cur:
            variants.append(trimmed)
            cur = trimmed
        else:
            break

    expanded: list[str] = []
    seen: set[str] = set()
    for item in variants:
        if len(item) >= 3 and item not in seen:
            seen.add(item)
            expanded.append(item)
        for pref in _REGION_PREFIXES:
            if item.startswith(pref) and len(item) - len(pref) >= 3:
                no_reg = item[len(pref) :]
                if no_reg not in seen:
                    seen.add(no_reg)
                    expanded.append(no_reg)
    return expanded


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
    # 1. Explicit service/detention/header receipt date (never match future hearing dates or issuance dates)
    explicit_patterns = (
        # Leading receipt timestamp header e.g. "张晨律师 2026年06月12日 17:03 【上海市..."
        r"^\s*[^\n【\[]{0,24}?(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?=\s+\d{1,2}:\d{2}|\s*[【\[])",
        # Explicit labeled service/receipt/detention date
        r"(?:送达日期|签收日期|收到日期|刑拘日期|拘留日期|执行日期)[：:\s]*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?",
        # Explicit past action e.g. "已于2026-11-10被刑事拘留 / 送达 / 签收 / 冻结 / 查封"
        r"(?:已于|于)\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s*(?:被?(?:刑事拘留|刑拘|拘留|逮捕|取保|送达|签收|收到|冻结|查封|移送))",
    )
    for pat in explicit_patterns:
        m = re.search(pat, content)
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                return d.isoformat()
            except ValueError:
                continue

    base_dt = fallback_dt or datetime.now(timezone.utc)
    if base_dt.tzinfo is None:
        base_dt = base_dt.replace(tzinfo=timezone.utc)
    return base_dt.astimezone(SHANGHAI_TZ).date().isoformat()


def _extract_doc_title_hint(content: str) -> Optional[str]:
    # e.g. 送达文书（上诉缴费单） or 送达调查令 (specific document names before generic 送达...)
    m_paren = re.search(r"送达文书[（(]([^）)]+)[）)]", content)
    if m_paren:
        return m_paren.group(1).strip()
    for title_kw in (
        "民事判决书",
        "刑事判决书",
        "行政判决书",
        "执行异议裁定书",
        "执行裁定书",
        "民事裁定书",
        "刑事裁定书",
        "行政裁定书",
        "裁定书",
        "仲裁裁决书",
        "行政复议决定书",
        "执行通知书",
        "报告财产令",
        "限期履行通知书",
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
    m_sub = re.search(r"送达([\u4e00-\u9fa5]{2,14})", content)
    if m_sub:
        sub_val = m_sub.group(1).strip()
        if sub_val != "文书":
            return sub_val
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
    # Judicial portals (court/12309/arbitration links) take precedence over incidental invoice mentions
    for link_rule in patterns.get("download_link_patterns", []):
        pat = link_rule.get("pattern")
        if pat and re.search(pat, content):
            domain = str(link_rule.get("canonical_domain") or "")
            if domain == "12309.gov.cn":
                return "procuratorate_12309"
            if domain == "arbitration_portal":
                return "arbitration"
            return "court_service"

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
        "enforcement_objection_served",
        "ruling_served",
        "arbitration_award_served",
        "reconsideration_decision_served",
        "evidence_notice_served",
        "pleading_served",
        "enforcement_notice_served",
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
    if "送达文书" in content or (
        "查收" in content
        and any(
            k in content
            for k in ("法院", "检察", "仲裁", "司法", "文书", "传票", "裁定", "判决", "通知书", "调查令", "zxfw")
        )
    ):
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

    # Generic http link fallback for 12309 / arbitration / court notices not matching fixed portals
    if sms_category in ("court_service", "procuratorate_12309", "arbitration") and not court_links:
        for m in re.finditer(r"https?://[^\s<>'\"]+", clean_text):
            url = m.group(0).rstrip(_TRAILING_PUNCT)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            domain = urlparse(url).netloc.lower() or "generic_portal"
            court_links.append(
                {
                    "domain": domain,
                    "description": "电子送达直链" if sms_category != "arbitration" else "仲裁电子送达链接",
                    "tier": 1,
                    "download_strategy": "direct_or_playwright",
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


@dataclass(frozen=True)
class CaseMatchResult:
    """Outcome of matching a triage payload against accessible cases."""

    case: Optional[Case] = None
    confidence: str = "none"  # exact_case_number | unique_client | ambiguous | none
    candidate_case_ids: list[int] = field(default_factory=list)
    reason: Optional[str] = None


_SUGGESTED_STAGE_BY_SUBTYPE_EVENT: dict[tuple[str, str, str], str] = {
    ("civil", "二审", "pleading_served"): "举证质证",
    ("civil", "二审", "evidence_notice_served"): "举证质证",
    ("civil", "二审", "hearing_scheduled"): "开庭审理",
    ("civil", "二审", "judgment_served"): "裁判生效",
    ("civil", "二审", "ruling_served"): "裁判生效",
    ("civil", "再审", "hearing_scheduled"): "审查听证",
    ("civil", "再审", "judgment_served"): "待裁判",
    ("civil", "再审", "ruling_served"): "待裁判",
    ("criminal", "刑事一审", "pleading_served"): "收到起诉书",
    ("criminal", "刑事一审", "hearing_scheduled"): "开庭审理",
    ("criminal", "刑事一审", "judgment_served"): "宣判与10日上诉期",
    ("criminal", "刑事一审", "ruling_served"): "宣判与10日上诉期",
    ("criminal", "刑事二审", "hearing_scheduled"): "二审开庭",
    ("criminal", "刑事二审", "judgment_served"): "终审生效归档",
    ("criminal", "刑事二审", "ruling_served"): "终审生效归档",
    ("administrative", "行政复议", "reconsideration_decision_served"): "复议决定(15日起诉期)",
    ("administrative", "行政二审", "hearing_scheduled"): "开庭审理",
    ("administrative", "行政二审", "judgment_served"): "终审归档",
}


def suggest_stage_for_event(
    case_category: Optional[str],
    event_type: Optional[str],
    *,
    procedure_subtype: Optional[str] = None,
    current_stage: Optional[str] = None,
) -> Optional[str]:
    """Return suggested procedural stage for `(case_category, procedure_subtype, event_type)` without regressing."""
    if not case_category or not event_type:
        return None
    templates = _stage_templates().get("categories", {})
    cat_cfg = templates.get(case_category, {})
    subtypes_map = cat_cfg.get("subtypes", {}) if isinstance(cat_cfg, dict) else {}
    stages: list[str] = list(
        (subtypes_map.get(procedure_subtype) if procedure_subtype else None)
        or cat_cfg.get("stages", [])
    )

    candidate: Optional[str] = None
    if procedure_subtype:
        candidate = _SUGGESTED_STAGE_BY_SUBTYPE_EVENT.get(
            (case_category, procedure_subtype, event_type)
        )
    if candidate is None:
        fallback = _SUGGESTED_STAGE_BY_EVENT.get((case_category, event_type))
        if fallback and (not stages or fallback in stages):
            candidate = fallback

    if not candidate:
        return None
    if stages and current_stage in stages and candidate in stages:
        if stages.index(candidate) <= stages.index(current_stage):
            return None
    return candidate


def match_case_with_confidence(
    session: Session,
    user: User,
    parsed: dict[str, Any],
    extra_text: str = "",
) -> CaseMatchResult:
    """Find matching accessible ``Case``(s) and return match confidence for auto-archiving."""
    accessible_ids = get_accessible_case_ids_filter(user, session)
    if not accessible_ids:
        return CaseMatchResult(confidence="none", reason="当前无可用在办案件")

    candidates = list(session.exec(select(Case).where(Case.id.in_(accessible_ids))).all())
    if not candidates:
        return CaseMatchResult(confidence="none", reason="当前无可用在办案件")

    active_candidates = [c for c in candidates if not c.is_archived]
    pool = active_candidates or candidates

    target_case_no = normalize_case_number(parsed.get("case_number"))
    combined = f"{parsed.get('client_hint') or ''} {extra_text}".strip()
    norm_combined = normalize_case_number(combined) or ""

    # 1. Exact & prefix/bracket-normalized case_number match (prefer active cases)
    exact_matches: list[Case] = []
    if target_case_no:
        target_keys = _case_number_match_keys(target_case_no)
        exact_matches = [
            c for c in active_candidates if normalize_case_number(c.case_number) == target_case_no
        ]
        if not exact_matches:
            exact_matches = [
                c for c in active_candidates if bool(_case_number_match_keys(c.case_number) & target_keys)
            ]
        if not exact_matches:
            exact_matches = [
                c
                for c in candidates
                if normalize_case_number(c.case_number) == target_case_no
                or bool(_case_number_match_keys(c.case_number) & target_keys)
            ]
    if not exact_matches and norm_combined:
        exact_matches = [
            c
            for c in pool
            if c.case_number
            and any(k in norm_combined for k in _case_number_match_keys(c.case_number))
        ]

    if len(exact_matches) == 1:
        return CaseMatchResult(case=exact_matches[0], confidence="exact_case_number")
    if len(exact_matches) > 1:
        return CaseMatchResult(
            case=None,
            confidence="ambiguous",
            candidate_case_ids=[c.id for c in exact_matches if c.id is not None],
            reason="案号命中多个案件，请人工确认归属案件",
        )

    # 2. Client name & corporate short-alias match among active cases
    if combined:
        client_matches = [
            c
            for c in active_candidates
            if c.client_name and len(c.client_name.strip()) >= 2 and c.client_name.strip() in combined
        ]
        if len(client_matches) == 1:
            return CaseMatchResult(case=client_matches[0], confidence="unique_client")
        if len(client_matches) > 1:
            return CaseMatchResult(
                case=None,
                confidence="ambiguous",
                candidate_case_ids=[c.id for c in client_matches if c.id is not None],
                reason=f"委托人「{client_matches[0].client_name}」名下有 {len(client_matches)} 个在办案件，请人工指定归属",
            )

        compact_combined = re.sub(r"[\s()（）]+", "", combined)
        buyer_name = str((parsed.get("invoice_meta") or {}).get("buyer_name") or "").strip()
        buyer_stripped = _strip_company_suffixes(buyer_name) if buyer_name else ""
        alias_matches: list[Case] = []
        for c in active_candidates:
            if not c.client_name:
                continue
            aliases = _client_short_aliases(c.client_name)
            hit = any(alias in compact_combined for alias in aliases)
            if not hit and len(buyer_stripped) >= 3:
                hit = buyer_stripped in _strip_company_suffixes(c.client_name)
            if hit:
                alias_matches.append(c)
        if len(alias_matches) == 1:
            return CaseMatchResult(
                case=alias_matches[0],
                confidence="unique_client_alias",
                reason="按委托人企业简称唯一匹配",
            )
        if len(alias_matches) > 1:
            return CaseMatchResult(
                case=None,
                confidence="ambiguous",
                candidate_case_ids=[c.id for c in alias_matches if c.id is not None],
                reason=f"简称命中 {len(alias_matches)} 个在办案件，请人工确认归属",
            )

        opponent_matches = [
            c
            for c in active_candidates
            if c.opponent_name and len(c.opponent_name.strip()) >= 2 and c.opponent_name.strip() in combined
        ]
        if len(opponent_matches) == 1:
            return CaseMatchResult(
                case=opponent_matches[0],
                confidence="unique_opponent",
                reason="仅匹配到对方当事人名称，建议人工核对案件归属",
            )
        if len(opponent_matches) > 1:
            return CaseMatchResult(
                case=None,
                confidence="ambiguous",
                candidate_case_ids=[c.id for c in opponent_matches if c.id is not None],
                reason="对方当事人命中多个在办案件，请人工指定归属",
            )

    return CaseMatchResult(confidence="none", reason="未匹配到在办案件，请人工选择或新建案件")


def match_case_for_user(
    session: Session,
    user: User,
    parsed: dict[str, Any],
    extra_text: str = "",
) -> Optional[Case]:
    """Find the best matching accessible ``Case`` for a triage item."""
    return match_case_with_confidence(session, user, parsed, extra_text=extra_text).case


def suggest_stage_options(category: Optional[str]) -> list[str]:
    """Return ordered stage list for a category from ``stage_templates.json``."""
    templates = _stage_templates()
    cats = templates.get("categories", {})
    if category and category in cats:
        return list(cats[category].get("stages", []))
    return []
