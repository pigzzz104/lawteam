from __future__ import annotations

import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import get_settings

STANDARD_CASE_SUBFOLDERS: tuple[str, ...] = (
    "01_司法与程序文书",
    "02_诉讼文书与法律意见",
    "03_证据与阅卷材料",
    "04_会见与庭审记录",
    "05_财务与发票",
)
UNCLAIMED_INVOICES_DIR = "_待认领发票"
STAGING_TRIAGE_DIR = "_待分拣暂存"

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE_RE = re.compile(r"\s+")


class PathSecurityError(ValueError):
    """Raised when a path escapes CASES_ROOT or traverses a symlink."""


def sanitize_segment(text: str, fallback: str = "未命名", max_len: int = 80) -> str:
    """Sanitize a single path segment (folder name or filename stem)."""
    cleaned = _ILLEGAL_CHARS_RE.sub("_", text or "")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" .")
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "_")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_len].strip(" .") or fallback


def sanitize_filename(filename: str, fallback: str = "document.bin", max_len: int = 120) -> str:
    """Sanitize a filename while preserving its extension."""
    raw = Path(filename or fallback).name
    p = Path(raw)
    stem = sanitize_segment(p.stem, fallback="document", max_len=max(20, max_len - 16))
    suffix = _ILLEGAL_CHARS_RE.sub("", p.suffix)[:16]
    return f"{stem}{suffix}" if suffix else stem


def get_cases_root() -> Path:
    """Return canonical resolved CASES_ROOT and ensure root + unclaimed folder exist."""
    root = get_settings().cases_root_path
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    (resolved_root / UNCLAIMED_INVOICES_DIR).mkdir(parents=True, exist_ok=True)
    return resolved_root


def resolve_safe_path(rel_path: str, must_exist: bool = False) -> Path:
    """
    Resolve a relative path strictly inside CASES_ROOT, rejecting absolute paths,
    '..' segments, and symlinks pointing outside CASES_ROOT.
    """
    if not rel_path:
        raise PathSecurityError("Relative path must not be empty")
    candidate_rel = Path(rel_path)
    if candidate_rel.is_absolute() or ".." in candidate_rel.parts:
        raise PathSecurityError(f"Illegal relative path: {rel_path}")

    root = get_cases_root()
    current = root
    for part in candidate_rel.parts:
        if part in ("", "."):
            continue
        current = current / part
        if current.exists() and current.is_symlink():
            raise PathSecurityError(f"Symlink traversal rejected: {current}")

    resolved = current.resolve()
    if resolved != root and root not in resolved.parents:
        raise PathSecurityError(f"Resolved path escapes CASES_ROOT: {resolved}")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"Path not found under CASES_ROOT: {rel_path}")
    return resolved


def build_case_folder_rel_path(
    *,
    case_number: Optional[str],
    client_name: str,
    case_title: str,
    year: Optional[int] = None,
) -> str:
    """
    Allocate a unique relative folder path `{YYYY}/{case_number_or_temp}_{client}` under CASES_ROOT.
    """
    settings = get_settings()
    target_year = year or datetime.now(settings.tz).year
    root = get_cases_root()
    year_dir = root / str(target_year)
    year_dir.mkdir(parents=True, exist_ok=True)

    prefix_raw = case_number.strip() if case_number and case_number.strip() else f"未立案_{case_title}"
    prefix = sanitize_segment(prefix_raw, fallback="新案件", max_len=60)
    client = sanitize_segment(client_name, fallback="委托人", max_len=40)
    base_name = f"{prefix}_{client}"

    candidate = base_name
    counter = 2
    while (year_dir / candidate).exists():
        candidate = f"{base_name}_{counter}"
        counter += 1
        if counter > 100:
            candidate = f"{base_name}_{secrets.token_hex(3)}"
            break

    return f"{target_year}/{candidate}"


def ensure_case_directories(folder_rel_path: str) -> Path:
    """Create the case directory and all 5 standardized subfolders cleanly inside CASES_ROOT."""
    case_dir = resolve_safe_path(folder_rel_path)
    case_dir.mkdir(parents=True, exist_ok=True)
    for sub in STANDARD_CASE_SUBFOLDERS:
        (case_dir / sub).mkdir(parents=True, exist_ok=True)
    return case_dir


def save_bytes_atomically(target_dir: Path, filename: str, data: bytes) -> Path:
    """
    Atomically write bytes into `target_dir` using exclusive create ('xb').
    On name collision, appends ` (2)`, ` (3)`, etc. without overwriting existing files.
    """
    root = get_cases_root()
    resolved_dir = target_dir.resolve()
    if resolved_dir != root and root not in resolved_dir.parents:
        raise PathSecurityError(f"Target directory escapes CASES_ROOT: {resolved_dir}")
    resolved_dir.mkdir(parents=True, exist_ok=True)

    clean_name = sanitize_filename(filename)
    p = Path(clean_name)
    stem, suffix = p.stem, p.suffix

    attempt = 1
    while True:
        candidate_name = clean_name if attempt == 1 else f"{stem} ({attempt}){suffix}"
        dest = resolved_dir / candidate_name
        try:
            with open(dest, "xb") as fh:
                fh.write(data)
            return dest
        except FileExistsError:
            attempt += 1
            if attempt > 200:
                unique_dest = resolved_dir / f"{stem}_{secrets.token_hex(4)}{suffix}"
                with open(unique_dest, "xb") as fh:
                    fh.write(data)
                return unique_dest


def save_to_case_subfolder(
    folder_rel_path: str,
    subfolder: str,
    filename: str,
    data: bytes,
) -> tuple[Path, str]:
    """Save bytes inside a case's subfolder and return `(abs_path, rel_path_to_cases_root)`."""
    if subfolder not in STANDARD_CASE_SUBFOLDERS and subfolder != UNCLAIMED_INVOICES_DIR:
        raise ValueError(f"Unsupported subfolder: {subfolder}")
    case_dir = ensure_case_directories(folder_rel_path)
    dest = save_bytes_atomically(case_dir / subfolder, filename, data)
    rel_to_root = dest.relative_to(get_cases_root()).as_posix()
    return dest, rel_to_root


def save_unclaimed_invoice(filename: str, data: bytes) -> tuple[Path, str]:
    """Save an unmatched invoice under `{CASES_ROOT}/_待认领发票/`."""
    root = get_cases_root()
    dest = save_bytes_atomically(root / UNCLAIMED_INVOICES_DIR, filename, data)
    rel_to_root = dest.relative_to(root).as_posix()
    return dest, rel_to_root


def save_staged_triage_file(batch_key: str, filename: str, data: bytes) -> tuple[Path, str]:
    """Save a pre-confirmation downloaded file under `{CASES_ROOT}/_待分拣暂存/{batch_key}/`."""
    safe_batch = sanitize_segment(batch_key, fallback="batch", max_len=48)
    rel_dir = f"{STAGING_TRIAGE_DIR}/{safe_batch}"
    target_dir = resolve_safe_path(rel_dir)
    dest = save_bytes_atomically(target_dir, filename, data)
    rel_to_root = dest.relative_to(get_cases_root()).as_posix()
    return dest, rel_to_root


def _move_file_avoiding_collision(
    src_rel_path: str,
    dest_dir: Path,
    desired_filename: Optional[str] = None,
) -> str:
    """Safely move a regular file under CASES_ROOT into ``dest_dir``, avoiding name collisions."""
    src = resolve_safe_path(src_rel_path, must_exist=True)
    if not src.is_file():
        raise FileNotFoundError(f"Source is not a regular file: {src_rel_path}")

    root = get_cases_root()
    resolved_dest_dir = dest_dir.resolve()
    if resolved_dest_dir != root and root not in resolved_dest_dir.parents:
        raise PathSecurityError(f"Target directory escapes CASES_ROOT: {resolved_dest_dir}")
    resolved_dest_dir.mkdir(parents=True, exist_ok=True)

    clean_name = sanitize_filename(desired_filename or src.name)
    p = Path(clean_name)
    stem, suffix = p.stem, p.suffix

    attempt = 1
    while True:
        candidate_name = clean_name if attempt == 1 else f"{stem} ({attempt}){suffix}"
        dest = resolved_dest_dir / candidate_name
        if not dest.exists():
            break
        attempt += 1

    shutil.move(os.fspath(src), os.fspath(dest))
    return dest.relative_to(root).as_posix()


def move_rel_file_to_case(
    src_rel_path: str,
    target_case_folder_rel_path: str,
    subfolder: str,
    desired_filename: Optional[str] = None,
) -> str:
    """Safely move a file under CASES_ROOT into a case subfolder, avoiding collisions."""
    if subfolder not in STANDARD_CASE_SUBFOLDERS:
        raise ValueError(f"Invalid target case subfolder: {subfolder}")
    case_dir = ensure_case_directories(target_case_folder_rel_path)
    return _move_file_avoiding_collision(src_rel_path, case_dir / subfolder, desired_filename)


def move_rel_file_to_staging(
    src_rel_path: str,
    *,
    is_invoice: bool,
    batch_key: str,
    desired_filename: Optional[str] = None,
) -> str:
    """Move a file back into ``_待认领发票`` or ``_待分拣暂存/{batch_key}`` when reopening triage."""
    if is_invoice:
        dest_dir = get_cases_root() / UNCLAIMED_INVOICES_DIR
    else:
        safe_batch = sanitize_segment(batch_key, fallback="reopen", max_len=48)
        dest_dir = resolve_safe_path(f"{STAGING_TRIAGE_DIR}/{safe_batch}")
    return _move_file_avoiding_collision(src_rel_path, dest_dir, desired_filename)


def list_case_files(folder_rel_path: str) -> dict[str, list[dict[str, Any]]]:
    """List files grouped across the 5 standard subfolders of a case."""
    case_dir = ensure_case_directories(folder_rel_path)
    root = get_cases_root()
    listing: dict[str, list[dict[str, Any]]] = {}

    for sub in STANDARD_CASE_SUBFOLDERS:
        sub_dir = case_dir / sub
        items: list[dict[str, Any]] = []
        if sub_dir.is_dir():
            for entry in sorted(sub_dir.iterdir(), key=lambda p: p.name):
                if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
                    continue
                stat = entry.stat()
                items.append(
                    {
                        "file_name": entry.name,
                        "rel_path": entry.relative_to(root).as_posix(),
                        "size_bytes": stat.st_size,
                        "modified_at": datetime.fromtimestamp(
                            stat.st_mtime, tz=get_settings().tz
                        ).isoformat(),
                    }
                )
        listing[sub] = items
    return listing


def remove_empty_case_directory(folder_rel_path: str) -> bool:
    """Safely remove an auto-created case directory tree ONLY if it contains no regular files."""
    if not folder_rel_path:
        return False
    try:
        case_dir = resolve_safe_path(folder_rel_path, must_exist=True)
    except (PathSecurityError, FileNotFoundError):
        return False
    if not case_dir.is_dir():
        return False
    for p in case_dir.rglob("*"):
        if p.is_file() or p.is_symlink():
            return False
    shutil.rmtree(case_dir, ignore_errors=True)
    return True


def format_court_doc_filename(
    *,
    received_date: str,
    case_number: Optional[str],
    doc_title: str,
    ext: str = ".pdf",
) -> str:
    """Format court delivery filename: `{YYYY-MM-DD}_{case_number}_{doc_title}.{ext}`."""
    safe_ext = ext if ext.startswith(".") else f".{ext}"
    date_part = sanitize_segment(received_date, fallback="unknown-date", max_len=16)
    case_part = sanitize_segment(case_number or "未立案", fallback="未立案", max_len=40)
    title_part = sanitize_segment(doc_title, fallback="司法文书", max_len=50)
    return sanitize_filename(f"{date_part}_{case_part}_{title_part}{safe_ext}")
