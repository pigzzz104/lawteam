"""Cross-platform SMS webhook payload normalizer and time-windowed deduplication.

Supports:
- iOS Shortcuts (JSON body with ``content``/``msg``/``text`` and optional ``sender``)
- macOS ``MacSMSForwarder`` (iCloud SMS relay JSON)
- Android ``SmsForwarder`` (JSON or form-urlencoded with ``from``/``content``/``timestamp``/``sign``)
- Plain text fallback
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, unquote_plus
from zoneinfo import ZoneInfo

from app.models.entities import User

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass
class NormalizedSmsPayload:
    client_platform: str
    sender_number: str
    content: str
    received_at: datetime
    shanghai_date: str
    content_hash: str
    dedup_key: str
    timestamp_raw: Optional[str] = None
    signature: Optional[str] = None


def _pick_first(data: Mapping[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        val = data.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return None


def _parse_received_at(raw_ts: Optional[str]) -> datetime:
    if not raw_ts:
        return datetime.now(timezone.utc)
    ts_str = raw_ts.strip()
    # Epoch seconds or milliseconds
    if ts_str.isdigit():
        num = int(ts_str)
        if num > 10_000_000_000:
            return datetime.fromtimestamp(num / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(float(num), tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SHANGHAI_TZ)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(ts_str, fmt).replace(tzinfo=SHANGHAI_TZ)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _decode_raw_body(raw_body: bytes, content_type: str) -> dict[str, Any]:
    text = raw_body.decode("utf-8", errors="replace").strip()
    if not text:
        return {}

    ct = (content_type or "").lower()
    if "application/json" in ct or text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return {"content": str(parsed)}
        except json.JSONDecodeError:
            if "application/json" in ct:
                raise ValueError("Invalid JSON SMS webhook payload")

    if "application/x-www-form-urlencoded" in ct or ("=" in text and "&" in text and "{" not in text):
        qs = parse_qs(text, keep_blank_values=False)
        return {k: v[0] for k, v in qs.items() if v}

    return {"content": text}


def _infer_platform(
    explicit_platform: Optional[str],
    payload: Mapping[str, Any],
) -> str:
    if explicit_platform:
        norm = explicit_platform.strip().lower()
        aliases = {
            "ios": "ios_shortcuts",
            "ios_shortcuts": "ios_shortcuts",
            "shortcuts": "ios_shortcuts",
            "mac": "mac_relay",
            "mac_relay": "mac_relay",
            "macsmsforwarder": "mac_relay",
            "android": "android_smsforwarder",
            "android_smsforwarder": "android_smsforwarder",
            "smsforwarder": "android_smsforwarder",
        }
        return aliases.get(norm, norm)
    if any(k in payload for k in ("sign", "sim_slot", "receive_time")):
        return "android_smsforwarder"
    if "handle" in payload:
        return "mac_relay"
    if any(k in payload for k in ("content", "msg", "text", "body")):
        return "ios_shortcuts"
    return "unknown"


def compute_sms_hashes(
    receiver_user_id: int,
    content: str,
    received_at: datetime,
) -> tuple[str, str, str]:
    """Compute `(shanghai_date, content_hash, dedup_key)` using Asia/Shanghai date boundary."""
    clean_content = content.strip()
    dt_utc = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
    shanghai_date = dt_utc.astimezone(SHANGHAI_TZ).date().isoformat()
    content_hash = hashlib.sha256(
        f"{receiver_user_id}:{clean_content}".encode("utf-8")
    ).hexdigest()
    dedup_key = hashlib.sha256(
        f"{receiver_user_id}:{clean_content}:{shanghai_date}".encode("utf-8")
    ).hexdigest()
    return shanghai_date, content_hash, dedup_key


def normalize_sms_request(
    *,
    raw_body: bytes,
    content_type: str,
    query_params: Mapping[str, str],
    headers: Mapping[str, str],
    user: User,
) -> NormalizedSmsPayload:
    """Parse multi-platform SMS forwarding payloads into a canonical structure."""
    assert user.id is not None
    payload = _decode_raw_body(raw_body, content_type)

    content = _pick_first(payload, ("content", "msg", "text", "body", "message", "sms"))
    if not content:
        raise ValueError("SMS payload missing non-empty content field")

    sender_number = (
        _pick_first(payload, ("sender", "from", "handle", "phone", "sender_number"))
        or "unknown"
    )
    platform_raw = query_params.get("platform") or _pick_first(
        payload, ("platform", "client_platform")
    )
    client_platform = _infer_platform(platform_raw, payload)

    ts_raw = _pick_first(payload, ("timestamp", "receive_time", "received_at"))
    received_at = _parse_received_at(ts_raw)
    shanghai_date, content_hash, dedup_key = compute_sms_hashes(
        user.id, content, received_at
    )

    sig = _pick_first(payload, ("sign", "signature")) or headers.get(
        "x-webhook-signature"
    ) or headers.get("x-sms-sign")

    return NormalizedSmsPayload(
        client_platform=client_platform,
        sender_number=sender_number,
        content=content.strip(),
        received_at=received_at,
        shanghai_date=shanghai_date,
        content_hash=content_hash,
        dedup_key=dedup_key,
        timestamp_raw=ts_raw,
        signature=sig,
    )


def verify_hmac_signature(user: User, normalized: NormalizedSmsPayload) -> bool:
    """Validate optional HMAC signature when ``user.sms_webhook_secret`` is configured."""
    secret = (user.sms_webhook_secret or "").strip()
    if not secret:
        return True
    sig = (normalized.signature or "").strip()
    if not sig:
        return False

    secret_bytes = secret.encode("utf-8")
    candidates: list[str] = []

    # Android SmsForwarder v3 style: Base64(HMAC_SHA256(secret, "{timestamp}\n{secret}"))
    if normalized.timestamp_raw:
        ts = normalized.timestamp_raw.strip()
        msg_bytes = f"{ts}\n{secret}".encode("utf-8")
        digest = hmac.new(secret_bytes, msg_bytes, hashlib.sha256).digest()
        candidates.append(base64.b64encode(digest).decode("ascii"))
        candidates.append(digest.hex())

    # Generic content HMAC (hex or base64, with or without sha256= prefix)
    content_digest = hmac.new(
        secret_bytes, normalized.content.encode("utf-8"), hashlib.sha256
    ).digest()
    candidates.append(content_digest.hex())
    candidates.append(f"sha256={content_digest.hex()}")
    candidates.append(base64.b64encode(content_digest).decode("ascii"))

    sig_clean = unquote_plus(sig)
    return any(
        hmac.compare_digest(sig, c) or hmac.compare_digest(sig_clean, c)
        for c in candidates
    )
