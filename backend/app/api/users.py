from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import ALLOWED_COURT_AUTO_FETCH_MODES
from app.core.security import (
    get_current_user,
    hash_password,
    record_audit_log,
    require_admin,
)
from app.database import get_session
from app.models.entities import SystemRole, User

router = APIRouter(prefix="/api/users", tags=["users"])


class UserSummary(BaseModel):
    id: int
    username: str
    full_name: str
    system_role: SystemRole
    wecom_userid: Optional[str]
    notify_channel: str
    notify_target: Optional[str]
    phone_os: str
    phone_last4: Optional[str] = None
    id_card_last4: Optional[str] = None
    court_auto_fetch_mode: Optional[str] = None
    calendar_color: str
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    username: str
    full_name: str
    password: str
    system_role: SystemRole = SystemRole.LAWYER
    wecom_userid: Optional[str] = None
    notify_channel: str = "wecom"
    notify_target: Optional[str] = None
    phone_os: str = "ios"
    phone_last4: Optional[str] = None
    id_card_last4: Optional[str] = None
    court_auto_fetch_mode: Optional[str] = None
    calendar_color: str = "#3b82f6"


class UserAdminUpdate(BaseModel):
    full_name: Optional[str] = None
    system_role: Optional[SystemRole] = None
    wecom_userid: Optional[str] = None
    notify_channel: Optional[str] = None
    notify_target: Optional[str] = None
    phone_os: Optional[str] = None
    phone_last4: Optional[str] = None
    id_card_last4: Optional[str] = None
    court_auto_fetch_mode: Optional[str] = None
    calendar_color: Optional[str] = None
    is_active: Optional[bool] = None
    new_password: Optional[str] = None


def _to_summary(u: User, *, include_secrets: bool = True) -> UserSummary:
    assert u.id is not None
    return UserSummary(
        id=u.id,
        username=u.username,
        full_name=u.full_name,
        system_role=u.system_role,
        wecom_userid=u.wecom_userid,
        notify_channel=u.notify_channel,
        notify_target=u.notify_target if include_secrets else None,
        phone_os=u.phone_os,
        phone_last4=u.phone_last4,
        id_card_last4=u.id_card_last4 if include_secrets else None,
        court_auto_fetch_mode=u.court_auto_fetch_mode,
        calendar_color=u.calendar_color,
        is_active=u.is_active,
        created_at=u.created_at,
    )


@router.get("", response_model=list[UserSummary])
def list_users(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[UserSummary]:
    """List team members (non-admins see redacted personal push targets/ID fragments)."""
    users = session.exec(select(User).order_by(User.id)).all()
    is_admin = current_user.system_role == SystemRole.ADMIN
    return [
        _to_summary(u, include_secrets=(is_admin or u.id == current_user.id))
        for u in users
    ]


@router.post("", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> UserSummary:
    """Admin-only endpoint to create a new lawyer/assistant/admin account."""
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if payload.phone_os not in ("ios", "android"):
        raise HTTPException(status_code=400, detail="phone_os must be 'ios' or 'android'")

    allowed_channels = ("wecom", "serverchan", "wxpusher", "pushplus", "webhook")
    ch = payload.notify_channel.strip().lower()
    if ch not in allowed_channels:
        raise HTTPException(status_code=400, detail=f"notify_channel must be one of {allowed_channels}")

    existing = session.exec(select(User).where(User.username == username)).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already exists")

    fetch_mode: Optional[str] = None
    if payload.court_auto_fetch_mode is not None:
        mode = payload.court_auto_fetch_mode.strip().lower()
        if mode:
            if mode not in ALLOWED_COURT_AUTO_FETCH_MODES:
                raise HTTPException(
                    status_code=400,
                    detail=f"court_auto_fetch_mode must be one of {ALLOWED_COURT_AUTO_FETCH_MODES}",
                )
            fetch_mode = mode

    user = User(
        username=username,
        full_name=payload.full_name.strip(),
        hashed_password=hash_password(payload.password),
        system_role=payload.system_role,
        wecom_userid=payload.wecom_userid.strip() if payload.wecom_userid else None,
        notify_channel=ch,
        notify_target=payload.notify_target.strip() if payload.notify_target else None,
        phone_os=payload.phone_os,
        phone_last4=payload.phone_last4.strip()[:4] if payload.phone_last4 else None,
        id_card_last4=payload.id_card_last4.strip()[:4] if payload.id_card_last4 else None,
        court_auto_fetch_mode=fetch_mode,
        calendar_color=payload.calendar_color,
    )
    session.add(user)
    session.flush()
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        detail={"username": user.username, "system_role": user.system_role.value},
    )
    session.commit()
    session.refresh(user)
    return _to_summary(user)


@router.patch("/{user_id}", response_model=UserSummary)
def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    admin_user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> UserSummary:
    """Admin-only endpoint to update user role, activation, or WeCom binding."""
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.full_name is not None:
        user.full_name = payload.full_name.strip() or user.full_name
    if payload.system_role is not None:
        if user.id == admin_user.id and payload.system_role != SystemRole.ADMIN:
            raise HTTPException(status_code=400, detail="Cannot demote yourself from admin")
        user.system_role = payload.system_role
    if payload.wecom_userid is not None:
        user.wecom_userid = payload.wecom_userid.strip() or None
    if payload.notify_channel is not None:
        allowed_channels = ("wecom", "serverchan", "wxpusher", "pushplus", "webhook")
        ch = payload.notify_channel.strip().lower()
        if ch not in allowed_channels:
            raise HTTPException(status_code=400, detail=f"notify_channel must be one of {allowed_channels}")
        user.notify_channel = ch
    if payload.notify_target is not None:
        user.notify_target = payload.notify_target.strip() or None
    if payload.phone_os is not None:
        if payload.phone_os not in ("ios", "android"):
            raise HTTPException(status_code=400, detail="phone_os must be 'ios' or 'android'")
        user.phone_os = payload.phone_os
    if payload.phone_last4 is not None:
        user.phone_last4 = payload.phone_last4.strip()[:4] or None
    if payload.id_card_last4 is not None:
        user.id_card_last4 = payload.id_card_last4.strip()[:4] or None
    if payload.court_auto_fetch_mode is not None:
        mode = payload.court_auto_fetch_mode.strip().lower()
        if not mode:
            user.court_auto_fetch_mode = None
        elif mode not in ALLOWED_COURT_AUTO_FETCH_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"court_auto_fetch_mode must be one of {ALLOWED_COURT_AUTO_FETCH_MODES}",
            )
        else:
            user.court_auto_fetch_mode = mode
    if payload.calendar_color is not None:
        user.calendar_color = payload.calendar_color
    if payload.is_active is not None:
        if user.id == admin_user.id and not payload.is_active:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        user.is_active = payload.is_active
    if payload.new_password:
        user.hashed_password = hash_password(payload.new_password)

    session.add(user)
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action="user.update",
        entity_type="user",
        entity_id=user.id,
        detail=payload.model_dump(exclude_none=True, exclude={"new_password"}),
    )
    session.commit()
    session.refresh(user)
    return _to_summary(user)
