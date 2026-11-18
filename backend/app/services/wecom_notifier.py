from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.config import Settings, get_settings
from app.models.entities import User

logger = logging.getLogger(__name__)

# In-memory WeCom access_token cache: (corp_id, agent_id) -> (token, expires_at_monotonic)
_WECOM_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}

SUPPORTED_NOTIFY_CHANNELS = ("wecom", "serverchan", "wxpusher", "pushplus", "webhook")


@dataclass
class ChannelAttempt:
    channel: str
    recipient_user_ids: list[int]
    target_summary: str
    ok: bool
    dry_run: bool = False
    error: Optional[str] = None
    response_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchResult:
    ok: bool
    dry_run: bool
    attempts: list[ChannelAttempt] = field(default_factory=list)
    error_msg: Optional[str] = None
    summary: str = ""


def clear_wecom_token_cache() -> None:
    _WECOM_TOKEN_CACHE.clear()


def _resolve_user_target(user: User) -> tuple[str, Optional[str]]:
    channel = (user.notify_channel or "wecom").strip().lower()
    if channel == "wecom":
        target = (user.wecom_userid or user.notify_target or "").strip() or None
        return "wecom", target
    target = (user.notify_target or "").strip() or None
    return channel, target


def _get_wecom_access_token(
    settings: Settings,
    http_client: Optional[httpx.Client] = None,
) -> str:
    corp_id = settings.wecom_corp_id.strip()
    secret = settings.wecom_secret.strip()
    agent_id = settings.wecom_agent_id.strip()
    if not corp_id or not secret or not agent_id:
        raise RuntimeError(
            "WeCom credentials incomplete: set WECOM_CORP_ID, WECOM_AGENT_ID, WECOM_SECRET"
        )

    cache_key = (corp_id, agent_id)
    now_mono = time.monotonic()
    cached = _WECOM_TOKEN_CACHE.get(cache_key)
    if cached and cached[1] > now_mono + 60:
        return cached[0]

    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": corp_id, "corpsecret": secret}
    if http_client is not None:
        resp = http_client.get(url, params=params, timeout=10.0)
    else:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0 or not data.get("access_token"):
        raise RuntimeError(f"WeCom gettoken failed: errcode={data.get('errcode')} errmsg={data.get('errmsg')}")

    token = str(data["access_token"])
    expires_in = int(data.get("expires_in") or 7200)
    _WECOM_TOKEN_CACHE[cache_key] = (token, now_mono + max(expires_in, 120))
    return token


def _send_wecom_batch(
    *,
    user_ids: list[int],
    wecom_userids: list[str],
    title: str,
    markdown_body: str,
    textcard_url: Optional[str],
    settings: Settings,
    http_client: Optional[httpx.Client],
) -> ChannelAttempt:
    touser = "|".join(wecom_userids)
    full_markdown = f"### {title}\n{markdown_body}" if title else markdown_body
    if settings.wecom_dry_run:
        return ChannelAttempt(
            channel="wecom",
            recipient_user_ids=user_ids,
            target_summary=touser,
            ok=True,
            dry_run=True,
            response_payload={
                "dry_run": True,
                "touser": touser,
                "msgtype": "markdown",
                "title": title,
            },
        )

    try:
        token = _get_wecom_access_token(settings, http_client=http_client)
        payload: dict[str, Any] = {
            "touser": touser,
            "agentid": int(settings.wecom_agent_id) if settings.wecom_agent_id.isdigit() else settings.wecom_agent_id,
            "msgtype": "markdown",
            "markdown": {"content": full_markdown},
            "enable_duplicate_check": 1,
            "duplicate_check_interval": 300,
        }
        if textcard_url:
            payload = {
                "touser": touser,
                "agentid": payload["agentid"],
                "msgtype": "textcard",
                "textcard": {
                    "title": title[:128],
                    "description": markdown_body[:512],
                    "url": textcard_url,
                    "btntxt": "查看详情",
                },
            }

        url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
        if http_client is not None:
            resp = http_client.post(url, params={"access_token": token}, json=payload, timeout=10.0)
        else:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, params={"access_token": token}, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"WeCom message/send errcode={data.get('errcode')} errmsg={data.get('errmsg')}")
        return ChannelAttempt(
            channel="wecom",
            recipient_user_ids=user_ids,
            target_summary=touser,
            ok=True,
            dry_run=False,
            response_payload=data,
        )
    except Exception as exc:
        return ChannelAttempt(
            channel="wecom",
            recipient_user_ids=user_ids,
            target_summary=touser,
            ok=False,
            dry_run=False,
            error=str(exc),
        )


def _send_personal_channel(
    *,
    user: User,
    channel: str,
    target: str,
    title: str,
    markdown_body: str,
    url: Optional[str],
    settings: Settings,
    http_client: Optional[httpx.Client],
) -> ChannelAttempt:
    assert user.id is not None
    masked = target[:4] + "***" + target[-4:] if len(target) > 10 else target
    if settings.wecom_dry_run:
        return ChannelAttempt(
            channel=channel,
            recipient_user_ids=[user.id],
            target_summary=masked,
            ok=True,
            dry_run=True,
            response_payload={
                "dry_run": True,
                "channel": channel,
                "recipient": user.username,
                "title": title,
            },
        )

    def _post_json(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        if http_client is not None:
            r = http_client.post(endpoint, json=body, timeout=10.0)
        else:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(endpoint, json=body)
        r.raise_for_status()
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text}

    try:
        if channel == "serverchan":
            endpoint = f"https://sctapi.ftqq.com/{target}.send"
            data = _post_json(endpoint, {"title": title[:32] or "LawTeam提醒", "desp": markdown_body})
            if isinstance(data, dict) and data.get("code") not in (None, 0):
                raise RuntimeError(f"ServerChan error: {data.get('message') or data}")
        elif channel == "wxpusher":
            if ":" in target:
                app_token, uid = target.split(":", 1)
            else:
                app_token, uid = settings.wxpusher_app_token.strip(), target
            if not app_token:
                raise RuntimeError("WxPusher requires WXPUSHER_APP_TOKEN or 'appToken:UID_xxx'")
            data = _post_json(
                "https://wxpusher.zjiecode.com/api/send/message",
                {
                    "appToken": app_token,
                    "content": f"{title}\n\n{markdown_body}",
                    "summary": title[:100],
                    "contentType": 3,
                    "uids": [uid],
                    **({"url": url} if url else {}),
                },
            )
            if isinstance(data, dict) and not data.get("success", True):
                raise RuntimeError(f"WxPusher error: {data.get('msg') or data}")
        elif channel == "pushplus":
            data = _post_json(
                "https://www.pushplus.plus/send",
                {
                    "token": target,
                    "title": title[:100],
                    "content": markdown_body,
                    "template": "markdown",
                },
            )
            if isinstance(data, dict) and data.get("code") not in (None, 200):
                raise RuntimeError(f"PushPlus error: {data.get('msg') or data}")
        elif channel == "webhook":
            if not (target.startswith("http://") or target.startswith("https://")):
                raise RuntimeError("Webhook notify_target must be http:// or https:// URL")
            data = _post_json(
                target,
                {
                    "title": title,
                    "content": markdown_body,
                    "markdown": f"### {title}\n{markdown_body}",
                    "recipient_username": user.username,
                    "recipient_name": user.full_name,
                    "url": url,
                },
            )
        else:
            raise RuntimeError(f"Unsupported notify_channel: {channel}")

        return ChannelAttempt(
            channel=channel,
            recipient_user_ids=[user.id],
            target_summary=masked,
            ok=True,
            dry_run=False,
            response_payload=data if isinstance(data, dict) else {"raw": str(data)},
        )
    except Exception as exc:
        return ChannelAttempt(
            channel=channel,
            recipient_user_ids=[user.id],
            target_summary=masked,
            ok=False,
            dry_run=False,
            error=str(exc),
        )


def dispatch_notification_to_users(
    *,
    users: list[User],
    title: str,
    markdown_body: str,
    textcard_url: Optional[str] = None,
    settings: Optional[Settings] = None,
    http_client: Optional[httpx.Client] = None,
) -> DispatchResult:
    """Dispatch a reminder to recipients via their configured WeCom or personal WeChat channel."""
    cfg = settings or get_settings()
    if not users:
        return DispatchResult(
            ok=False,
            dry_run=cfg.wecom_dry_run,
            error_msg="No active recipient users resolved",
            summary="无有效接收人",
        )

    wecom_user_ids: list[int] = []
    wecom_targets: list[str] = []
    attempts: list[ChannelAttempt] = []
    errors: list[str] = []

    for user in users:
        if user.id is None:
            continue
        channel, target = _resolve_user_target(user)
        if not target:
            err = f"用户 {user.full_name}({user.username}) 未配置 {channel} 推送目标"
            attempts.append(
                ChannelAttempt(
                    channel=channel,
                    recipient_user_ids=[user.id],
                    target_summary="",
                    ok=False,
                    dry_run=cfg.wecom_dry_run,
                    error=err,
                )
            )
            errors.append(err)
            continue

        if channel == "wecom":
            wecom_user_ids.append(user.id)
            wecom_targets.append(target)
        else:
            res = _send_personal_channel(
                user=user,
                channel=channel,
                target=target,
                title=title,
                markdown_body=markdown_body,
                url=textcard_url,
                settings=cfg,
                http_client=http_client,
            )
            attempts.append(res)
            if not res.ok and res.error:
                errors.append(res.error)

    if wecom_targets:
        wecom_res = _send_wecom_batch(
            user_ids=wecom_user_ids,
            wecom_userids=wecom_targets,
            title=title,
            markdown_body=markdown_body,
            textcard_url=textcard_url,
            settings=cfg,
            http_client=http_client,
        )
        attempts.append(wecom_res)
        if not wecom_res.ok and wecom_res.error:
            errors.append(wecom_res.error)

    any_ok = any(a.ok for a in attempts)
    all_ok = bool(attempts) and all(a.ok for a in attempts)
    channels_used = ",".join(sorted({a.channel for a in attempts}))
    mode_tag = "DRY-RUN" if cfg.wecom_dry_run else "LIVE"
    summary = f"[{mode_tag}:{channels_used}] {title} -> {len(users)}人"
    if cfg.wecom_dry_run:
        logger.info("Notification dry-run: %s | errors=%s", summary, errors or "none")

    return DispatchResult(
        ok=all_ok if any_ok else False,
        dry_run=cfg.wecom_dry_run,
        attempts=attempts,
        error_msg="; ".join(errors) if errors else None,
        summary=summary,
    )
