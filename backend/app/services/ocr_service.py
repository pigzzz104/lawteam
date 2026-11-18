"""Scanned PDF rendering and 3-tier OCR fallback service (VLM -> local macOS Vision / RapidOCR / Tesseract).

Iron Rule #1: OCR only transcribes printed text from scanned documents; it NEVER computes
statutory deadlines. All date calculations remain deterministic in `deadline_engine.py`.
"""

from __future__ import annotations

import asyncio
import importlib
import io
import logging
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.services.llm_client import UniversalLLMClient

logger = logging.getLogger(__name__)

MIN_TEXT_CHARS = 30
RENDER_DPI = 150
MAX_OCR_PAGES = 2
MAX_EDGE_PX = 1600

_OCR_SYSTEM_PROMPT = (
    "你是严谨的法律文书与电子发票文字转录助手。"
    "你只做逐字转录，严禁总结、改写、补充原文没有的信息，也严禁推算任何截止日期。"
)
_OCR_USER_PROMPT = (
    "请逐字转录以下扫描页图片中的全部文字（尤其是标题、案号、法院/机关名称、"
    "举证期限天数、开庭时间与地点、购买方/销售方/发票号码/价税合计），保持原文顺序，输出纯文本。"
)

_SWIFT_VISION_SCRIPT = r"""
import Foundation
import Vision

let args = Array(CommandLine.arguments.dropFirst())
var outputs: [String] = []
for path in args {
    let url = URL(fileURLWithPath: path)
    let handler = VNImageRequestHandler(url: url, options: [:])
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    do {
        try handler.perform([req])
        let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        outputs.append(lines.joined(separator: "\n"))
    } catch {
        continue
    }
}
print(outputs.joined(separator: "\n"))
"""


@dataclass
class OcrExtractionResult:
    text: str
    source: str  # vlm | mac_vision | rapidocr | pytesseract | none

    @property
    def has_meaningful_text(self) -> bool:
        return len(re.sub(r"\s+", "", self.text or "")) >= 15


def render_pdf_pages_to_png_bytes(
    data: bytes,
    *,
    max_pages: int = MAX_OCR_PAGES,
    dpi: int = RENDER_DPI,
) -> list[bytes]:
    """Render up to `max_pages` pages of a PDF into PNG byte buffers via pypdfium2."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []

    scale = max(72, dpi) / 72.0
    images: list[bytes] = []
    doc = None
    try:
        doc = pdfium.PdfDocument(io.BytesIO(data))
        page_total = min(len(doc), max(1, max_pages))
        for idx in range(page_total):
            page = doc[idx]
            bitmap = page.render(scale=scale)
            pil_img = bitmap.to_pil().convert("RGB")
            w, h = pil_img.size
            longest = max(w, h)
            if longest > MAX_EDGE_PX:
                ratio = MAX_EDGE_PX / float(longest)
                pil_img = pil_img.resize(
                    (max(1, int(w * ratio)), max(1, int(h * ratio))),
                    Image.Resampling.LANCZOS,
                )
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            images.append(buf.getvalue())
            page.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("PDF rasterization failed: %s", exc)
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass
    return images


def _run_pyobjc_mac_vision(image_paths: list[str]) -> tuple[str, str] | None:
    if platform.system() != "Darwin":
        return None
    try:
        Vision = importlib.import_module("Vision")
        Foundation = importlib.import_module("Foundation")
    except Exception:  # noqa: BLE001
        return None

    chunks: list[str] = []
    for img_path in image_paths:
        url = Foundation.NSURL.fileURLWithPath_(img_path)
        handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        # 0 = accurate (must be accurate for zh-Hans; 1 = fast outputs garbled CJK)
        req.setRecognitionLevel_(0)
        req.setUsesLanguageCorrection_(True)
        req.setRecognitionLanguages_(["zh-Hans", "en-US"])
        ok, _err = handler.performRequests_error_([req], None)
        if not ok:
            continue
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if cands:
                chunks.append(str(cands[0].string()))
    text = "\n".join(chunks).strip()
    return (text, "mac_vision") if text else None


def _run_rapidocr(image_paths: list[str]) -> tuple[str, str] | None:
    engine = None
    for mod_name in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            mod = importlib.import_module(mod_name)
            engine = mod.RapidOCR()
            break
        except Exception:  # noqa: BLE001
            continue
    if engine is None:
        return None

    chunks: list[str] = []
    for img_path in image_paths:
        try:
            res, _ = engine(img_path)
            if res:
                for row in res:
                    if isinstance(row, (list, tuple)) and len(row) >= 2 and row[1]:
                        chunks.append(str(row[1]))
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(chunks).strip()
    return (text, "rapidocr") if text else None


def _run_pytesseract(image_paths: list[str]) -> tuple[str, str] | None:
    try:
        pytesseract = importlib.import_module("pytesseract")
    except Exception:  # noqa: BLE001
        return None

    chunks: list[str] = []
    for img_path in image_paths:
        try:
            with Image.open(img_path) as img:
                txt = pytesseract.image_to_string(img, lang="chi_sim+eng", config="--psm 4")
                if txt and txt.strip():
                    chunks.append(txt.strip())
        except Exception:  # noqa: BLE001
            continue
    text = "\n".join(chunks).strip()
    return (text, "pytesseract") if text else None


def _run_swift_mac_vision(image_paths: list[str]) -> tuple[str, str] | None:
    if platform.system() != "Darwin" or not image_paths:
        return None
    swift_bin = shutil.which("swift")
    if not swift_bin:
        return None
    try:
        proc = subprocess.run(
            [swift_bin, "-", *image_paths],
            input=_SWIFT_VISION_SCRIPT,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip(), "mac_vision"
    except Exception as exc:  # noqa: BLE001
        logger.debug("macOS Swift Vision OCR fallback skipped: %s", exc)
    return None


def _run_local_ocr_on_images(images_png: list[bytes]) -> tuple[str, str]:
    """Run local OCR ladder across PNG pages (PyObjC Vision -> RapidOCR -> Tesseract -> Swift Vision)."""
    if not images_png:
        return "", "none"

    temp_dir = Path(tempfile.mkdtemp(prefix="lawteam_ocr_"))
    image_paths: list[str] = []
    try:
        for idx, raw in enumerate(images_png):
            p = temp_dir / f"page_{idx}.png"
            p.write_bytes(raw)
            image_paths.append(str(p))

        for runner in (
            _run_pyobjc_mac_vision,
            _run_rapidocr,
            _run_pytesseract,
            _run_swift_mac_vision,
        ):
            res = runner(image_paths)
            if res is not None and res[0]:
                return res
        return "", "none"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _ocr_with_vlm(images_png: list[bytes]) -> str | None:
    client = UniversalLLMClient()
    if not client.is_enabled or not images_png:
        return None
    text = await client.transcribe_images(
        images_png,
        system_prompt=_OCR_SYSTEM_PROMPT,
        user_prompt=_OCR_USER_PROMPT,
    )
    return text.strip() if text and text.strip() else None


async def extract_scanned_pdf_text(
    data: bytes,
    *,
    max_pages: int = MAX_OCR_PAGES,
    dpi: int = RENDER_DPI,
) -> OcrExtractionResult:
    """Extract text from a scanned PDF using Vision LLM first, then local OCR fallback."""
    images = render_pdf_pages_to_png_bytes(data, max_pages=max_pages, dpi=dpi)
    if not images:
        return OcrExtractionResult(text="", source="none")

    vlm_text = await _ocr_with_vlm(images)
    if vlm_text and len(re.sub(r"\s+", "", vlm_text)) >= 15:
        return OcrExtractionResult(text=vlm_text, source="vlm")

    local_text, local_source = await asyncio.to_thread(_run_local_ocr_on_images, images)
    if local_text:
        return OcrExtractionResult(text=local_text, source=local_source)

    return OcrExtractionResult(text="", source="none")
