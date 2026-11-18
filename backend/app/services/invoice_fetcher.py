"""Electronic invoice downloader, multi-format metadata extractor (PDF/OFD/XML), and archiver."""

from __future__ import annotations

import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo

import httpx
import pdfplumber

from app.services import file_bridge

logger = logging.getLogger(__name__)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_MAX_ZIP_ENTRIES = 16
_MAX_ZIP_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 32 * 1024 * 1024

_CLICK_SELECTORS = (
    "a:has-text('下载PDF')",
    "a:has-text('下载 PDF')",
    "button:has-text('下载PDF')",
    "button:has-text('下载 PDF')",
    "a:has-text('下载OFD')",
    "a:has-text('下载 OFD')",
    "button:has-text('下载OFD')",
    "button:has-text('下载 OFD')",
    "a:has-text('下载XML')",
    "a:has-text('下载 XML')",
    "button:has-text('下载XML')",
    "button:has-text('下载 XML')",
    "a:has-text('下载发票')",
    "button:has-text('下载发票')",
    "a.primary",
)
_CAPTCHA_HINTS = ("图形验证码", "请输入验证码", "短信验证码", "滑块验证", "请登录")


@dataclass
class InvoiceFetchResult:
    status: str  # fetched | manual_required | no_links
    rel_file_path: Optional[str] = None
    file_name: Optional[str] = None
    invoice_meta: dict[str, Any] = None  # type: ignore[assignment]
    manual_reason: Optional[str] = None
    file_format: Optional[str] = None

    def __post_init__(self) -> None:
        if self.invoice_meta is None:
            self.invoice_meta = {}


def _empty_invoice_meta(file_format: str = "pdf", raw_extracted: bool = False) -> dict[str, Any]:
    return {
        "invoice_number": None,
        "issue_date": None,
        "buyer_name": None,
        "buyer_tax_id": None,
        "seller_name": None,
        "seller_tax_id": None,
        "item_name": None,
        "total_amount": None,
        "remark": None,
        "file_format": file_format,
        "raw_extracted": raw_extracted,
    }


def _clean_line_value(raw: str) -> str:
    return re.sub(r"\s+", " ", (raw or "").strip())


def _normalize_date_str(raw: str) -> Optional[str]:
    val = (raw or "").strip()
    if not val:
        return None
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?", val)
    if not m:
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", val)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m_compact = re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", val)
    if m_compact:
        return f"{m_compact.group(1)}-{m_compact.group(2)}-{m_compact.group(3)}"
    return None


def _extract_invoice_fields_from_text(text: str, file_format: str = "pdf") -> dict[str, Any]:
    meta = _empty_invoice_meta(file_format=file_format, raw_extracted=bool(text))
    if not text:
        return meta

    m_no = re.search(r"发票号码[：:\s]*([0-9]{8,22})", text)
    if m_no:
        meta["invoice_number"] = m_no.group(1).strip()

    m_date = re.search(r"开票日期[：:\s]*([^\n]+)", text)
    if m_date:
        meta["issue_date"] = _normalize_date_str(m_date.group(1))

    m_total = re.search(r"[(（]小写[)）]\s*[¥￥]?\s*(-?\d+(?:\.\d{1,2})?)", text)
    if not m_total:
        m_total = re.search(r"价税合计[^0-9¥￥\-]{0,20}[¥￥]?\s*(-?\d+(?:\.\d{1,2})?)", text)
    if m_total:
        meta["total_amount"] = m_total.group(1).strip()

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

    m_item = re.search(r"\*[^*\n]{1,20}\*([^\s\n]{2,40})", text)
    if m_item:
        meta["item_name"] = _clean_line_value(m_item.group(1))

    m_remark = re.search(r"备\s*注[：:\s]*([^\n]+)", text)
    if m_remark:
        remark_val = _clean_line_value(m_remark.group(1))
        if remark_val and remark_val not in ("无", "-"):
            meta["remark"] = remark_val

    return meta


def parse_invoice_pdf_bytes(data: bytes, fallback_name: str = "invoice.pdf") -> dict[str, Any]:
    """Extract standard Chinese VAT/digital invoice metadata from PDF bytes via pdfplumber."""
    text = ""
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages[:2]).strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("pdfplumber invoice parse error (%s): %s", fallback_name, exc)
    return _extract_invoice_fields_from_text(text, file_format="pdf")


def _decode_xml_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def parse_invoice_xml_bytes(
    data: bytes,
    fallback_name: str = "invoice.xml",
    *,
    file_format: str = "xml",
) -> dict[str, Any]:
    """Extract metadata from Chinese digital/VAT electronic invoice XML payload."""
    meta = _empty_invoice_meta(file_format=file_format, raw_extracted=False)
    xml_text = _decode_xml_bytes(data).strip()
    if not xml_text:
        return meta

    # Disallow DTD/entity expansion before parsing untrusted XML
    if re.search(r"<!(?:DOCTYPE|ENTITY)\b", xml_text, re.IGNORECASE):
        xml_text = re.sub(r"<!(?:DOCTYPE|ENTITY)[^>]*>", "", xml_text, flags=re.IGNORECASE)

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("XML invoice parse error (%s): %s", fallback_name, exc)
        return meta

    tag_map: dict[str, str] = {}
    for elem in root.iter():
        local_tag = elem.tag.split("}")[-1].strip().lower()
        val = (elem.text or "").strip()
        if local_tag and val and local_tag not in tag_map:
            tag_map[local_tag] = _clean_line_value(val)

    def _first(*keys: str) -> Optional[str]:
        for k in keys:
            if k in tag_map and tag_map[k]:
                return tag_map[k]
        return None

    meta["invoice_number"] = _first("fphm", "invoicenumber", "invoice_no", "eiid", "deinvoiceid")
    raw_date = _first("kprq", "issuetime", "issuedate", "requesttime")
    if raw_date:
        meta["issue_date"] = _normalize_date_str(raw_date)
    meta["buyer_name"] = _first("gmfmc", "buyername", "gfmc")
    meta["buyer_tax_id"] = _first("gmfnsrsbh", "buyertaxid", "gmfidnum")
    meta["seller_name"] = _first("xfmc", "sellername")
    meta["seller_tax_id"] = _first("xfnsrsbh", "sellertaxid", "xfidnum")
    raw_total = _first(
        "jshj",
        "totaltax-includedamount",
        "totaltaxincludedamount",
        "totalamount",
        "hjje",
    )
    if raw_total:
        m_amt = re.search(r"-?\d+(?:\.\d{1,2})?", raw_total)
        meta["total_amount"] = m_amt.group(0) if m_amt else raw_total
    meta["item_name"] = _first("xmmc", "spmc", "itemname", "goodsname")
    remark_val = _first("bz", "remark")
    if remark_val and remark_val not in ("无", "-"):
        meta["remark"] = remark_val

    if not meta["invoice_number"]:
        m_20 = re.search(r"(?<!\d)(\d{20})(?!\d)", xml_text)
        if m_20:
            meta["invoice_number"] = m_20.group(1)

    meta["raw_extracted"] = any(
        meta[k] for k in ("invoice_number", "total_amount", "buyer_name", "seller_name")
    )
    return meta


def parse_invoice_ofd_bytes(data: bytes, fallback_name: str = "invoice.ofd") -> dict[str, Any]:
    """Extract metadata from an OFD container (embedded original XML or TextCode glyph streams)."""
    meta = _empty_invoice_meta(file_format="ofd", raw_extracted=False)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir()][:64]
            xml_candidates = [
                info
                for info in infos
                if info.filename.lower().endswith(".xml") and info.file_size <= 8 * 1024 * 1024
            ]
            # Prefer embedded original invoice XML attachments first
            xml_candidates.sort(
                key=lambda i: (
                    0
                    if any(
                        kw in i.filename.lower()
                        for kw in ("original_invoice", "invoice", "attach", "einvoice")
                    )
                    else 1,
                    i.filename,
                )
            )
            for info in xml_candidates:
                raw_xml = zf.read(info.filename)
                parsed = parse_invoice_xml_bytes(raw_xml, info.filename, file_format="ofd")
                if parsed.get("raw_extracted"):
                    return parsed

            # Fallback: extract rendered OFD TextCode strings across Content*.xml files
            text_chunks: list[str] = []
            for info in xml_candidates:
                if "content" not in info.filename.lower():
                    continue
                xml_str = _decode_xml_bytes(zf.read(info.filename))
                if re.search(r"<!(?:DOCTYPE|ENTITY)\b", xml_str, re.IGNORECASE):
                    xml_str = re.sub(r"<!(?:DOCTYPE|ENTITY)[^>]*>", "", xml_str, flags=re.IGNORECASE)
                try:
                    root = ET.fromstring(xml_str)
                except ET.ParseError:
                    continue
                for elem in root.iter():
                    if elem.tag.split("}")[-1].lower() == "textcode" and elem.text:
                        text_chunks.append(elem.text.strip())
            if text_chunks:
                return _extract_invoice_fields_from_text("\n".join(text_chunks), file_format="ofd")
    except (zipfile.BadZipFile, ValueError, RuntimeError) as exc:
        logger.debug("OFD invoice parse error (%s): %s", fallback_name, exc)
    return meta


def parse_invoice_bytes(
    data: bytes,
    ext: str = ".pdf",
    fallback_name: str = "invoice.pdf",
) -> dict[str, Any]:
    """Dispatch invoice metadata parsing by normalized file extension."""
    norm_ext = f".{ext.lstrip('.').lower()}" if ext else ".pdf"
    if norm_ext == ".xml":
        return parse_invoice_xml_bytes(data, fallback_name=fallback_name)
    if norm_ext == ".ofd":
        return parse_invoice_ofd_bytes(data, fallback_name=fallback_name)
    return parse_invoice_pdf_bytes(data, fallback_name=fallback_name)


def format_invoice_filename(
    meta: dict[str, Any],
    fallback_name: str = "电子发票.pdf",
    *,
    ext: Optional[str] = None,
) -> str:
    """Format invoice filename: `{YYYYMMDD}_{item_name}_{total_amount}元_{invoice_no}{ext}`."""
    norm_ext = f".{(ext or meta.get('file_format') or 'pdf').lstrip('.').lower()}"
    if norm_ext not in (".pdf", ".ofd", ".xml"):
        norm_ext = ".pdf"
    if fallback_name == "电子发票.pdf" and norm_ext != ".pdf":
        fallback_name = f"电子发票{norm_ext}"

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
        f"{issue_date}_{item_name}_{amount}元_{invoice_no}{norm_ext}",
        fallback=fallback_name,
    )


def _merge_invoice_meta(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for k, v in secondary.items():
        if k in ("file_format", "raw_extracted"):
            continue
        if not merged.get(k) and v:
            merged[k] = v
    merged["raw_extracted"] = bool(merged.get("raw_extracted") or secondary.get("raw_extracted"))
    return merged


def _detect_artifact_ext(
    data: bytes,
    *,
    url: str = "",
    content_type: str = "",
    content_disposition: str = "",
) -> Optional[str]:
    if data.startswith(b"%PDF-"):
        return ".pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = [n.lower() for n in zf.namelist()[:64]]
                if any(n == "ofd.xml" or n.endswith("/ofd.xml") for n in names):
                    return ".ofd"
        except zipfile.BadZipFile:
            pass
        return ".zip"

    head_text = _decode_xml_bytes(data[:512]).lstrip().lower()
    if head_text.startswith("<?xml") or any(
        tag in head_text for tag in ("<einvoice", "<invoice", "<fp>", "<taxsupervisioninfo")
    ):
        return ".xml"

    ct = (content_type or "").lower()
    cd = unquote(content_disposition or "").lower()
    low_url = (url or "").lower()
    if ".ofd" in cd or "application/ofd" in ct or low_url.endswith(".ofd"):
        return ".ofd"
    if (".xml" in cd or "xml" in ct or low_url.endswith(".xml")) and "<html" not in head_text:
        return ".xml"
    if ".pdf" in cd or "application/pdf" in ct or low_url.endswith(".pdf"):
        return ".pdf"
    return None


def _unpack_zip_invoice_artifact(zip_bytes: bytes) -> Optional[tuple[str, bytes, dict[str, Any]]]:
    """Safely extract invoice artifacts from a container ZIP and merge XML metadata."""
    extracted: dict[str, tuple[bytes, dict[str, Any]]] = {}
    total_size = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for info in zf.infolist()[:_MAX_ZIP_ENTRIES]:
                if info.is_dir() or ".." in info.filename.replace("\\", "/").split("/"):
                    continue
                if info.file_size <= 0 or info.file_size > _MAX_ZIP_MEMBER_BYTES:
                    continue
                total_size += info.file_size
                if total_size > _MAX_ZIP_TOTAL_BYTES:
                    break
                ext = ""
                lower_name = info.filename.lower()
                for candidate_ext in (".pdf", ".ofd", ".xml"):
                    if lower_name.endswith(candidate_ext):
                        ext = candidate_ext
                        break
                if not ext:
                    continue
                member_bytes = zf.read(info.filename)
                member_meta = parse_invoice_bytes(member_bytes, ext=ext, fallback_name=info.filename)
                if ext not in extracted:
                    extracted[ext] = (member_bytes, member_meta)
    except zipfile.BadZipFile:
        return None

    if not extracted:
        return None

    merged_meta: dict[str, Any] = {}
    if ".xml" in extracted:
        merged_meta = dict(extracted[".xml"][1])
    for pref_ext in (".pdf", ".ofd", ".xml"):
        if pref_ext in extracted:
            raw_bytes, own_meta = extracted[pref_ext]
            combined_meta = _merge_invoice_meta(own_meta, merged_meta)
            combined_meta["file_format"] = pref_ext.lstrip(".")
            return pref_ext, raw_bytes, combined_meta
    return None


def _classify_http_response_artifact(
    resp: httpx.Response,
    url: str,
) -> Optional[tuple[str, bytes, dict[str, Any]]]:
    if resp.status_code >= 400 or not resp.content:
        return None
    ext = _detect_artifact_ext(
        resp.content,
        url=str(resp.url or url),
        content_type=resp.headers.get("content-type", ""),
        content_disposition=resp.headers.get("content-disposition", ""),
    )
    if ext == ".zip":
        return _unpack_zip_invoice_artifact(resp.content)
    if ext in (".pdf", ".ofd", ".xml"):
        meta = parse_invoice_bytes(resp.content, ext=ext)
        if ext == ".xml" and not meta.get("raw_extracted"):
            return None
        return ext, resp.content, meta
    return None


async def _probe_baiwang_api(
    client: httpx.AsyncClient,
    url: str,
) -> Optional[tuple[str, bytes, dict[str, Any]]]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "baiwang.com" not in host and "efapiao.com" not in host:
        return None
    query = parse_qs(parsed.query)
    param = (query.get("param") or [""])[0].strip()
    if not param:
        return None

    for fmt in ("PDF", "OFD", "XML"):
        try:
            resp = await client.get(
                "https://pis.baiwang.com/bwmg/mix/bw/downloadFormat",
                params={"param": param, "formatType": fmt},
            )
            artifact = _classify_http_response_artifact(resp, str(resp.url))
            if artifact:
                return artifact
        except Exception:  # noqa: BLE001
            continue

    try:
        preview = await client.post(
            "https://pis.baiwang.com/bwmg/mix/bw/previewInvoiceQd",
            content=param.encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        if preview.status_code < 400:
            data = preview.json()
            inner = data.get("data") if isinstance(data, dict) else None
            if isinstance(inner, dict):
                for key in ("pdfUrl", "ofdUrl", "xmlUrl", "downloadUrl", "url"):
                    sub_url = inner.get(key)
                    if isinstance(sub_url, str) and sub_url.startswith("http"):
                        sub_resp = await client.get(sub_url)
                        artifact = _classify_http_response_artifact(sub_resp, sub_url)
                        if artifact:
                            return artifact
    except Exception:  # noqa: BLE001
        pass
    return None


async def _probe_nuonuo_api(
    client: httpx.AsyncClient,
    url: str,
) -> Optional[tuple[str, bytes, dict[str, Any]]]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if "nnfp.jss.com.cn" not in host:
        return None
    try:
        landing = await client.get(url)
        final_parsed = urlparse(str(landing.url))
        qs = parse_qs(final_parsed.query)
        payload: dict[str, str] = {}
        for k in ("paramList", "code", "aliView", "shortLinkSource", "isOuterPageReq"):
            vals = qs.get(k)
            if vals:
                payload[k] = vals[0]
        if not payload:
            return None
        endpoint = (
            "https://nnfp.jss.com.cn/sapi/invoice/scan/IvcDetail.do"
            if payload.get("isOuterPageReq") == "true"
            else "https://nnfp.jss.com.cn/sapi/scan2/getIvcDetailShow.do"
        )
        detail_resp = await client.post(endpoint, data=payload)
        if detail_resp.status_code >= 400:
            return None
        detail_json = detail_resp.json()
        vo = (detail_json.get("data") or {}).get("invoiceSimpleVo") or {}
        for key in ("url", "pdfUrl", "ofdDownloadUrl", "xmlUrl"):
            sub_url = vo.get(key)
            if isinstance(sub_url, str) and sub_url.startswith("http"):
                sub_resp = await client.get(sub_url)
                artifact = _classify_http_response_artifact(sub_resp, sub_url)
                if artifact:
                    return artifact
    except Exception:  # noqa: BLE001
        return None
    return None


async def _download_invoice_pdf_direct(url: str) -> Optional[bytes]:
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA},
    ) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            return None
        ct = (resp.headers.get("content-type") or "").lower()
        if "pdf" in ct or resp.content.startswith(b"%PDF-"):
            return resp.content
    return None


async def _download_invoice_artifact_direct(url: str) -> Optional[tuple[str, bytes, dict[str, Any]]]:
    """Download PDF/OFD/XML/ZIP directly or via Baiwang/Nuonuo preview APIs."""
    async with httpx.AsyncClient(
        timeout=25.0,
        follow_redirects=True,
        headers={"User-Agent": _BROWSER_UA},
    ) as client:
        for probe in (_probe_baiwang_api, _probe_nuonuo_api):
            artifact = await probe(client, url)
            if artifact is not None:
                return artifact

        try:
            resp = await client.get(url)
            artifact = _classify_http_response_artifact(resp, url)
            if artifact is not None:
                return artifact
        except Exception as exc:  # noqa: BLE001
            logger.debug("Direct artifact fetch failed for %s: %s", url, exc)
    return None


async def _download_invoice_artifact_playwright(
    url: str,
) -> tuple[Optional[str], Optional[bytes], Optional[str]]:
    """Use Playwright to intercept network responses, iframes, and format download buttons."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None, None, None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(accept_downloads=True, user_agent=_BROWSER_UA)
            page = await context.new_page()
            captured: Optional[tuple[str, bytes]] = None
            manual_hint: Optional[str] = None

            async def _on_response(response: Any) -> None:
                nonlocal captured
                if captured is not None:
                    return
                try:
                    ct = (response.headers.get("content-type") or "").lower()
                    cd = (response.headers.get("content-disposition") or "").lower()
                    resp_url = response.url or ""
                    if not any(
                        tok in ct or tok in cd or tok in resp_url.lower()
                        for tok in ("pdf", "ofd", "xml", "zip", "download")
                    ):
                        return
                    body = await response.body()
                    ext = _detect_artifact_ext(
                        body, url=resp_url, content_type=ct, content_disposition=cd
                    )
                    if ext == ".zip":
                        unpacked = _unpack_zip_invoice_artifact(body)
                        if unpacked:
                            captured = (unpacked[0], unpacked[1])
                    elif ext in (".pdf", ".ofd", ".xml"):
                        if ext == ".xml" and not parse_invoice_xml_bytes(body).get("raw_extracted"):
                            return
                        captured = (ext, body)
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", _on_response)
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            if resp and resp.status in (401, 403):
                await browser.close()
                return None, None, f"发票平台返回访问受限（HTTP {resp.status}），请点击链接人工下载后上传归档"

            await page.wait_for_timeout(1800)

            try:
                body_text = await page.inner_text("body")
            except Exception:  # noqa: BLE001
                body_text = ""
            if any(hint in body_text for hint in _CAPTCHA_HINTS):
                manual_hint = "发票页面要求验证码或登录验证，请点击链接人工下载后在收件箱上传归档"
                await browser.close()
                return None, None, manual_hint

            # Inspect iframes & click PDF/OFD/XML buttons across main page and frames
            if captured is None:
                frames = [page.main_frame, *[f for f in page.frames if f != page.main_frame]]
                for frame in frames:
                    if captured is not None:
                        break
                    for sel in _CLICK_SELECTORS:
                        loc = frame.locator(sel)
                        if await loc.count() <= 0:
                            continue
                        try:
                            async with page.expect_download(timeout=8000) as dl_info:
                                await loc.first.click(timeout=3000)
                            dl = await dl_info.value
                            tmp_path = await dl.path()
                            if tmp_path:
                                with open(tmp_path, "rb") as fh:
                                    raw = fh.read()
                                sug = dl.suggested_filename or ""
                                ext = _detect_artifact_ext(raw, url=sug, content_disposition=sug)
                                if ext == ".zip":
                                    unpacked = _unpack_zip_invoice_artifact(raw)
                                    if unpacked:
                                        captured = (unpacked[0], unpacked[1])
                                elif ext in (".pdf", ".ofd", ".xml"):
                                    captured = (ext, raw)
                                if captured is not None:
                                    break
                        except Exception:  # noqa: BLE001
                            continue

            await browser.close()
            if captured is not None:
                return captured[0], captured[1], None
            return None, None, manual_hint
    except Exception as exc:  # noqa: BLE001
        logger.info("Playwright invoice fetch failed for %s: %s", url, exc)
        return None, None, None


async def _download_invoice_pdf_playwright(url: str) -> Optional[bytes]:
    ext, data, _ = await _download_invoice_artifact_playwright(url)
    if data and ext == ".pdf":
        return data
    return None


_DEFAULT_PW_PDF_FN = _download_invoice_pdf_playwright


async def fetch_and_stage_invoice(parsed_sms: dict[str, Any]) -> InvoiceFetchResult:
    """Download invoice PDF/OFD/XML from extracted links, parse metadata, and stage to `_待认领发票`."""
    links: list[dict[str, Any]] = parsed_sms.get("invoice_links") or []
    if not links:
        return InvoiceFetchResult(
            status="no_links",
            manual_reason="短信中未识别出电子发票下载链接，请手动上传 PDF/OFD/XML 发票",
        )

    picked_ext: Optional[str] = None
    picked_bytes: Optional[bytes] = None
    extra_meta: dict[str, Any] = {}
    manual_hint: Optional[str] = None
    primary_url = ""

    for link in links:
        url = str(link.get("url") or "").strip() if isinstance(link, dict) else str(link).strip()
        if not url:
            continue
        if not primary_url:
            primary_url = url

        pdf_direct = await _download_invoice_pdf_direct(url)
        if pdf_direct is not None:
            picked_ext, picked_bytes = ".pdf", pdf_direct
            break

        artifact_direct = await _download_invoice_artifact_direct(url)
        if artifact_direct is not None:
            picked_ext, picked_bytes, extra_meta = artifact_direct
            break

        if _download_invoice_pdf_playwright is not _DEFAULT_PW_PDF_FN:
            pw_pdf = await _download_invoice_pdf_playwright(url)
            if pw_pdf is not None:
                picked_ext, picked_bytes = ".pdf", pw_pdf
                break

        pw_ext, pw_bytes, pw_hint = await _download_invoice_artifact_playwright(url)
        if pw_ext and pw_bytes:
            picked_ext, picked_bytes = pw_ext, pw_bytes
            break
        if pw_hint and not manual_hint:
            manual_hint = pw_hint

    if not picked_bytes or not picked_ext:
        base_reason = (
            manual_hint
            or "未能自动下载电子发票文件（可能需要图形/短信验证码或页面登录），请点击直达链接下载后在收件箱上传归档"
        )
        if primary_url:
            base_reason = f"{base_reason}。直达链接：{primary_url}"
        return InvoiceFetchResult(
            status="manual_required",
            manual_reason=base_reason,
        )

    meta = parse_invoice_bytes(picked_bytes, ext=picked_ext)
    if extra_meta:
        meta = _merge_invoice_meta(meta, extra_meta)
    filename = format_invoice_filename(meta, ext=picked_ext)
    _, rel_path = file_bridge.save_unclaimed_invoice(filename, picked_bytes)
    return InvoiceFetchResult(
        status="fetched",
        rel_file_path=rel_path,
        file_name=filename,
        invoice_meta=meta,
        file_format=picked_ext.lstrip("."),
    )
