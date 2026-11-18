"""Unified cross-platform SMS webhook endpoints returning HTTP 202 within <200ms."""

from __future__ import annotations

from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import get_settings
from app.core.security import check_triage_access, decode_triage_action_token
from app.database import get_session
from app.models.entities import Case, InboxTriageItem, User
from app.services.sms_normalizer import normalize_sms_request, verify_hmac_signature
from app.services.triage_pipeline import (
    enrich_and_fetch_triage_item,
    ingest_sms_and_create_triage,
)
from app.services.wecom_callback import (
    decrypt_wecom_message,
    execute_triage_quick_action,
    parse_triage_event_key,
    parse_wecom_xml,
    render_quick_action_html,
    render_triage_action_confirm_html,
    verify_wecom_signature,
)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class SmsWebhookAck(BaseModel):
    status: str  # accepted | duplicate
    sms_id: int
    triage_id: int
    dedup_key: str
    client_platform: str


async def _handle_sms_webhook(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session,
) -> SmsWebhookAck:
    user = session.exec(
        select(User).where(User.sms_webhook_token == token)
    ).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive webhook token",
        )

    raw_body = await request.body()
    try:
        normalized = normalize_sms_request(
            raw_body=raw_body,
            content_type=request.headers.get("content-type", ""),
            query_params=request.query_params,
            headers=request.headers,
            user=user,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if not verify_hmac_signature(user, normalized):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC webhook signature",
        )

    sms_row, triage, is_duplicate = ingest_sms_and_create_triage(
        session, user, normalized
    )
    assert sms_row.id is not None and triage.id is not None

    if not is_duplicate:
        background_tasks.add_task(enrich_and_fetch_triage_item, triage.id)

    return SmsWebhookAck(
        status="duplicate" if is_duplicate else "accepted",
        sms_id=sms_row.id,
        triage_id=triage.id,
        dedup_key=normalized.dedup_key,
        client_platform=normalized.client_platform,
    )


@router.post("/sms", response_model=SmsWebhookAck, status_code=status.HTTP_202_ACCEPTED)
async def receive_sms_webhook_query(
    request: Request,
    background_tasks: BackgroundTasks,
    token: Optional[str] = Query(default=None),
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
    session: Session = Depends(get_session),
) -> SmsWebhookAck:
    resolved_token = (token or x_webhook_token or "").strip()
    if not resolved_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing SMS webhook token",
        )
    return await _handle_sms_webhook(resolved_token, request, background_tasks, session)


@router.post(
    "/sms/{webhook_token}",
    response_model=SmsWebhookAck,
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_sms_webhook_path(
    webhook_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> SmsWebhookAck:
    return await _handle_sms_webhook(webhook_token.strip(), request, background_tasks, session)


def _resolve_signed_quick_action_context(
    *,
    token: str,
    session: Session,
) -> tuple[dict, User, InboxTriageItem]:
    payload = decode_triage_action_token(token)
    try:
        user_id = int(payload["sub"])
        triage_id = int(payload["triage_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed triage action token") from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    item = check_triage_access(session, user, session.get(InboxTriageItem, triage_id))
    current_epoch = int((item.extracted_payload or {}).get("action_epoch") or 0)
    token_epoch = int(payload.get("action_epoch") or 0)
    if token_epoch != current_epoch:
        raise HTTPException(
            status_code=409,
            detail="This quick-action link has been invalidated after reopen",
        )
    return payload, user, item


def _run_signed_quick_action(
    *,
    token: str,
    session: Session,
    source: str,
) -> tuple[dict, int]:
    payload, user, item = _resolve_signed_quick_action_context(token=token, session=session)
    assert item.id is not None
    result = execute_triage_quick_action(
        session,
        item=item,
        actor_user=user,
        action=str(payload.get("action") or ""),
        case_id=payload.get("case_id"),
        source=source,
    )
    return result, item.id


@router.get("/triage-action")
def preview_signed_triage_quick_action(
    token: str = Query(...),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render a read-only confirmation page for signed mobile links (never mutates DB on GET)."""
    clean_tok = token.strip()
    payload, user, item = _resolve_signed_quick_action_context(token=clean_tok, session=session)
    resolved_case_id = payload.get("case_id") or item.matched_case_id
    target_case = session.get(Case, resolved_case_id) if resolved_case_id else None
    if target_case is not None and target_case.visibility.value == "private" and target_case.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Access denied for private case")
    settings = get_settings()
    return HTMLResponse(
        content=render_triage_action_confirm_html(
            token=clean_tok,
            action=str(payload.get("action") or ""),
            item=item,
            target_case=target_case,
            frontend_base_url=settings.frontend_base_url,
        )
    )


@router.post("/triage-action")
async def execute_signed_triage_quick_action(
    request: Request,
    token: Optional[str] = Query(default=None),
    response_format: Optional[str] = Query(default=None, alias="format"),
    session: Session = Depends(get_session),
) -> Response:
    """Execute signed mobile quick-action links via explicit POST."""
    resolved_token = (token or "").strip()
    if not resolved_token:
        ct = (request.headers.get("content-type") or "").lower()
        if "application/x-www-form-urlencoded" in ct or "multipart/form-data" in ct:
            form = await request.form()
            resolved_token = str(form.get("token") or "").strip()
        elif "application/json" in ct:
            body = await request.json()
            if isinstance(body, dict):
                resolved_token = str(body.get("token") or "").strip()
    if not resolved_token:
        raise HTTPException(status_code=400, detail="Missing triage action token")

    result, triage_id = _run_signed_quick_action(
        token=resolved_token, session=session, source="wechat_quick_link"
    )
    wants_json = (
        response_format == "json"
        or "application/json" in request.headers.get("accept", "").lower()
    )
    if wants_json:
        return JSONResponse(content=result)
    settings = get_settings()
    return HTMLResponse(
        content=render_quick_action_html(result, settings.frontend_base_url, triage_id)
    )


@router.get("/wecom/callback")
def verify_wecom_callback_url(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
) -> Response:
    """WeCom app callback URL verification endpoint (GET echostr challenge)."""
    settings = get_settings()
    if not settings.wecom_callback_token:
        raise HTTPException(status_code=503, detail="WECOM_CALLBACK_TOKEN is not configured")
    if not verify_wecom_signature(
        settings.wecom_callback_token, timestamp, nonce, msg_signature, echostr
    ):
        raise HTTPException(status_code=403, detail="Invalid WeCom callback signature")

    if settings.wecom_encoding_aes_key:
        try:
            plain_echo = decrypt_wecom_message(
                settings.wecom_encoding_aes_key,
                echostr,
                expected_corp_id=settings.wecom_corp_id.strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        plain_echo = echostr
    return Response(content=plain_echo, media_type="text/plain")


@router.post("/wecom/callback")
async def receive_wecom_callback_event(
    request: Request,
    msg_signature: Optional[str] = Query(default=None),
    timestamp: Optional[str] = Query(default=None),
    nonce: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """Receive WeCom interactive button_interaction / menu callbacks and update triage atomically."""
    settings = get_settings()
    raw_body = (await request.body()).decode("utf-8", errors="ignore").strip()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty WeCom callback body")

    try:
        outer = parse_wecom_xml(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid WeCom XML payload: {exc}") from exc

    if not settings.wecom_callback_token:
        raise HTTPException(status_code=503, detail="WECOM_CALLBACK_TOKEN is not configured")

    encrypted = outer.get("Encrypt", "")
    if not verify_wecom_signature(
        settings.wecom_callback_token,
        timestamp or "",
        nonce or "",
        msg_signature or "",
        encrypted,
    ):
        raise HTTPException(status_code=403, detail="Invalid WeCom callback signature")

    if settings.wecom_encoding_aes_key and not encrypted:
        raise HTTPException(status_code=400, detail="Encrypted WeCom payload is required")

    if encrypted:
        if not settings.wecom_encoding_aes_key:
            raise HTTPException(
                status_code=503, detail="WECOM_ENCODING_AES_KEY is required for encrypted callbacks"
            )
        try:
            inner_xml = decrypt_wecom_message(
                settings.wecom_encoding_aes_key,
                encrypted,
                expected_corp_id=settings.wecom_corp_id.strip() or None,
            )
            event_fields = parse_wecom_xml(inner_xml)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed decrypting WeCom payload: {exc}") from exc
    else:
        event_fields = outer

    event_key = event_fields.get("EventKey", "").strip()
    from_user = event_fields.get("FromUserName", "").strip()
    parsed_key = parse_triage_event_key(event_key)
    if parsed_key is None:
        return JSONResponse(content={"ok": True, "ignored_event": event_fields.get("Event", "")})

    if not from_user:
        raise HTTPException(status_code=400, detail="Missing FromUserName in WeCom event")
    actor = session.exec(select(User).where(User.wecom_userid == from_user)).first()
    if actor is None or not actor.is_active:
        raise HTTPException(status_code=403, detail="Unrecognized or inactive WeCom user")

    action, triage_id, case_id = parsed_key
    item = check_triage_access(session, actor, session.get(InboxTriageItem, triage_id))

    result = execute_triage_quick_action(
        session,
        item=item,
        actor_user=actor,
        action=action,
        case_id=case_id,
        source="wecom_callback",
    )
    return JSONResponse(content=result)
