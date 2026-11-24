from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.config import ALLOWED_COURT_AUTO_FETCH_MODES
from app.core.constants import ALLOWED_NOTIFY_CHANNELS, AuditAction, clean_optional_str
from app.core.security import (
    get_current_user,
    hash_password,
    record_audit_log,
    require_admin,
)
from app.core.url_safety import UnsafeUrlError, validate_webhook_url
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

    ch = payload.notify_channel.strip().lower()
    if ch not in ALLOWED_NOTIFY_CHANNELS:
        raise HTTPException(status_code=400, detail=f"notify_channel must be one of {ALLOWED_NOTIFY_CHANNELS}")

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

    clean_target = clean_optional_str(payload.notify_target)
    if ch == "webhook" and clean_target:
        try:
            validate_webhook_url(clean_target)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    user = User(
        username=username,
        full_name=payload.full_name.strip(),
        hashed_password=hash_password(payload.password),
        system_role=payload.system_role,
        wecom_userid=clean_optional_str(payload.wecom_userid),
        notify_channel=ch,
        notify_target=clean_target,
        phone_os=payload.phone_os,
        phone_last4=clean_optional_str(payload.phone_last4, max_len=4),
        id_card_last4=clean_optional_str(payload.id_card_last4, max_len=4),
        court_auto_fetch_mode=fetch_mode,
        calendar_color=payload.calendar_color,
    )
    session.add(user)
    session.flush()
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action=AuditAction.USER_CREATE,
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

    updates = payload.model_dump(exclude_unset=True)

    if "full_name" in updates and updates["full_name"] is not None:
        user.full_name = clean_optional_str(updates["full_name"]) or user.full_name
    if "system_role" in updates and updates["system_role"] is not None:
        if user.id == admin_user.id and updates["system_role"] != SystemRole.ADMIN:
            raise HTTPException(status_code=400, detail="Cannot demote yourself from admin")
        user.system_role = updates["system_role"]
    if "wecom_userid" in updates:
        user.wecom_userid = clean_optional_str(updates["wecom_userid"])
    if "notify_channel" in updates and updates["notify_channel"] is not None:
        ch = str(updates["notify_channel"]).strip().lower()
        if ch not in ALLOWED_NOTIFY_CHANNELS:
            raise HTTPException(status_code=400, detail=f"notify_channel must be one of {ALLOWED_NOTIFY_CHANNELS}")
        user.notify_channel = ch
    if "notify_target" in updates:
        user.notify_target = clean_optional_str(updates["notify_target"])

    eff_channel = (user.notify_channel or "wecom").strip().lower()
    if eff_channel == "webhook" and user.notify_target:
        try:
            validate_webhook_url(user.notify_target)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if "phone_os" in updates and updates["phone_os"] is not None:
        if updates["phone_os"] not in ("ios", "android"):
            raise HTTPException(status_code=400, detail="phone_os must be 'ios' or 'android'")
        user.phone_os = updates["phone_os"]
    if "phone_last4" in updates:
        user.phone_last4 = clean_optional_str(updates["phone_last4"], max_len=4)
    if "id_card_last4" in updates:
        user.id_card_last4 = clean_optional_str(updates["id_card_last4"], max_len=4)
    if "court_auto_fetch_mode" in updates:
        raw_mode = updates["court_auto_fetch_mode"]
        mode = str(raw_mode).strip().lower() if raw_mode is not None else ""
        if not mode:
            user.court_auto_fetch_mode = None
        elif mode not in ALLOWED_COURT_AUTO_FETCH_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"court_auto_fetch_mode must be one of {ALLOWED_COURT_AUTO_FETCH_MODES}",
            )
        else:
            user.court_auto_fetch_mode = mode
    if "calendar_color" in updates and updates["calendar_color"] is not None:
        user.calendar_color = clean_optional_str(updates["calendar_color"]) or user.calendar_color
    if "is_active" in updates and updates["is_active"] is not None:
        if user.id == admin_user.id and not updates["is_active"]:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        user.is_active = bool(updates["is_active"])
    if payload.new_password:
        user.hashed_password = hash_password(payload.new_password)

    session.add(user)
    record_audit_log(
        session,
        actor_user_id=admin_user.id,
        action=AuditAction.USER_UPDATE,
        entity_type="user",
        entity_id=user.id,
        detail=payload.model_dump(exclude_none=True, exclude={"new_password"}),
    )
    session.commit()
    session.refresh(user)
    return _to_summary(user)
