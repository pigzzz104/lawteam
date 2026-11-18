"""Electronic invoice downloader, pdfplumber metadata extractor, and archiver."""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import pdfplumber

from app.services import file_bridge

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class InvoiceFetchResult:
    status: str  # fetched | manual_required | no_links
    rel_file_path: Optional[str] = None
    file_name: Optional[str] = None
    invoice_meta: dict[str, Any] = None  # type: ignore[assignment]
    manual_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.invoice_meta is None:
            self.invoice_meta = {}


def _clean_line_value(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def parse_invoice_pdf_bytes(data: bytes, fallback_name: str = "invoice.pdf") -> dict[str, Any]:
    """Extract standard Chinese VAT/digital invoice metadata from PDF bytes via pdfplumber."""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:2]).strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("pdfplumber invoice parse error (%s): %s", fallback_name, exc)

    meta: dict[str, Any] = {
        "invoice_number": None,
        "issue_date": None,
        "buyer_name": None,
        "buyer_tax_id": None,
        "seller_name": None,
        "seller_tax_id": None,
        "item_name": None,
        "total_amount": None,
        "remark": None,
        "raw_extracted": bool(text),
    }
    if not text:
        return meta

    m_no = re.search(r"发票号码[：:\s]*([0-9]{8,22})", text)
    if m_no:
        meta["invoice_number"] = m_no.group(1).strip()

    m_date = re.search(r"开票日期[：:\s]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if not m_date:
        m_date = re.search(r"开票日期[：:\s]*(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m_date:
        meta["issue_date"] = f"{int(m_date.group(1)):04d}-{int(m_date.group(2)):02d}-{int(m_date.group(3)):02d}"

    # Total amount (价税合计小写)
    m_total = re.search(r"[(（]小写[)）]\s*[¥￥]?\s*(-?\d+(?:\.\d{1,2})?)", text)
    if not m_total:
        m_total = re.search(r"价税合计[^0-9¥￥\-]{0,20}[¥￥]?\s*(-?\d+(?:\.\d{1,2})?)", text)
    if m_total:
        meta["total_amount"] = m_total.group(1).strip()

    # Buyer & Seller names
    m_buyer = re.search(r"购买方(?:信息)?[^\n]*?名\s*称[：:\s]*([^\n\s]{2,60})", text)
    if not m_buyer:
        names = re.findall(r"名\s*称[：:\s]*([^\n]+)", text)
        if names:
            meta["buyer_name"] = _clean_line_value(names[0].split("销售方")[0].split("统一社会")[0])
        if len(names) >= 2:
            meta["seller_name"] = _clean_line_value(names[1].split("统一社会")[0])
    else:
        meta["buyer_name"] = _clean_line_value(m_buyer.group(1))

    m_seller = re.search(r"销售方(?:信息)?[^\n]*?名\s*称[：:\s]*([^\n\s]{2,60})", text)
    if m_seller:
        meta["seller_name"] = _clean_line_value(m_seller.group(1))

    tax_ids = re.findall(r"(?:纳税人识别号|统一社会信用代码)[：:\s]*([0-9A-Z]{15,20})", text)
    if tax_ids:
        meta["buyer_tax_id"] = tax_ids[0]
    if len(tax_ids) >= 2:
        meta["seller_tax_id"] = tax_ids[1]

    # Item name (*类别*项目)
    m_item = re.search(r"\*[^*\n]{1,20}\*([^\s\n]{2,40})", text)
    if m_item:
        meta["item_name"] = _clean_line_value(m_item.group(1))

    m_remark = re.search(r"备\s*注[：:\s]*([^\n]+)", text)
    if m_remark:
        remark_val = _clean_line_value(m_remark.group(1))
        if remark_val and remark_val not in ("无", "-"):
            meta["remark"] = remark_val

    return meta


def format_invoice_filename(meta: dict[str, Any], fallback_name: str = "电子发票.pdf") -> str:
    """Format invoice filename: `{YYYYMMDD}_{item_name}_{total_amount}元_{invoice_no}.pdf`."""
    issue_date = str(meta.get("issue_date") or "").replace("-", "")
    if not re.fullmatch(r"\d{8}", issue_date):
        issue_date = datetime.now(SHANGHAI_TZ).strftime("%Y%m%d")

    item_name = file_bridge.sanitize_segment(
        str(meta.get("item_name") or "法律服务费"), fallback="电子发票", max_len=24
    )
    amount = str(meta.get("total_amount") or "待核金额")
    invoice_no = file_bridge.sanitize_segment(
        str(meta.get("invoice_number") or "待核票号"), fallback="待核票号", max_len=24
    )
    return file_bridge.sanitize_filename(
        f"{issue_date}_{item_name}_{amount}元_{invoice_no}.pdf",
        fallback=fallback_name,
    )


async def _download_invoice_pdf_direct(url: str) -> Optional[bytes]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return None
        ct = (resp.headers.get("content-type") or "").lower()
        if "pdf" in ct or resp.content.startswith(b"%PDF-"):
            return resp.content
    return None


async def _download_invoice_pdf_playwright(url: str) -> Optional[bytes]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            pdf_bytes: Optional[bytes] = None

            async def _on_response(response: Any) -> None:
                nonlocal pdf_bytes
                if pdf_bytes is not None:
                    return
                ct = (response.headers.get("content-type") or "").lower()
                if "application/pdf" in ct:
                    try:
                        body = await response.body()
                        if body.startswith(b"%PDF-"):
                            pdf_bytes = body
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", _on_response)
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1800)

            if pdf_bytes is None:
                dl_btn = page.get_by_role("button", name=re.compile("下载PDF|下载发票|PDF下载"))
                if await dl_btn.count() > 0:
                    async with page.expect_download(timeout=8000) as dl_info:
                        await dl_btn.first.click()
                        dl = await dl_info.value
                        p_file = await dl.path()
                        if p_file:
                            with open(p_file, "rb") as fh:
                                pdf_bytes = fh.read()
            await browser.close()
            return pdf_bytes
    except Exception as exc:  # noqa: BLE001
        logger.info("Playwright invoice fetch failed for %s: %s", url, exc)
        return None


async def fetch_and_stage_invoice(parsed_sms: dict[str, Any]) -> InvoiceFetchResult:
    """Download invoice PDF from extracted links, parse metadata, and save to `_待认领发票`."""
    links: list[dict[str, Any]] = parsed_sms.get("invoice_links") or []
    if not links:
        return InvoiceFetchResult(
            status="no_links",
            manual_reason="短信中未识别出电子发票下载链接，请手动上传PDF发票",
        )

    pdf_bytes: Optional[bytes] = None
    for link in links:
        url = str(link.get("url") or "").strip()
        if not url:
            continue
        pdf_bytes = await _download_invoice_pdf_direct(url)
        if pdf_bytes is None:
            pdf_bytes = await _download_invoice_pdf_playwright(url)
        if pdf_bytes is not None:
            break

    if not pdf_bytes:
        return InvoiceFetchResult(
            status="manual_required",
            manual_reason="未能自动下载电子发票PDF（可能需要验证码或页面登录），请人工下载后在收件箱上传归档。",
        )

    meta = parse_invoice_pdf_bytes(pdf_bytes)
    filename = format_invoice_filename(meta)
    _, rel_path = file_bridge.save_unclaimed_invoice(filename, pdf_bytes)
    return InvoiceFetchResult(
        status="fetched",
        rel_file_path=rel_path,
        file_name=filename,
        invoice_meta=meta,
    )
