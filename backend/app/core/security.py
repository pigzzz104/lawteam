from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from app.config import get_settings
from app.database import get_session
from app.models.entities import (
    AuditLog,
    Case,
    CaseMember,
    CaseMemberRole,
    CaseVisibility,
    InboxTriageItem,
    SystemRole,
    User,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    user_id: int,
    username: str,
    system_role: SystemRole,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token."""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": system_role.value,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token."""
    settings = get_settings()
    try:
        return jwt.decode(
            token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def create_triage_action_token(
    *,
    user_id: int,
    triage_id: int,
    action: str,
    case_id: Optional[int] = None,
    action_epoch: int = 0,
    expires_hours: int = 72,
) -> str:
    """Create a signed JWT token for one-tap WeChat/WeCom triage confirmation or ignore."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "scope": "wecom_triage_action",
        "sub": str(user_id),
        "triage_id": triage_id,
        "action": action,
        "action_epoch": action_epoch,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
    }
    if case_id is not None:
        payload["case_id"] = case_id
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_triage_action_token(token: str) -> dict[str, Any]:
    """Decode and validate a signed WeChat/WeCom triage quick-action token."""
    payload = decode_access_token(token)
    if payload.get("scope") != "wecom_triage_action":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid triage action token scope",
        )
    return payload


def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Resolve the currently authenticated active user from Bearer JWT."""
    payload = decode_access_token(token)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token payload",
        )
    try:
        user_id = int(sub)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid subject identifier",
        ) from exc

    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Require SystemRole.ADMIN for administrative endpoints."""
    if current_user.system_role != SystemRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


def get_accessible_case_ids_filter(user: User, session: Session) -> set[int]:
    """Return all Case IDs that a user is allowed to view."""
    if user.system_role == SystemRole.ADMIN:
        rows = session.exec(
            select(Case.id).where(
                (Case.visibility == CaseVisibility.TEAM) | (Case.owner_id == user.id)
            )
        ).all()
        return {cid for cid in rows if cid is not None}

    owned_ids = set(
        cid for cid in session.exec(select(Case.id).where(Case.owner_id == user.id)).all() if cid
    )
    member_case_ids = set(
        cid
        for cid in session.exec(
            select(CaseMember.case_id)
            .join(Case, Case.id == CaseMember.case_id)
            .where(CaseMember.user_id == user.id, Case.visibility == CaseVisibility.TEAM)
        ).all()
        if cid
    )
    return owned_ids | member_case_ids


def check_case_access(
    case: Optional[Case],
    user: User,
    session: Session,
    require_manage: bool = False,
) -> Case:
    """
    Enforce CaseVisibility & CaseMember RBAC:
    - PRIVATE: strictly visible & editable only by owner_id (even ADMIN cannot view others' PRIVATE cases).
    - TEAM: visible to ADMIN, owner_id, and CaseMember rows.
    - require_manage: additionally restricts destructive/admin case edits to owner_id, ADMIN, or LEAD member.
    """
    if case is None or case.id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    if case.visibility == CaseVisibility.PRIVATE:
        if case.owner_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied for private case",
            )
        return case

    # TEAM visibility
    if user.system_role == SystemRole.ADMIN or case.owner_id == user.id:
        return case

    member = session.exec(
        select(CaseMember).where(
            CaseMember.case_id == case.id,
            CaseMember.user_id == user.id,
        )
    ).first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: not a member of this case",
        )

    if require_manage and member.role_in_case != CaseMemberRole.LEAD:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lead lawyer or admin privileges required for this case action",
        )

    return case


def check_triage_access(
    session: Session,
    user: User,
    item: Optional[InboxTriageItem],
) -> InboxTriageItem:
    """Enforce RBAC and strict PRIVATE case isolation on an InboxTriageItem."""
    if item is None or item.id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Triage item not found")

    if item.matched_case_id is not None:
        matched_case = session.get(Case, item.matched_case_id)
        if matched_case is not None and matched_case.visibility == CaseVisibility.PRIVATE:
            if matched_case.owner_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied for private case triage item",
                )

    if user.system_role == SystemRole.ADMIN or item.owner_user_id == user.id:
        return item
    if item.matched_case_id is not None:
        accessible_ids = get_accessible_case_ids_filter(user, session)
        if item.matched_case_id in accessible_ids:
            return item
    if (
        item.item_type == "invoice_archive"
        and item.matched_case_id is None
        and user.system_role == SystemRole.ASSISTANT
    ):
        return item
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied to this triage item",
    )


def filter_visible_triage_items(
    session: Session,
    user: User,
    items: list[InboxTriageItem],
) -> list[InboxTriageItem]:
    """Filter triage items respecting strict PRIVATE isolation even for ADMIN."""
    if not items:
        return []
    case_ids = {it.matched_case_id for it in items if it.matched_case_id is not None}
    private_other_case_ids: set[int] = set()
    if case_ids:
        cases = session.exec(select(Case).where(Case.id.in_(case_ids))).all()  # type: ignore[union-attr]
        for c in cases:
            if c.id is not None and c.visibility == CaseVisibility.PRIVATE and c.owner_id != user.id:
                private_other_case_ids.add(c.id)

    if user.system_role == SystemRole.ADMIN:
        return [it for it in items if it.matched_case_id not in private_other_case_ids]

    accessible_ids = get_accessible_case_ids_filter(user, session)
    visible: list[InboxTriageItem] = []
    for it in items:
        if it.matched_case_id in private_other_case_ids:
            continue
        if it.owner_user_id == user.id:
            visible.append(it)
        elif it.matched_case_id is not None and it.matched_case_id in accessible_ids:
            visible.append(it)
        elif (
            it.item_type == "invoice_archive"
            and it.matched_case_id is None
            and user.system_role == SystemRole.ASSISTANT
        ):
            visible.append(it)
    return visible


def record_audit_log(
    session: Session,
    *,
    actor_user_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    case_id: Optional[int] = None,
    detail: Optional[dict[str, Any]] = None,
) -> AuditLog:
    """Append an immutable audit log entry inside the active DB transaction."""
    entry = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        detail=detail or {},
    )
    session.add(entry)
    return entry
