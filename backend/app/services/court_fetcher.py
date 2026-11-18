"""Three-tier court document auto-fetcher and PDF metadata parser.

Tier 1: Direct API download for national unified delivery (zxfw.court.gov.cn) & Hubei API.
Tier 2: Headless Playwright automation for provincial portals & phone_last4 verification.
Tier 3: Human-in-the-loop fallback (`MANUAL_FETCH_REQUIRED`) on CAPTCHA, facial recognition,
        missing phone_last4, or `confirm_before_sign` policy gate.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import pdfplumber

from app.config import get_settings
from app.services import file_bridge
from app.services.sms_parser import normalize_case_number

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

ZXFW_API_URL = "https://zxfw.court.gov.cn/yzw/yzw-zxfw-sdfw/api/v1/sdfw/getWsListBySdbhNew"
HBFY_API_URL = "http://dzsd.hbfy.gov.cn/delimobile/tDeliSms/findSmsInfo"

_CASE_NO_RE = re.compile(r"[（(〔\[]\s*\d{4}\s*[）)\]〕]\s*[\u4e00-\u9fa5]{1,12}\d*[\u4e00-\u9fa5]{1,8}\d+号?")
_HEARING_TIME_RE = re.compile(
    r"(?:定于|开庭时间|传唤时间|听证时间)[：:\s]*(\d{4}年\d{1,2}月\d{1,2}日\s*(?:上午|下午)?\s*\d{1,2}[时点:]\d{0,2}分?)"
)
_HEARING_LOC_RE = re.compile(
    r"(?:(?:开庭|听证|庭审)?地点[：:\s]*|在(?:本院)?\s*)([\u4e00-\u9fa5A-Za-z0-9第\-]{2,35}(?:法庭|审判庭|调解室|仲裁庭))"
)
_SPECIFIED_DAYS_RE = re.compile(r"举证期限为(?:自收到[\u4e00-\u9fa5]{0,12}之?日?起)?(\d{1,3})日")
_JUDGE_RE = re.compile(r"(?:审\s*判\s*员|审\s*判\s*长|承办法官)[：:\s]*([\u4e00-\u9fa5]{2,5})")
_CLERK_RE = re.compile(r"书\s*记\s*员[：:\s]*([\u4e00-\u9fa5]{2,5})")

_MANUAL_HINTS_MAP = {
    "人脸": "平台要求人脸识别核验，已转人工下载",
    "小程序": "平台要求跳转微信小程序签收，已转人工下载",
    "验证码": "平台要求动态短信或图形验证码，已转人工下载",
    "滑块": "平台触发滑块人机校验，已转人工下载",
    "手机号": "平台需要手机号后4位核验",
    "后四位": "平台需要手机号后4位核验",
    "后4位": "平台需要手机号后4位核验",
}


@dataclass
class CourtFetchResult:
    status: str  # fetched | manual_required | no_links
    tier_used: str  # tier1_api | tier2_playwright | tier3_manual | none
    downloaded_files: list[str] = field(default_factory=list)
    parsed_docs: list[dict[str, Any]] = field(default_factory=list)
    court_name: Optional[str] = None
    sent_at: Optional[str] = None
    manual_reason: Optional[str] = None
    requires_phone_last4: bool = False


def parse_court_pdf_bytes(data: bytes, filename: str) -> dict[str, Any]:
    """Extract procedural metadata from the first 3 pages of a court PDF document."""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages[:3]]
            text = "\n".join(pages_text).strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("pdfplumber failed for %s: %s", filename, exc)

    doc_info: dict[str, Any] = {
        "filename": filename,
        "document_type": filename.rsplit(".", 1)[0],
        "doc_event_type": None,
        "case_no": None,
        "hearing_time": None,
        "hearing_location": None,
        "authority_specified_days": None,
        "judge_name": None,
        "clerk_name": None,
        "has_text": len(text) > 30,
    }
    combined = f"{filename}\n{text}"

    doc_type_map = (
        ("民事判决书", "judgment_served"),
        ("刑事判决书", "judgment_served"),
        ("行政判决书", "judgment_served"),
        ("判决书", "judgment_served"),
        ("裁定书", "ruling_served"),
        ("仲裁裁决书", "arbitration_award_served"),
        ("行政复议决定书", "reconsideration_decision_served"),
        ("举证通知书", "evidence_notice_served"),
        ("举证期限通知书", "evidence_notice_served"),
        ("应诉通知书", "pleading_served"),
        ("起诉状副本", "pleading_served"),
        ("上诉状副本", "pleading_served"),
        ("开庭传票", "hearing_scheduled"),
        ("传票", "hearing_scheduled"),
        ("调查令", "enforcement_notice_served"),
        ("上诉缴费单", "document_served"),
    )
    for kw, ev in doc_type_map:
        if kw in combined:
            doc_info["document_type"] = kw
            doc_info["doc_event_type"] = ev
            break

    if text:
        m_case = _CASE_NO_RE.search(text)
        if m_case:
            doc_info["case_no"] = normalize_case_number(m_case.group(0))

        m_time = _HEARING_TIME_RE.search(text)
        if m_time:
            nums = re.findall(r"\d+", m_time.group(1))
            if len(nums) >= 4:
                y, mo, d, h = int(nums[0]), int(nums[1]), int(nums[2]), int(nums[3])
                mi = int(nums[4]) if len(nums) >= 5 else 0
                if "下午" in m_time.group(1) and h < 12:
                    h += 12
                try:
                    doc_info["hearing_time"] = datetime(
                        y, mo, d, h, mi, tzinfo=SHANGHAI_TZ
                    ).isoformat()
                except ValueError:
                    pass

        m_loc = _HEARING_LOC_RE.search(text)
        if m_loc:
            doc_info["hearing_location"] = m_loc.group(1).strip()

        m_days = _SPECIFIED_DAYS_RE.search(text)
        if m_days:
            doc_info["authority_specified_days"] = int(m_days.group(1))

        m_judge = _JUDGE_RE.search(text)
        if m_judge:
            doc_info["judge_name"] = m_judge.group(1).strip()

        m_clerk = _CLERK_RE.search(text)
        if m_clerk:
            doc_info["clerk_name"] = m_clerk.group(1).strip()

    return doc_info


def _detect_manual_hint(message: str) -> tuple[Optional[str], bool]:
    msg = message or ""
    needs_phone = any(k in msg for k in ("手机号", "后四位", "后4位", "尾号"))
    for kw, desc in _MANUAL_HINTS_MAP.items():
        if kw in msg:
            return desc, needs_phone
    return None, needs_phone


async def _post_zxfw_api(
    qdbh: str,
    sdbh: str,
    sdsin: str,
    phone_last4: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"qdbh": qdbh, "sdbh": sdbh, "sdsin": sdsin}
    if phone_last4:
        payload["sjhmh4w"] = phone_last4
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.post(ZXFW_API_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


async def _download_url_bytes(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


async def _tier1_fetch_zxfw(
    link_info: dict[str, Any],
    *,
    batch_key: str,
    case_number: Optional[str],
    received_date: str,
    phone_last4: Optional[str],
) -> tuple[Optional[CourtFetchResult], Optional[str], bool]:
    """Attempt Tier 1 zxfw API download.

    Returns ``(result_if_resolved, error_reason_if_failed, requires_phone_last4)``.
    """
    params = link_info.get("params") or {}
    qdbh, sdbh, sdsin = params.get("qdbh"), params.get("sdbh"), params.get("sdsin")
    if not (qdbh and sdbh and sdsin):
        return None, "短信链接缺少 qdbh/sdbh/sdsin 参数", False

    try:
        data = await _post_zxfw_api(qdbh, sdbh, sdsin, phone_last4=phone_last4)
    except Exception as exc:  # noqa: BLE001
        return None, f"zxfw API 请求失败: {exc}", False

    if not data.get("success"):
        err_msg = str(data.get("msg") or data.get("message") or "zxfw API 返回失败")
        hint, needs_phone = _detect_manual_hint(err_msg)
        return None, hint or err_msg, needs_phone

    documents = data.get("data") or []
    if not documents:
        return None, "送达平台未返回待下载文书（可能链接已过期或已签收归档）", False

    court_name = documents[0].get("c_fymc")
    sent_at = documents[0].get("dt_cjsj")
    staged_paths: list[str] = []
    parsed_docs: list[dict[str, Any]] = []

    for doc in documents:
        doc_title = str(doc.get("c_wsmc") or "司法文书").strip()
        dl_url = str(doc.get("wjlj") or "").strip()
        fmt = str(doc.get("c_wjgs") or "pdf").strip().lstrip(".") or "pdf"
        if not dl_url:
            continue
        try:
            content_bytes = await _download_url_bytes(dl_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed downloading court doc %s: %s", doc_title, exc)
            continue
        if len(content_bytes) < 64:
            continue

        filename = file_bridge.format_court_doc_filename(
            received_date=received_date,
            case_number=case_number,
            doc_title=doc_title,
            ext=f".{fmt}",
        )
        _, rel_path = file_bridge.save_staged_triage_file(batch_key, filename, content_bytes)
        staged_paths.append(rel_path)
        if fmt.lower() == "pdf":
            parsed_docs.append(parse_court_pdf_bytes(content_bytes, filename))

    if not staged_paths:
        return None, "未能从送达平台下载有效文书文件", False

    return (
        CourtFetchResult(
            status="fetched",
            tier_used="tier1_api",
            downloaded_files=staged_paths,
            parsed_docs=parsed_docs,
            court_name=court_name,
            sent_at=sent_at,
        ),
        None,
        False,
    )


async def _tier2_playwright_fetch(
    link_info: dict[str, Any],
    *,
    batch_key: str,
    case_number: Optional[str],
    received_date: str,
    phone_last4: Optional[str],
    id_card_last4: Optional[str],
    extraction_code: Optional[str],
    confirm_sign: bool,
) -> tuple[Optional[CourtFetchResult], str, bool]:
    """Tier 2 Playwright interactive download for provincial portals and phone_last4 flows."""
    settings = get_settings()
    domain = link_info.get("domain") or ""
    url = link_info.get("url") or ""

    if settings.court_auto_fetch_mode == "confirm_before_sign" and not confirm_sign:
        return (
            None,
            f"平台（{domain}）需要核验并签收文书；因系统配置为「签收前人工确认」，请在收件箱核对信息后填写手机号后4位并点击授权签收重试。",
            not bool(phone_last4),
        )

    if not (phone_last4 or extraction_code or id_card_last4):
        return (
            None,
            f"平台（{domain}）通常需要手机号后4位或短信提取码验证，当前未配置手机号后4位，请提供后重试。",
            True,
        )

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None, "未安装 Playwright 运行时，请点击短信链接人工下载后上传归档。", False

    staged_paths: list[str] = []
    parsed_docs: list[dict[str, Any]] = []
    captured_docs: list[tuple[str, bytes]] = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)

            body_text = await page.inner_text("body")
            hint, needs_phone = _detect_manual_hint(body_text)
            if any(k in body_text for k in ("人脸识别", "微信小程序", "滑块验证", "图形验证码")):
                await browser.close()
                return None, hint or "页面触发人机/人脸验证，请人工打开链接下载", needs_phone

            verify_value = extraction_code or phone_last4 or id_card_last4 or ""
            for selector in (
                "input[placeholder*='后四位']",
                "input[placeholder*='后4位']",
                "input[placeholder*='手机']",
                "input[placeholder*='验证码']",
                "input#checkCode",
                "input[type='text']",
            ):
                loc = page.locator(selector).first
                if await loc.count() > 0 and verify_value:
                    await loc.fill(verify_value)
                    break

            for btn_text in ("确认", "确定", "验证", "查看文书", "签收并查看"):
                btn = page.get_by_role("button", name=re.compile(btn_text))
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(1500)
                    break

            async with page.expect_download(timeout=8000) as dl_info:
                dl_btn = page.get_by_role("button", name=re.compile("下载|保存|PDF"))
                if await dl_btn.count() > 0:
                    await dl_btn.first.click()
                    download = await dl_info.value
                    tmp_file = await download.path()
                    if tmp_file:
                        with open(tmp_file, "rb") as fh:
                            captured_docs.append((download.suggested_filename or "送达文书.pdf", fh.read()))

            await browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.info("Tier 2 Playwright fallback triggered for %s: %s", domain, exc)
        return (
            None,
            f"自动访问送达页面（{domain}）未完成，需人工点击短信链接下载或提供尾号重试。",
            not bool(phone_last4),
        )

    for raw_name, file_bytes in captured_docs:
        filename = file_bridge.format_court_doc_filename(
            received_date=received_date,
            case_number=case_number,
            doc_title=raw_name.rsplit(".", 1)[0],
            ext=".pdf",
        )
        _, rel_path = file_bridge.save_staged_triage_file(batch_key, filename, file_bytes)
        staged_paths.append(rel_path)
        parsed_docs.append(parse_court_pdf_bytes(file_bytes, filename))

    if not staged_paths:
        return None, f"未在送达页面（{domain}）捕获到文书下载，请人工下载。", False

    return (
        CourtFetchResult(
            status="fetched",
            tier_used="tier2_playwright",
            downloaded_files=staged_paths,
            parsed_docs=parsed_docs,
        ),
        "",
        False,
    )


async def fetch_court_documents(
    parsed_sms: dict[str, Any],
    *,
    batch_key: str,
    phone_last4: Optional[str] = None,
    id_card_last4: Optional[str] = None,
    confirm_sign: bool = False,
) -> CourtFetchResult:
    """Execute 3-tier court document fetching sequentially for extracted court links."""
    links: list[dict[str, Any]] = parsed_sms.get("court_links") or []
    if not links:
        return CourtFetchResult(status="no_links", tier_used="none")

    case_number = parsed_sms.get("case_number")
    received_date = str(parsed_sms.get("event_date") or datetime.now(SHANGHAI_TZ).date().isoformat())
    extraction_code = parsed_sms.get("extraction_code")
    reasons: list[str] = []
    needs_phone_any = False
    all_files: list[str] = []
    all_docs: list[dict[str, Any]] = []
    tier_used = "none"
    court_name: Optional[str] = None
    sent_at: Optional[str] = None

    for link in links:
        domain = link.get("domain") or ""
        tier = int(link.get("tier") or 2)

        if domain == "zxfw.court.gov.cn" and tier == 1:
            res, err, needs_phone = await _tier1_fetch_zxfw(
                link,
                batch_key=batch_key,
                case_number=case_number,
                received_date=received_date,
                phone_last4=phone_last4,
            )
            if res is not None:
                all_files.extend(res.downloaded_files)
                all_docs.extend(res.parsed_docs)
                tier_used = res.tier_used
                court_name = court_name or res.court_name
                sent_at = sent_at or res.sent_at
                continue
            needs_phone_any = needs_phone_any or needs_phone
            # Strict serial fallback to Tier 2 when Tier 1 fails
            t2_res, t2_err, t2_needs_phone = await _tier2_playwright_fetch(
                link,
                batch_key=batch_key,
                case_number=case_number,
                received_date=received_date,
                phone_last4=phone_last4,
                id_card_last4=id_card_last4,
                extraction_code=extraction_code,
                confirm_sign=confirm_sign,
            )
            if t2_res is not None:
                all_files.extend(t2_res.downloaded_files)
                all_docs.extend(t2_res.parsed_docs)
                tier_used = t2_res.tier_used
                continue
            reasons.append(err or t2_err)
            needs_phone_any = needs_phone_any or t2_needs_phone
        else:
            t2_res, t2_err, t2_needs_phone = await _tier2_playwright_fetch(
                link,
                batch_key=batch_key,
                case_number=case_number,
                received_date=received_date,
                phone_last4=phone_last4,
                id_card_last4=id_card_last4,
                extraction_code=extraction_code,
                confirm_sign=confirm_sign,
            )
            if t2_res is not None:
                all_files.extend(t2_res.downloaded_files)
                all_docs.extend(t2_res.parsed_docs)
                tier_used = t2_res.tier_used
                continue
            reasons.append(t2_err)
            needs_phone_any = needs_phone_any or t2_needs_phone

    if all_files:
        return CourtFetchResult(
            status="fetched",
            tier_used=tier_used,
            downloaded_files=all_files,
            parsed_docs=all_docs,
            court_name=court_name,
            sent_at=sent_at,
        )

    return CourtFetchResult(
        status="manual_required",
        tier_used="tier3_manual",
        manual_reason="；".join(r for r in reasons if r) or "需要人工打开送达链接下载文书",
        requires_phone_last4=needs_phone_any,
    )
