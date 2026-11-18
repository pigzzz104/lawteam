"""WeCom interactive callback signature/AES crypto helpers and atomic triage action handler."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import struct
import xml.etree.ElementTree as ET
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlmodel import Session, select

from app.core.security import check_case_access, check_triage_access
from app.models.entities import Case, InboxTriageItem, TriageStatus, User
from app.services.triage_archiver import (
    CaseGlobalConflictError,
    apply_triage_to_case,
    auto_create_case_for_triage,
    build_confirm_kwargs_from_triage,
    mark_triage_ignored,
)


def compute_wecom_signature(
    token: str,
    timestamp: str,
    nonce: str,
    msg_encrypt: str = "",
) -> str:
    """Compute official WeCom SHA-1 callback signature."""
    parts = sorted([token, timestamp, nonce, msg_encrypt])
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify_wecom_signature(
    token: str,
    timestamp: str,
    nonce: str,
    msg_signature: str,
    msg_encrypt: str = "",
) -> bool:
    if not token or not msg_signature:
        return False
    expected = compute_wecom_signature(token, timestamp, nonce, msg_encrypt)
    return hmac.compare_digest(expected, msg_signature.strip().lower())


def decrypt_wecom_message(
    encoding_aes_key: str,
    msg_encrypt: str,
    expected_corp_id: Optional[str] = None,
) -> str:
    """Decrypt WeCom AES-256-CBC base64 payload and validate PKCS#7 / CorpID."""
    key_b64 = encoding_aes_key.strip() + "="
    aes_key = base64.b64decode(key_b64)
    if len(aes_key) != 32:
        raise ValueError("Invalid WECOM_ENCODING_AES_KEY length")

    iv = aes_key[:16]
    ciphertext = base64.b64decode(msg_encrypt.strip())
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(ciphertext) + decryptor.finalize()
    if not plain:
        raise ValueError("Empty decrypted WeCom payload")

    pad = plain[-1]
    if pad < 1 or pad > 32 or plain[-pad:] != bytes([pad]) * pad:
        raise ValueError("Invalid PKCS#7 padding in WeCom payload")
    unpadded = plain[:-pad]
    if len(unpadded) < 20:
        raise ValueError("Truncated WeCom payload")

    msg_len = struct.unpack("!I", unpadded[16:20])[0]
    msg_bytes = unpadded[20 : 20 + msg_len]
    corp_bytes = unpadded[20 + msg_len :]
    if expected_corp_id and corp_bytes.decode("utf-8", errors="ignore") != expected_corp_id:
        raise ValueError("WeCom CorpID mismatch")
    return msg_bytes.decode("utf-8")


def parse_wecom_xml(xml_text: str) -> dict[str, str]:
    """Safely extract flat tag values from WeCom callback XML."""
    clean_xml = xml_text.strip()
    if re.search(r"<!(?:DOCTYPE|ENTITY)\b", clean_xml, re.IGNORECASE):
        raise ValueError("XML DTD/Entity declarations are disallowed")
    root = ET.fromstring(clean_xml)
    result: dict[str, str] = {}
    for elem in root.iter():
        if elem is root:
            continue
        tag = elem.tag.split("}")[-1].strip()
        text = (elem.text or "").strip()
        if tag and text and tag not in result:
            result[tag] = text
    return result


def parse_triage_event_key(event_key: str) -> Optional[tuple[str, int, Optional[int]]]:
    """Parse ``triage:<confirm|ignore>:<triage_id>[:<case_id>]`` keys."""
    parts = (event_key or "").strip().split(":")
    if len(parts) < 3 or parts[0] != "triage":
        return None
    action = parts[1].strip().lower()
    if action not in ("confirm", "ignore"):
        return None
    try:
        triage_id = int(parts[2])
        case_id = int(parts[3]) if len(parts) >= 4 and parts[3] else None
    except ValueError:
        return None
    return action, triage_id, case_id


def execute_triage_quick_action(
    session: Session,
    *,
    item: InboxTriageItem,
    actor_user: User,
    action: str,
    case_id: Optional[int] = None,
    source: str = "wecom_callback",
) -> dict[str, Any]:
    """Atomically execute confirm/ignore on a triage item and persist AuditLog."""
    if item.id is not None:
        locked = session.exec(
            select(InboxTriageItem)
            .where(InboxTriageItem.id == item.id)
            .with_for_update()
        ).first()
        if locked is not None:
            item = locked

    check_triage_access(session, actor_user, item)
    if item.status in (TriageStatus.CONFIRMED, TriageStatus.IGNORED):
        return {
            "ok": True,
            "already_handled": True,
            "status": item.status.value,
            "triage_id": item.id,
            "case_id": item.matched_case_id,
            "message": "该收件箱事项此前已处理完成，无需重复操作。",
        }

    normalized_action = action.strip().lower()
    if normalized_action == "ignore":
        mark_triage_ignored(session, item=item, actor_user=actor_user, source=source)
        session.commit()
        session.refresh(item)
        return {
            "ok": True,
            "already_handled": False,
            "status": item.status.value,
            "triage_id": item.id,
            "case_id": item.matched_case_id,
            "message": "已忽略该条短信/通知记录。",
        }

    if normalized_action == "confirm":
        resolved_case_id = case_id or item.matched_case_id
        created_case = False
        if resolved_case_id:
            target_case = check_case_access(session.get(Case, resolved_case_id), actor_user, session)
        else:
            payload = dict(item.extracted_payload or {})
            if not payload.get("case_number"):
                return {
                    "ok": False,
                    "already_handled": False,
                    "status": item.status.value,
                    "triage_id": item.id,
                    "case_id": None,
                    "message": "该事项尚未匹配到推荐案件且缺少明确案号，请打开工作台收件箱手动选择案件归档。",
                }
            try:
                target_case, created_case, related_hint = auto_create_case_for_triage(
                    session, owner=actor_user, payload=payload
                )
                if related_hint:
                    payload["related_case_hint"] = related_hint
                    item.extracted_payload = payload
            except CaseGlobalConflictError:
                return {
                    "ok": False,
                    "already_handled": False,
                    "status": item.status.value,
                    "triage_id": item.id,
                    "case_id": None,
                    "message": "系统内已存在该案号的相关案件，请打开工作台收件箱人工核对归属。",
                }

        confirm_kwargs = build_confirm_kwargs_from_triage(item, target_case)
        res = apply_triage_to_case(
            session,
            item=item,
            actor_user=actor_user,
            target_case=target_case,
            auto_archived=False,
            created_case=created_case,
            triage_reason=f"通过微信快捷交互确认归入「{target_case.case_title}」",
            **confirm_kwargs,
        )
        session.commit()
        session.refresh(item)
        return {
            "ok": True,
            "already_handled": False,
            "status": item.status.value,
            "triage_id": item.id,
            "case_id": res.case_id,
            "case_title": target_case.case_title,
            "created_case": created_case,
            "generated_deadline_count": res.generated_deadline_count,
            "created_event_id": res.created_event_id,
            "created_schedule_id": res.created_schedule_id,
            "message": f"已确认归档至案件「{target_case.case_title}」（写入文档 {len(res.archived_documents)} 份，生成期限 {res.generated_deadline_count} 条）。",
        }

    return {
        "ok": False,
        "already_handled": False,
        "status": item.status.value,
        "triage_id": item.id,
        "case_id": item.matched_case_id,
        "message": f"不支持的快捷操作：{normalized_action}",
    }


def render_quick_action_html(result: dict[str, Any], frontend_base_url: str, triage_id: int) -> str:
    """Render a clean mobile-friendly confirmation HTML page for WeChat taps."""
    ok = bool(result.get("ok"))
    title = "LawTeam-OS 操作完成" if ok else "LawTeam-OS 需要进一步确认"
    msg = html.escape(str(result.get("message") or ""))
    base = frontend_base_url.rstrip("/")
    target_url = (
        f"{base}/cases/{result['case_id']}"
        if result.get("case_id")
        else f"{base}/inbox?id={triage_id}"
    )
    badge_color = "#059669" if ok else "#d97706"
    badge_text = "处理成功" if ok else "需工作台处理"
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        f"<title>{title}</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;margin:0;padding:24px;color:#0f172a;}"
        ".card{max-width:460px;margin:32px auto;background:#fff;border-radius:16px;padding:24px;box-shadow:0 4px 20px rgba(15,23,42,.08);}"
        f".badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;color:#fff;background:{badge_color};}}"
        "h1{font-size:18px;margin:14px 0 8px;}p{font-size:14px;line-height:1.6;color:#334155;}"
        ".btn{display:inline-block;margin-top:18px;padding:10px 18px;border-radius:10px;background:#1d4ed8;color:#fff;text-decoration:none;font-size:14px;font-weight:600;}"
        "</style></head><body><div class='card'>"
        f"<span class='badge'>{badge_text}</span>"
        f"<h1>{title}</h1><p>{msg}</p>"
        f"<a class='btn' href='{html.escape(target_url)}'>前往系统查看</a>"
        "</div></body></html>"
    )


def render_triage_action_confirm_html(
    *,
    token: str,
    action: str,
    item: InboxTriageItem,
    target_case: Optional[Case],
    frontend_base_url: str,
) -> str:
    """Render a read-only GET confirmation page that posts to /api/webhooks/triage-action."""
    triage_id = item.id or 0
    base = frontend_base_url.rstrip("/")
    payload = item.extracted_payload or {}
    hint = html.escape(str(payload.get("doc_title_hint") or payload.get("case_number") or f"待分拣项 #{triage_id}"))
    if item.status in (TriageStatus.CONFIRMED, TriageStatus.IGNORED):
        return render_quick_action_html(
            {
                "ok": True,
                "case_id": item.matched_case_id,
                "message": "该收件箱事项此前已处理完成，无需重复操作。",
            },
            frontend_base_url,
            triage_id,
        )

    is_confirm = action.strip().lower() == "confirm"
    action_label = "确认归入案件" if is_confirm else "忽略该条短信/通知"
    case_line = (
        f"<p><strong>目标案件：</strong>{html.escape(target_case.case_title)}</p>"
        if is_confirm and target_case is not None
        else ""
    )
    btn_bg = "#1b56b3" if is_confirm else "#b3261e"
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'/>"
        f"<title>LawTeam-OS 确认操作</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f7f9fc;margin:0;padding:24px;color:#181c23;}"
        ".card{max-width:460px;margin:32px auto;background:#fff;border-radius:20px;padding:24px;border:1px solid #cdd5e2;}"
        "h1{font-size:18px;margin:0 0 12px;}p{font-size:14px;line-height:1.6;color:#434955;margin:6px 0;}"
        f".btn-submit{{display:inline-block;margin-top:16px;padding:10px 20px;border:0;border-radius:999px;background:{btn_bg};color:#fff;font-size:14px;font-weight:600;cursor:pointer;}}"
        ".btn-link{display:inline-block;margin-left:12px;color:#1b56b3;text-decoration:none;font-size:14px;}"
        "</style></head><body><div class='card'>"
        f"<h1>请确认：{html.escape(action_label)}</h1>"
        f"<p><strong>事项：</strong>{hint}</p>"
        f"{case_line}"
        "<form method='post' action='/api/webhooks/triage-action'>"
        f"<input type='hidden' name='token' value='{html.escape(token)}' />"
        f"<button type='submit' class='btn-submit'>{html.escape(action_label)}</button>"
        f"<a class='btn-link' href='{html.escape(base)}/inbox?id={triage_id}'>打开工作台</a>"
        "</form></div></body></html>"
    )
