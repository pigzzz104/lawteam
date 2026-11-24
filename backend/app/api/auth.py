from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import ALLOWED_COURT_AUTO_FETCH_MODES, get_settings
from app.core.security import (
    create_access_token,
    get_current_user,
    verify_password,
)
from app.core.url_safety import UnsafeUrlError, validate_webhook_url
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
    court_auto_fetch_mode: str
    calendar_color: str
    sms_webhook_token: str
    has_sms_webhook_secret: bool
    ics_feed_token: str
    default_deadline_offsets_days: list[int]
    default_schedule_offsets_minutes: list[int]
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
    court_auto_fetch_mode: Optional[str] = None
    calendar_color: Optional[str] = None
    sms_webhook_secret: Optional[str] = None
    default_deadline_offsets_days: Optional[list[int]] = None
    default_schedule_offsets_minutes: Optional[list[int]] = None
    rotate_sms_token: bool = False
    rotate_ics_token: bool = False


def serialize_user_self(user: User) -> UserSelfRead:
    assert user.id is not None
    eff_fetch_mode = user.court_auto_fetch_mode or get_settings().court_auto_fetch_mode
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
        court_auto_fetch_mode=eff_fetch_mode,
        calendar_color=user.calendar_color,
        sms_webhook_token=user.sms_webhook_token,
        has_sms_webhook_secret=bool(user.sms_webhook_secret),
        ics_feed_token=user.ics_feed_token,
        default_deadline_offsets_days=list(user.default_deadline_offsets_days or []),
        default_schedule_offsets_minutes=list(user.default_schedule_offsets_minutes or []),
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
    updates = payload.model_dump(exclude_unset=True)

    if "full_name" in updates and updates["full_name"] is not None:
        current_user.full_name = str(updates["full_name"]).strip() or current_user.full_name
    if "wecom_userid" in updates:
        raw_wc = updates["wecom_userid"]
        current_user.wecom_userid = str(raw_wc).strip() or None if raw_wc is not None else None
    if "notify_channel" in updates and updates["notify_channel"] is not None:
        allowed_channels = ("wecom", "serverchan", "wxpusher", "pushplus", "webhook")
        ch = str(updates["notify_channel"]).strip().lower()
        if ch not in allowed_channels:
            raise HTTPException(
                status_code=400,
                detail=f"notify_channel must be one of {allowed_channels}",
            )
        current_user.notify_channel = ch
    if "notify_target" in updates:
        raw_target = updates["notify_target"]
        current_user.notify_target = (
            str(raw_target).strip() or None if raw_target is not None else None
        )

    eff_channel = (current_user.notify_channel or "wecom").strip().lower()
    if eff_channel == "webhook" and current_user.notify_target:
        try:
            validate_webhook_url(current_user.notify_target)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if (
        "default_deadline_offsets_days" in updates
        and updates["default_deadline_offsets_days"] is not None
    ):
        current_user.default_deadline_offsets_days = sorted(
            {int(x) for x in updates["default_deadline_offsets_days"] if int(x) >= 0},
            reverse=True,
        )
    if (
        "default_schedule_offsets_minutes" in updates
        and updates["default_schedule_offsets_minutes"] is not None
    ):
        current_user.default_schedule_offsets_minutes = sorted(
            {int(x) for x in updates["default_schedule_offsets_minutes"] if int(x) >= 0},
            reverse=True,
        )
    if "phone_os" in updates and updates["phone_os"] is not None:
        if updates["phone_os"] not in ("ios", "android"):
            raise HTTPException(status_code=400, detail="phone_os must be 'ios' or 'android'")
        current_user.phone_os = updates["phone_os"]
    if "phone_last4" in updates:
        raw_p4 = updates["phone_last4"]
        current_user.phone_last4 = (
            str(raw_p4).strip()[:4] or None if raw_p4 is not None else None
        )
    if "id_card_last4" in updates:
        raw_id4 = updates["id_card_last4"]
        current_user.id_card_last4 = (
            str(raw_id4).strip()[:4] or None if raw_id4 is not None else None
        )
    if "court_auto_fetch_mode" in updates:
        raw_mode = updates["court_auto_fetch_mode"]
        mode = str(raw_mode).strip().lower() if raw_mode is not None else ""
        if not mode:
            current_user.court_auto_fetch_mode = None
        elif mode not in ALLOWED_COURT_AUTO_FETCH_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"court_auto_fetch_mode must be one of {ALLOWED_COURT_AUTO_FETCH_MODES}",
            )
        else:
            current_user.court_auto_fetch_mode = mode
    if "calendar_color" in updates and updates["calendar_color"] is not None:
        current_user.calendar_color = str(updates["calendar_color"]).strip() or current_user.calendar_color
    if "sms_webhook_secret" in updates:
        raw_sec = updates["sms_webhook_secret"]
        current_user.sms_webhook_secret = (
            str(raw_sec).strip() or None if raw_sec is not None else None
        )
    if payload.rotate_sms_token:
        current_user.sms_webhook_token = secrets.token_urlsafe(16)
    if payload.rotate_ics_token:
        current_user.ics_feed_token = secrets.token_urlsafe(24)

    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return serialize_user_self(current_user)
