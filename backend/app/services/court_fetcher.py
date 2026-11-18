"""Three-tier court document auto-fetcher and PDF metadata parser.

Tier 1: Direct API download for national unified delivery (zxfw.court.gov.cn) & Hubei API (dzsd.hbfy.gov.cn).
Tier 2: Headless Playwright automation for provincial portals (GDEMS/JYSD/SFDW/HBFY) & phone_last4 flows.
Tier 3: Human-in-the-loop fallback (`MANUAL_FETCH_REQUIRED`) on CAPTCHA, facial recognition,
        missing phone_last4, or `confirm_before_sign` / `auto_non_judgment` policy gate.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
import pdfplumber

from app.config import get_settings
from app.core.url_safety import (
    UnsafeUrlError,
    safe_async_get,
    validate_external_fetch_url,
)
from app.services import file_bridge
from app.services.ocr_service import MIN_TEXT_CHARS, extract_scanned_pdf_text
from app.services.sms_parser import normalize_case_number

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

ZXFW_API_URL = "https://zxfw.court.gov.cn/yzw/yzw-zxfw-sdfw/api/v1/sdfw/getWsListBySdbhNew"
HBFY_API_URL = "http://dzsd.hbfy.gov.cn/delimobile/tDeliSms/findSmsInfo"

_CASE_NO_RE = re.compile(
    r"(?:(?:(?![的在查收收到代理案号文您你请])[\u4e00-\u9fa5]){1,6}(?:劳人仲|仲[案裁]?字?|案字))?[（(〔\[]\s*\d{4}\s*[）)\]〕]\s*[\u4e00-\u9fa5A-Za-z0-9]{1,14}第?\d+号?"
)
_HEARING_TIME_RE = re.compile(
    r"(?:定于|开庭时间|传唤时间|听证时间)[：:\s]*(\d{4}年\d{1,2}月\d{1,2}日\s*(?:上午|下午)?\s*\d{1,2}[时点:]\d{0,2}分?)"
)
_HEARING_LOC_RE = re.compile(
    r"(?:(?:开庭|听证|庭审)?地点[：:\s]*|在(?:本院)?\s*)([\u4e00-\u9fa5A-Za-z0-9第\-]{2,35}(?:法庭|审判庭|调解室|仲裁庭))"
)
_SPECIFIED_DAYS_RE = re.compile(
    r"举证期限(?:为|不少于)?(?:自收到[\u4e00-\u9fa5]{0,18}?之?\s*日?\s*起)?\s*(\d{1,3})\s*[日天]"
)
_SPECIFIED_CN_DAYS_RE = re.compile(
    r"举证期限(?:为|不少于)?(?:自收到[\u4e00-\u9fa5]{0,18}?之?\s*日?\s*起)?\s*([一二三四五六七八九十廿卅]{1,3})\s*[日天]"
)
_CN_NUM_VALUES = {
    "五": 5,
    "七": 7,
    "十": 10,
    "十二": 12,
    "十五": 15,
    "二十": 20,
    "三十": 30,
    "四十五": 45,
    "六十": 60,
}
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
_JUDGMENT_KEYWORDS = ("判决书", "裁定书", "调解书", "决定书", "裁决书")
_ALLOWED_DOC_EXTS = {".pdf", ".ofd", ".doc", ".docx", ".txt", ".jpg", ".jpeg", ".png"}
_MAX_ZIP_ENTRIES = 30
_MAX_ZIP_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 32 * 1024 * 1024


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


def _extract_specified_days(text: str) -> Optional[int]:
    m_days = _SPECIFIED_DAYS_RE.search(text)
    if m_days:
        return int(m_days.group(1))
    m_cn = _SPECIFIED_CN_DAYS_RE.search(text)
    if m_cn and m_cn.group(1) in _CN_NUM_VALUES:
        return _CN_NUM_VALUES[m_cn.group(1)]
    return None


def _extract_court_fields_from_text(
    text: str,
    filename: str,
    *,
    has_text: bool,
    is_scanned: bool = False,
    ocr_used: bool = False,
    ocr_source: Optional[str] = None,
) -> dict[str, Any]:
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
        "has_text": has_text,
        "is_scanned": is_scanned,
        "ocr_used": ocr_used,
        "ocr_source": ocr_source,
    }
    combined = f"{filename}\n{text}"

    doc_type_map = (
        ("民事判决书", "judgment_served"),
        ("刑事判决书", "judgment_served"),
        ("行政判决书", "judgment_served"),
        ("判决书", "judgment_served"),
        ("执行异议裁定书", "enforcement_objection_served"),
        ("裁定书", "ruling_served"),
        ("仲裁裁决书", "arbitration_award_served"),
        ("裁决书", "arbitration_award_served"),
        ("行政复议决定书", "reconsideration_decision_served"),
        ("复议决定书", "reconsideration_decision_served"),
        ("执行通知书", "enforcement_notice_served"),
        ("报告财产令", "enforcement_notice_served"),
        ("限期履行通知书", "enforcement_notice_served"),
        ("举证通知书", "evidence_notice_served"),
        ("举证期限通知书", "evidence_notice_served"),
        ("应诉通知书", "pleading_served"),
        ("起诉状副本", "pleading_served"),
        ("上诉状副本", "pleading_served"),
        ("开庭传票", "hearing_scheduled"),
        ("传票", "hearing_scheduled"),
        ("调查令", "document_served"),
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

        doc_info["authority_specified_days"] = _extract_specified_days(text)

        m_judge = _JUDGE_RE.search(text)
        if m_judge:
            doc_info["judge_name"] = m_judge.group(1).strip()

        m_clerk = _CLERK_RE.search(text)
        if m_clerk:
            doc_info["clerk_name"] = m_clerk.group(1).strip()

    return doc_info


def _extract_pdf_text_sync(data: bytes, filename: str) -> str:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages[:3]]
            return "\n".join(pages_text).strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("pdfplumber failed for %s: %s", filename, exc)
        return ""


def parse_court_pdf_bytes(data: bytes, filename: str) -> dict[str, Any]:
    """Extract procedural metadata from the first 3 pages of a court PDF document."""
    text = _extract_pdf_text_sync(data, filename)
    compact_len = len(re.sub(r"\s+", "", text))
    has_text = compact_len >= MIN_TEXT_CHARS
    return _extract_court_fields_from_text(
        text,
        filename,
        has_text=has_text,
        is_scanned=not has_text,
    )


async def parse_court_pdf_bytes_with_ocr(data: bytes, filename: str) -> dict[str, Any]:
    """Extract metadata from PDF text layer, falling back to VLM/local OCR on scanned PDFs."""
    info = parse_court_pdf_bytes(data, filename)
    if info.get("has_text"):
        return info

    ocr_res = await extract_scanned_pdf_text(data, max_pages=2)
    if not ocr_res.has_meaningful_text:
        return info

    return _extract_court_fields_from_text(
        ocr_res.text,
        filename,
        has_text=True,
        is_scanned=True,
        ocr_used=True,
        ocr_source=ocr_res.source,
    )


def _detect_manual_hint(message: str) -> tuple[Optional[str], bool]:
    msg = message or ""
    needs_phone = any(k in msg for k in ("手机号", "后四位", "后4位", "尾号"))
    for kw, desc in _MANUAL_HINTS_MAP.items():
        if kw in msg:
            return desc, needs_phone
    return None, needs_phone


def _unpack_zip_docs(zip_bytes: bytes) -> list[tuple[str, bytes]]:
    """Safely unpack court documents from a ZIP package with ZipSlip protection."""
    docs: list[tuple[str, bytes]] = []
    total_size = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist()[:_MAX_ZIP_ENTRIES]:
                if info.is_dir():
                    continue
                norm_name = info.filename.replace("\\", "/")
                if norm_name.startswith("/") or ".." in norm_name.split("/"):
                    continue
                if info.file_size <= 0 or info.file_size > _MAX_ZIP_MEMBER_BYTES:
                    continue
                total_size += info.file_size
                if total_size > _MAX_ZIP_TOTAL_BYTES:
                    break
                leaf_name = Path(norm_name).name
                ext = Path(leaf_name).suffix.lower()
                if ext not in _ALLOWED_DOC_EXTS:
                    continue
                raw = zf.read(info.filename)
                if len(raw) >= 64:
                    docs.append((leaf_name, raw))
    except zipfile.BadZipFile:
        return []
    return docs


async def _stage_captured_docs(
    captured_docs: list[tuple[str, bytes]],
    *,
    batch_key: str,
    case_number: Optional[str],
    received_date: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    staged_paths: list[str] = []
    parsed_docs: list[dict[str, Any]] = []
    for raw_name, file_bytes in captured_docs:
        ext = Path(raw_name).suffix.lower()
        if ext not in _ALLOWED_DOC_EXTS:
            ext = ".pdf"
        filename = file_bridge.format_court_doc_filename(
            received_date=received_date,
            case_number=case_number,
            doc_title=raw_name.rsplit(".", 1)[0] or "送达文书",
            ext=ext,
        )
        _, rel_path = file_bridge.save_staged_triage_file(batch_key, filename, file_bytes)
        staged_paths.append(rel_path)
        if ext == ".pdf":
            parsed_docs.append(await parse_court_pdf_bytes_with_ocr(file_bytes, filename))
    return staged_paths, parsed_docs


async def _post_zxfw_api(
    qdbh: str,
    sdbh: str,
    sdsin: str,
    phone_last4: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"qdbh": qdbh, "sdbh": sdbh, "sdsin": sdsin}
    if phone_last4:
        payload["sjhmh4w"] = phone_last4
    headers = {
        "Origin": "https://zxfw.court.gov.cn",
        "Referer": "https://zxfw.court.gov.cn/zxfw/",
    }
    async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers=headers) as client:
        resp = await client.post(ZXFW_API_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


async def _post_hbfy_api(msg: str) -> dict[str, Any]:
    ts = int(time.time() * 1000)
    headers = {"Referer": "http://dzsd.hbfy.gov.cn/deli-mobile-ui/"}
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resp = await client.post(f"{HBFY_API_URL}?t={ts}", json={"msg": msg})
        resp.raise_for_status()
        return resp.json()


async def _download_url_bytes(url: str, *, headers: Optional[dict[str, str]] = None) -> bytes:
    resp = await safe_async_get(
        url,
        headers=headers,
        timeout=30.0,
        require_portal_allowlist=False,
    )
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
            parsed_docs.append(await parse_court_pdf_bytes_with_ocr(content_bytes, filename))

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


def _extract_hbfy_msg(link_info: dict[str, Any]) -> Optional[str]:
    params = link_info.get("params") or {}
    if params.get("msg"):
        return str(params["msg"]).strip()
    url = str(link_info.get("url") or "")
    parsed = urlparse(url)
    qs_msg = (parse_qs(parsed.query).get("msg") or [None])[0]
    if qs_msg:
        return str(qs_msg).strip()
    m = re.search(r"msg=([^&#\s]+)", url)
    return m.group(1).strip() if m else None


async def _tier1_fetch_hbfy(
    link_info: dict[str, Any],
    *,
    batch_key: str,
    case_number: Optional[str],
    received_date: str,
) -> tuple[Optional[CourtFetchResult], Optional[str], bool]:
    """Attempt Tier 1 Hubei court direct SMS API download (`dzsd.hbfy.gov.cn`)."""
    msg = _extract_hbfy_msg(link_info)
    if not msg:
        return None, "湖北送达链接缺少 msg 参数", False

    try:
        payload = await _post_hbfy_api(msg)
    except Exception as exc:  # noqa: BLE001
        return None, f"湖北电子送达 API 请求失败: {exc}", False

    inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(inner, dict):
        return None, "湖北电子送达 API 返回格式异常", False

    if str(inner.get("isNeedCaptcha") or "").upper() == "Y":
        return None, "湖北电子送达平台要求图形验证码核验，请人工点击链接下载后上传归档", False

    doc_list = inner.get("docList") or []
    if not doc_list:
        err_msg = str(payload.get("msg") or payload.get("message") or "")
        return None, err_msg or "湖北电子送达平台未返回待下载文书", False

    captured: list[tuple[str, bytes]] = []
    headers = {"Referer": "http://dzsd.hbfy.gov.cn/deli-mobile-ui/"}
    for item in doc_list:
        if not isinstance(item, dict):
            continue
        doc_title = str(item.get("docName") or "湖北送达文书").strip()
        rel_dl = str(item.get("downloadPath") or item.get("pdfPath") or "").strip()
        if not rel_dl:
            continue
        dl_url = (
            rel_dl
            if rel_dl.startswith("http")
            else f"http://dzsd.hbfy.gov.cn/delimobile/{rel_dl.lstrip('/')}"
        )
        try:
            content_bytes = await _download_url_bytes(dl_url, headers=headers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed downloading HBFY doc %s: %s", doc_title, exc)
            continue
        if len(content_bytes) < 64:
            continue
        if content_bytes.startswith(b"PK\x03\x04") and not doc_title.lower().endswith(".ofd"):
            unpacked = _unpack_zip_docs(content_bytes)
            if unpacked:
                captured.extend(unpacked)
                continue
        ext = Path(rel_dl.split("?")[0]).suffix.lower() or ".pdf"
        if ext not in _ALLOWED_DOC_EXTS:
            ext = ".pdf"
        name = doc_title if doc_title.lower().endswith(ext) else f"{doc_title}{ext}"
        captured.append((name, content_bytes))

    if not captured:
        return None, "未能从湖北电子送达平台下载有效文书文件", False

    staged_paths, parsed_docs = await _stage_captured_docs(
        captured,
        batch_key=batch_key,
        case_number=case_number,
        received_date=received_date,
    )
    return (
        CourtFetchResult(
            status="fetched",
            tier_used="tier1_api",
            downloaded_files=staged_paths,
            parsed_docs=parsed_docs,
            court_name=inner.get("courtName") or "湖北法院电子送达平台",
            sent_at=inner.get("sendTime"),
        ),
        None,
        False,
    )


async def _collect_download(
    page: Any,
    click_action: Any,
    captured_docs: list[tuple[str, bytes]],
    *,
    timeout: int = 12000,
) -> bool:
    try:
        async with page.expect_download(timeout=timeout) as dl_info:
            await click_action()
        download = await dl_info.value
        tmp_file = await download.path()
        if not tmp_file:
            return False
        with open(tmp_file, "rb") as fh:
            raw_bytes = fh.read()
        sug = download.suggested_filename or "送达文书.pdf"
        if raw_bytes.startswith(b"PK\x03\x04") and sug.lower().endswith(".zip"):
            unpacked = _unpack_zip_docs(raw_bytes)
            if unpacked:
                captured_docs.extend(unpacked)
                return True
        captured_docs.append((sug, raw_bytes))
        return True
    except Exception:  # noqa: BLE001
        return False


async def _pw_fetch_gdems(
    page: Any,
    verify_value: str,
    captured_docs: list[tuple[str, bytes]],
) -> Optional[str]:
    """Playwright adapter for `sd.gdems.com` (Guangdong EMS court delivery)."""
    if verify_value:
        verify_input = page.locator(
            "input[placeholder*='后4位'], input[placeholder*='手机'], "
            "input[placeholder*='提取码'], input[placeholder*='验证码'], input[type='text']"
        )
        if await verify_input.count() > 0:
            await verify_input.first.fill(verify_value)
    confirm_loc = page.locator(
        "#submit-btn, #confirm-btn, .submit-btn, .confirm-btn, "
        "button:has-text('确认并预览材料'), button:has-text('确认')"
    )
    if await confirm_loc.count() > 0:
        await confirm_loc.first.click(timeout=5000)
        await page.wait_for_timeout(1500)
    else:
        dl_probe = page.locator("a.downloadPackClass, label a:has(img), a:has-text('送达材料')")
        if await dl_probe.count() <= 0:
            return "广东送达页面暂未开放材料下载或需人工确认通知内容"

    for sel in (
        "a.downloadPackClass",
        "label a:has(img)",
        "a:has-text('送达材料')",
        "a:has-text('下载')",
        "button:has-text('下载')",
        "[title*='下载']",
    ):
        loc = page.locator(sel)
        if await loc.count() <= 0:
            continue
        if await _collect_download(
            page, lambda target=loc.first: target.click(timeout=5000), captured_docs
        ):
            break
    return None


async def _pw_fetch_jysd(
    page: Any,
    verify_value: str,
    captured_docs: list[tuple[str, bytes]],
) -> Optional[str]:
    """Playwright adapter for `jysd.10102368.com` (`sifayun.com` iframe)."""
    target_frame = page.main_frame
    for fr in page.frames:
        if "sifayun.com" in (fr.url or "") or await fr.locator("input[placeholder*='手机']").count() > 0:
            target_frame = fr
            break

    phone_input = target_frame.locator("input[placeholder*='手机']")
    if await phone_input.count() > 0 and verify_value:
        await phone_input.first.fill(verify_value)
        login_btn = target_frame.locator("button:has-text('登录'), button:has-text('确认')")
        if await login_btn.count() > 0:
            await login_btn.first.click(timeout=5000)
            await page.wait_for_timeout(1800)

    detail_btn = target_frame.locator("button:has-text('查看文书详情')")
    if await detail_btn.count() > 0:
        await detail_btn.first.click(timeout=5000)
        await page.wait_for_timeout(1500)

    rows = target_frame.locator(".el-table__body-wrapper tr.el-table__row")
    if await rows.count() <= 0:
        rows = target_frame.locator(".el-table__body tr")
    row_count = await rows.count()
    if row_count > 0:
        for idx in range(row_count):
            row = rows.nth(idx)
            dl_btn = row.locator("button:has-text('下载')")
            if await dl_btn.count() > 0:
                ok = await _collect_download(
                    page, lambda btn=dl_btn.first: btn.click(timeout=5000), captured_docs
                )
                if ok:
                    continue
            # Modal verification confirm button if triggered
            modal_btn = target_frame.locator(
                ".checkFileDialog button:has-text('下载文书并核验'), button:has-text('下载文书并核验')"
            )
            if await modal_btn.count() > 0:
                await _collect_download(
                    page, lambda btn=modal_btn.first: btn.click(timeout=5000), captured_docs
                )
    else:
        dl_btns = target_frame.locator("button:has-text('下载')")
        for idx in range(await dl_btns.count()):
            await _collect_download(
                page, lambda btn=dl_btns.nth(idx): btn.click(timeout=5000), captured_docs
            )
    return None


async def _pw_fetch_sfdw(
    page: Any,
    verify_value: str,
    captured_docs: list[tuple[str, bytes]],
) -> Optional[str]:
    """Playwright adapter for `sfpt.cdfy12368.gov.cn` & TDH SFDW portals."""
    code_input = page.locator("input#checkCode, input[placeholder*='验证码'], input[placeholder*='手机']")
    if await code_input.count() > 0 and verify_value:
        await code_input.first.fill(verify_value)
        invoked = await page.evaluate(
            "() => { if (window.app && typeof window.app.checkYzm === 'function') { window.app.checkYzm(); return true; } return false; }"
        )
        if not invoked:
            btn = page.locator("button:has-text('确认'), button:has-text('验证'), button:has-text('提交')")
            if await btn.count() > 0:
                await btn.first.click(timeout=5000)
        await page.wait_for_timeout(1500)

    raw_data = await page.evaluate(
        "() => window.app && window.app.$data ? JSON.stringify(window.app.$data) : null"
    )
    if raw_data:
        try:
            app_data = json.loads(raw_data)
            ws_list = app_data.get("wsList") or []
            for ws in ws_list:
                await _collect_download(
                    page,
                    lambda item=ws: page.evaluate(
                        "w => { if (typeof downloadFile === 'function') downloadFile(window.app, w); }",
                        item,
                    ),
                    captured_docs,
                    timeout=20000,
                )
            if captured_docs:
                return None
        except Exception:  # noqa: BLE001
            pass

    dl_btns = page.locator("button:has-text('下载'), a:has-text('下载')")
    for idx in range(await dl_btns.count()):
        await _collect_download(
            page, lambda btn=dl_btns.nth(idx): btn.click(timeout=5000), captured_docs
        )
    return None


async def _pw_fetch_hbfy(
    page: Any,
    verify_value: str,
    captured_docs: list[tuple[str, bytes]],
) -> Optional[str]:
    """Playwright fallback adapter for `dzsd.hbfy.gov.cn`."""
    if await page.locator("input[name='captcha'], img.code_img").count() > 0:
        return "湖北电子送达页面需要图形验证码核验，请人工点击链接下载后上传归档"

    if verify_value:
        verify_input = page.locator(
            "input[placeholder*='后4位'], input[placeholder*='手机'], input[placeholder*='提取码']"
        )
        if await verify_input.count() > 0:
            await verify_input.first.fill(verify_value)
            confirm_btn = page.locator("button:has-text('确认'), button:has-text('验证'), button:has-text('查询')")
            if await confirm_btn.count() > 0:
                await confirm_btn.first.click(timeout=5000)
                await page.wait_for_timeout(1000)

    all_btn = page.locator("button:has-text('下载全部')")
    if await all_btn.count() > 0:
        if await _collect_download(
            page, lambda: all_btn.first.click(timeout=5000), captured_docs, timeout=15000
        ):
            return None

    icons = page.locator("svg.downloadIcon, .downloadIcon, button:has-text('下载'), a:has-text('下载')")
    for idx in range(await icons.count()):
        await _collect_download(
            page, lambda btn=icons.nth(idx): btn.click(timeout=5000), captured_docs
        )
    return None


async def _pw_fetch_generic(
    page: Any,
    verify_value: str,
    captured_docs: list[tuple[str, bytes]],
) -> Optional[str]:
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

    dl_btn = page.get_by_role("button", name=re.compile("下载|保存|PDF"))
    count = await dl_btn.count()
    for idx in range(count):
        await _collect_download(page, lambda b=dl_btn.nth(idx): b.click(timeout=5000), captured_docs)
    return None


def _check_tier2_fetch_gate(
    *,
    domain: str,
    auto_fetch_mode: Optional[str],
    confirm_sign: bool,
    phone_last4: Optional[str],
    id_card_last4: Optional[str],
    extraction_code: Optional[str],
    sms_text: str = "",
    doc_title_hint: str = "",
) -> tuple[Optional[str], bool]:
    settings = get_settings()
    eff_mode = (auto_fetch_mode or settings.court_auto_fetch_mode or "confirm_before_sign").strip()
    if not confirm_sign:
        if eff_mode == "confirm_before_sign":
            return (
                f"平台（{domain}）需要核验并签收文书；因当前策略为「签收前人工确认」，请在收件箱核对信息后填写手机号后4位并点击授权签收重试。",
                not bool(phone_last4),
            )
        if eff_mode == "auto_non_judgment":
            combined_hint = f"{sms_text}\n{doc_title_hint}"
            if any(kw in combined_hint for kw in _JUDGMENT_KEYWORDS):
                return (
                    f"平台（{domain}）涉及判决/裁决类重要文书签收；按「仅自动抓非裁决通知」策略，请核对后点击授权签收重试。",
                    not bool(phone_last4),
                )

    # gdems / hbfy often require only a confirmation click without phone_last4
    no_verify_domains = ("sd.gdems.com", "dzsd.hbfy.gov.cn")
    if domain not in no_verify_domains and not (phone_last4 or extraction_code or id_card_last4):
        return (
            f"平台（{domain}）通常需要手机号后4位或短信提取码验证，当前未配置手机号后4位，请提供后重试。",
            True,
        )
    return None, False


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
    auto_fetch_mode: Optional[str] = None,
    sms_text: str = "",
    doc_title_hint: str = "",
) -> tuple[Optional[CourtFetchResult], str, bool]:
    """Tier 2 Playwright interactive download for provincial portals and phone_last4 flows."""
    domain = link_info.get("domain") or ""
    url = link_info.get("url") or ""

    try:
        url = validate_external_fetch_url(url, require_portal_allowlist=True)
    except UnsafeUrlError as exc:
        return None, str(exc), False

    gate_reason, needs_phone = _check_tier2_fetch_gate(
        domain=domain,
        auto_fetch_mode=auto_fetch_mode,
        confirm_sign=confirm_sign,
        phone_last4=phone_last4,
        id_card_last4=id_card_last4,
        extraction_code=extraction_code,
        sms_text=sms_text,
        doc_title_hint=doc_title_hint,
    )
    if gate_reason:
        return None, gate_reason, needs_phone

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None, "未安装 Playwright 运行时，请点击短信链接人工下载后上传归档。", False

    captured_docs: list[tuple[str, bytes]] = []
    adapter_reason: Optional[str] = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(1500)

            body_text = await page.inner_text("body")
            hint, needs_phone_hint = _detect_manual_hint(body_text)
            if any(k in body_text for k in ("人脸识别", "微信小程序", "滑块验证", "图形验证码")):
                await browser.close()
                return None, hint or "页面触发人机/人脸验证，请人工打开链接下载", needs_phone_hint

            verify_value = extraction_code or phone_last4 or id_card_last4 or ""
            if "sd.gdems.com" in domain:
                adapter_reason = await _pw_fetch_gdems(page, verify_value, captured_docs)
            elif "10102368.com" in domain:
                adapter_reason = await _pw_fetch_jysd(page, verify_value, captured_docs)
            elif "cdfy12368.gov.cn" in domain or "171.106.48.55" in domain:
                adapter_reason = await _pw_fetch_sfdw(page, verify_value, captured_docs)
            elif "dzsd.hbfy.gov.cn" in domain:
                adapter_reason = await _pw_fetch_hbfy(page, verify_value, captured_docs)
            else:
                adapter_reason = await _pw_fetch_generic(page, verify_value, captured_docs)

            await browser.close()
    except Exception as exc:  # noqa: BLE001
        logger.info("Tier 2 Playwright fallback triggered for %s: %s", domain, exc)
        return (
            None,
            f"自动访问送达页面（{domain}）未完成，需人工点击短信链接下载或提供尾号重试。",
            not bool(phone_last4),
        )

    staged_paths, parsed_docs = await _stage_captured_docs(
        captured_docs,
        batch_key=batch_key,
        case_number=case_number,
        received_date=received_date,
    )
    if not staged_paths:
        return (
            None,
            adapter_reason or f"未在送达页面（{domain}）捕获到文书下载，请人工下载。",
            False,
        )

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


async def _fetch_url_response(url: str) -> tuple[bytes, str, str, str]:
    resp = await safe_async_get(url, timeout=20.0, require_portal_allowlist=True)
    resp.raise_for_status()
    return (
        resp.content,
        str(resp.url),
        (resp.headers.get("content-type") or "").lower(),
        resp.headers.get("content-disposition") or "",
    )


def _extract_embedded_doc_url(html_text: str, base_url: str) -> Optional[str]:
    for pat in (
        r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+url=([^"'\s>]+)""",
        r"""<(?:iframe|embed|source)[^>]+src=["']([^"']+\.(?:pdf|ofd)(?:\?[^"']*)?)["']""",
        r"""<a[^>]+href=["']([^"']+\.(?:pdf|ofd|docx|zip)(?:\?[^"']*)?)["']""",
    ):
        m = re.search(pat, html_text, re.IGNORECASE)
        if m:
            candidate = urljoin(base_url, m.group(1).strip())
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


def _classify_direct_doc_payload(
    content: bytes,
    *,
    final_url: str,
    content_type: str,
    content_disposition: str,
    fallback_title: str,
) -> list[tuple[str, bytes]]:
    if len(content) < 64:
        return []
    if content.startswith(b"%PDF-") or "application/pdf" in content_type or final_url.lower().endswith(".pdf"):
        return [(f"{fallback_title}.pdf", content)]
    if content.startswith(b"PK\x03\x04"):
        unpacked = _unpack_zip_docs(content)
        if unpacked:
            return unpacked
        is_ofd = "ofd" in content_type or final_url.lower().endswith(".ofd")
        if not is_ofd:
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    is_ofd = any(name.upper() == "OFD.XML" for name in zf.namelist())
            except Exception:  # noqa: BLE001
                is_ofd = False
        if is_ofd:
            return [(f"{fallback_title}.ofd", content)]
        return []
    m_fn = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\r\n]+)', content_disposition, re.IGNORECASE)
    if m_fn:
        leaf = Path(m_fn.group(1).strip()).name
        if Path(leaf).suffix.lower() in _ALLOWED_DOC_EXTS:
            return [(leaf, content)]
    return []


async def _tier1_fetch_direct_or_portal(
    link_info: dict[str, Any],
    *,
    batch_key: str,
    case_number: Optional[str],
    received_date: str,
) -> tuple[Optional[CourtFetchResult], Optional[str], bool]:
    """Attempt direct HTTP / HTML-redirect PDF fetch for 12309 and arbitration links."""
    url = str(link_info.get("url") or "").strip()
    if not url:
        return None, None, False
    fallback_title = str(link_info.get("description") or "送达文书").strip()

    try:
        raw, final_url, ct, cd = await _fetch_url_response(url)
    except UnsafeUrlError as exc:
        logger.warning("Blocked unsafe court URL %s: %s", url, exc)
        return None, str(exc), False
    except Exception as exc:  # noqa: BLE001
        logger.debug("Direct/portal fetch failed for %s: %s", url, exc)
        return None, None, False

    captured = _classify_direct_doc_payload(
        raw,
        final_url=final_url,
        content_type=ct,
        content_disposition=cd,
        fallback_title=fallback_title,
    )
    if not captured and ("html" in ct or b"<html" in raw[:512].lower()):
        html_text = raw.decode("utf-8", errors="ignore")
        hint, needs_phone = _detect_manual_hint(html_text)
        if hint and any(k in html_text for k in ("验证码", "人脸", "小程序", "滑块")):
            return None, hint, needs_phone
        nested_url = _extract_embedded_doc_url(html_text, final_url)
        if nested_url:
            try:
                raw2, final_url2, ct2, cd2 = await _fetch_url_response(nested_url)
                captured = _classify_direct_doc_payload(
                    raw2,
                    final_url=final_url2,
                    content_type=ct2,
                    content_disposition=cd2,
                    fallback_title=fallback_title,
                )
            except Exception:  # noqa: BLE001
                captured = []

    if not captured:
        return None, None, False

    staged_paths, parsed_docs = await _stage_captured_docs(
        captured,
        batch_key=batch_key,
        case_number=case_number,
        received_date=received_date,
    )
    if not staged_paths:
        return None, None, False

    return (
        CourtFetchResult(
            status="fetched",
            tier_used="tier1_api",
            downloaded_files=staged_paths,
            parsed_docs=parsed_docs,
        ),
        None,
        False,
    )


async def fetch_court_documents(
    parsed_sms: dict[str, Any],
    *,
    batch_key: str,
    phone_last4: Optional[str] = None,
    id_card_last4: Optional[str] = None,
    confirm_sign: bool = False,
    auto_fetch_mode: Optional[str] = None,
    sms_text: str = "",
) -> CourtFetchResult:
    """Execute 3-tier court document fetching sequentially for extracted court links."""
    links: list[dict[str, Any]] = parsed_sms.get("court_links") or []
    if not links:
        return CourtFetchResult(status="no_links", tier_used="none")

    case_number = parsed_sms.get("case_number")
    received_date = str(parsed_sms.get("event_date") or datetime.now(SHANGHAI_TZ).date().isoformat())
    extraction_code = parsed_sms.get("extraction_code")
    doc_title_hint = str(parsed_sms.get("doc_title_hint") or "")
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
        t1_err: Optional[str] = None

        if domain == "zxfw.court.gov.cn" and tier == 1:
            res, t1_err, needs_phone = await _tier1_fetch_zxfw(
                link,
                batch_key=batch_key,
                case_number=case_number,
                received_date=received_date,
                phone_last4=phone_last4,
            )
        elif "dzsd.hbfy.gov.cn" in domain:
            res, t1_err, needs_phone = await _tier1_fetch_hbfy(
                link,
                batch_key=batch_key,
                case_number=case_number,
                received_date=received_date,
            )
        elif link.get("download_strategy") == "direct_or_playwright" or domain in (
            "12309.gov.cn",
            "arbitration_portal",
        ):
            res, t1_err, needs_phone = await _tier1_fetch_direct_or_portal(
                link,
                batch_key=batch_key,
                case_number=case_number,
                received_date=received_date,
            )
        else:
            res, needs_phone = None, False

        if res is not None:
            all_files.extend(res.downloaded_files)
            all_docs.extend(res.parsed_docs)
            tier_used = res.tier_used
            court_name = court_name or res.court_name
            sent_at = sent_at or res.sent_at
            continue

        needs_phone_any = needs_phone_any or needs_phone
        if "dzsd.hbfy.gov.cn" in domain and t1_err and "验证码" in t1_err:
            reasons.append(t1_err)
            continue
        if t1_err and ("不在司法/发票官方白名单内" in t1_err or "内网" in t1_err or "受限主机" in t1_err):
            reasons.append(t1_err)
            continue
        t2_res, t2_err, t2_needs_phone = await _tier2_playwright_fetch(
            link,
            batch_key=batch_key,
            case_number=case_number,
            received_date=received_date,
            phone_last4=phone_last4,
            id_card_last4=id_card_last4,
            extraction_code=extraction_code,
            confirm_sign=confirm_sign,
            auto_fetch_mode=auto_fetch_mode,
            sms_text=sms_text,
            doc_title_hint=doc_title_hint,
        )
        if t2_res is not None:
            all_files.extend(t2_res.downloaded_files)
            all_docs.extend(t2_res.parsed_docs)
            tier_used = t2_res.tier_used
            continue
        reasons.append(t1_err or t2_err)
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
