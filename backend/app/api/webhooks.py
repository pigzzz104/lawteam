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
    status,
)
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models.entities import User
from app.services.sms_normalizer import normalize_sms_request, verify_hmac_signature
from app.services.triage_pipeline import (
    enrich_and_fetch_triage_item,
    ingest_sms_and_create_triage,
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
