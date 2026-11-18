from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.security import (
    create_access_token,
    get_current_user,
    verify_password,
)
from app.database import get_session
from app.models.entities import SystemRole, User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserSelfRead(BaseModel):
    id: int
    username: str
    full_name: str
    system_role: SystemRole
    wecom_userid: Optional[str]
    notify_channel: str
    notify_target: Optional[str]
    phone_os: str
    phone_last4: Optional[str]
    id_card_last4: Optional[str]
    calendar_color: str
    sms_webhook_token: str
    has_sms_webhook_secret: bool
    ics_feed_token: str
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSelfRead


class UserSelfUpdate(BaseModel):
    full_name: Optional[str] = None
    wecom_userid: Optional[str] = None
    notify_channel: Optional[str] = None
    notify_target: Optional[str] = None
    phone_os: Optional[str] = None
    phone_last4: Optional[str] = None
    id_card_last4: Optional[str] = None
    calendar_color: Optional[str] = None
    sms_webhook_secret: Optional[str] = None
    rotate_sms_token: bool = False
    rotate_ics_token: bool = False


def serialize_user_self(user: User) -> UserSelfRead:
    assert user.id is not None
    return UserSelfRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        system_role=user.system_role,
        wecom_userid=user.wecom_userid,
        notify_channel=user.notify_channel,
        notify_target=user.notify_target,
        phone_os=user.phone_os,
        phone_last4=user.phone_last4,
        id_card_last4=user.id_card_last4,
        calendar_color=user.calendar_color,
        sms_webhook_token=user.sms_webhook_token,
        has_sms_webhook_secret=bool(user.sms_webhook_secret),
        ics_feed_token=user.ics_feed_token,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def _authenticate_user(session: Session, username: str, password: str) -> User:
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )
    return user


@router.post("/token", response_model=TokenResponse)
def login_oauth_form(
    form_data: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
) -> TokenResponse:
    """OAuth2 password form login endpoint."""
    user = _authenticate_user(session, form_data.username, form_data.password)
    assert user.id is not None
    token = create_access_token(user.id, user.username, user.system_role)
    return TokenResponse(access_token=token, user=serialize_user_self(user))


@router.post("/login", response_model=TokenResponse)
def login_json(
    payload: LoginRequest,
    session: Session = Depends(get_session),
) -> TokenResponse:
    """JSON login endpoint for frontend clients."""
    user = _authenticate_user(session, payload.username, payload.password)
    assert user.id is not None
    token = create_access_token(user.id, user.username, user.system_role)
    return TokenResponse(access_token=token, user=serialize_user_self(user))


@router.get("/me", response_model=UserSelfRead)
def read_me(current_user: User = Depends(get_current_user)) -> UserSelfRead:
    """Return current authenticated user profile and webhook/ICS tokens."""
    return serialize_user_self(current_user)


@router.patch("/me", response_model=UserSelfRead)
def update_me(
    payload: UserSelfUpdate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UserSelfRead:
    """Allow user to update personal mobile/WeCom profile or rotate tokens."""
    if payload.full_name is not None:
        current_user.full_name = payload.full_name.strip() or current_user.full_name
    if payload.wecom_userid is not None:
        current_user.wecom_userid = payload.wecom_userid.strip() or None
    if payload.notify_channel is not None:
        allowed_channels = ("wecom", "serverchan", "wxpusher", "pushplus", "webhook")
        ch = payload.notify_channel.strip().lower()
        if ch not in allowed_channels:
            raise HTTPException(
                status_code=400,
                detail=f"notify_channel must be one of {allowed_channels}",
            )
        current_user.notify_channel = ch
    if payload.notify_target is not None:
        current_user.notify_target = payload.notify_target.strip() or None
    if payload.phone_os is not None:
        if payload.phone_os not in ("ios", "android"):
            raise HTTPException(status_code=400, detail="phone_os must be 'ios' or 'android'")
        current_user.phone_os = payload.phone_os
    if payload.phone_last4 is not None:
        current_user.phone_last4 = payload.phone_last4.strip()[:4] or None
    if payload.id_card_last4 is not None:
        current_user.id_card_last4 = payload.id_card_last4.strip()[:4] or None
    if payload.calendar_color is not None:
        current_user.calendar_color = payload.calendar_color.strip()
    if payload.sms_webhook_secret is not None:
        current_user.sms_webhook_secret = payload.sms_webhook_secret.strip() or None
    if payload.rotate_sms_token:
        current_user.sms_webhook_token = secrets.token_urlsafe(16)
    if payload.rotate_ics_token:
        current_user.ics_feed_token = secrets.token_urlsafe(24)

    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return serialize_user_self(current_user)
