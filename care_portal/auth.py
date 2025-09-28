# care_portal/auth.py
from __future__ import annotations

import os
import base64
import hmac
import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import User, Role

# InviteCode is optional — the app works without it.
try:
    from .models import InviteCode  # type: ignore
except Exception:  # pragma: no cover
    InviteCode = None  # type: ignore

# ---------- Password hashing ----------
_ITER = 100_000


def hash_password(password: str) -> str:
    """Return salted PBKDF2-HMAC(SHA256) hash encoded as base64(salt+key)."""
    if not password:
        raise ValueError("Empty passwords are not allowed.")
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return base64.b64encode(salt + key).decode("ascii")


def verify_password(password: str, secret: str) -> bool:
    """Constant-time verify of plaintext vs stored base64(salt+key)."""
    try:
        raw = base64.b64decode(secret.encode("ascii"))
        salt, stored = raw[:16], raw[16:]
        new = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
        return hmac.compare_digest(new, stored)
    except Exception:
        return False


# ---------- Role helpers ----------
STAFF_ROLES = {"doctor", "receptionist", "admin", "pharmacist", "support", "finance"}


def _role_text_to_value(role_txt: str):
    """Map user-entered role to Role enum (if enum) or pass-through string."""
    role_txt = (role_txt or "patient").lower()
    try:
        if hasattr(Role, "__members__"):
            mapping = {k.lower(): v for k, v in Role.__members__.items()}
            return mapping.get(role_txt, list(Role)[0])
        return Role(role_txt)  # type: ignore[arg-type]
    except Exception:
        return role_txt


# ---------- DB helpers ----------
def _get_user_by_email(db: Session, email: str) -> Optional[User]:
    email = (email or "").strip().lower()
    if not email:
        return None
    return db.scalar(select(User).where(func.lower(User.email) == email))


def _get_user_by_key(db: Session, key: str) -> Optional[User]:
    """
    Look up by email OR full_name (case-insensitive).
    """
    key = (key or "").strip().lower()
    if not key:
        return None
    return db.scalar(
        select(User).where(
            or_(
                func.lower(User.email) == key,
                func.lower(User.full_name) == key,
            )
        )
    )


# ---------- Authentication ----------
def authenticate_user(key: str, password: str) -> Optional[User]:
    """
    Authenticate by email OR full name + password.
    Returns a User or None (no exceptions for invalid creds).
    """
    key = (key or "").strip()
    if not key or not password:
        return None

    with SessionLocal() as db:
        user = _get_user_by_key(db, key)
        if not user:
            return None
        secret = getattr(user, "password_hash", "") or ""
        if not secret:
            return None
        if not verify_password(password, secret):
            return None
        return user


# ---------- Invitation helpers (optional) ----------
def _find_valid_invite(db: Session, code: str):
    if InviteCode is None:
        # No invite table: treat as no invite found (UI/logic will allow patients, require for staff)
        return None
    code = (code or "").strip()
    if not code:
        return None
    inv = db.scalar(select(InviteCode).where(InviteCode.code == code))
    if not inv:
        return None

    # Optional-safe checks
    if getattr(inv, "disabled", False):
        return None
    if getattr(inv, "used_by", None) is not None:
        return None
    exp = getattr(inv, "expires_at", None)
    if exp and exp < datetime.utcnow():
        return None
    return inv


def _consume_invite(db: Session, invite, user_id: int):
    if InviteCode is None or invite is None:
        return
    # optional-safe writes
    setattr(invite, "used_by", user_id)
    setattr(invite, "used_at", datetime.utcnow())
    db.add(invite)


# ---------- Registration (invite-aware) ----------
def register_user(
    *,
    db: Optional[Session] = None,
    email: str,
    password: str,
    full_name: str,
    phone: Optional[str],
    role_value: str,            # ex: "patient", "admin"
    invite_code: Optional[str] = None,
) -> User:
    """
    Create a user. Patients do not require invites.
    Staff roles require a valid invite if the InviteCode table exists.
    When `db` is not supplied, this function manages its own session/commit.
    """
    needs_close = False
    if db is None:
        db = SessionLocal()
        needs_close = True

    role_txt = (role_value or "patient").lower()
    email = (email or "").strip().lower()
    full_name = (full_name or "").strip()
    phone = (phone or "").strip() if phone else None

    try:
        if not email:
            raise ValueError("Email is required.")
        if not password:
            raise ValueError("Password is required.")

        if _get_user_by_email(db, email):
            raise ValueError("Email is already registered.")

        invite = None
        if role_txt in STAFF_ROLES and InviteCode is not None:
            invite = _find_valid_invite(db, invite_code or "")
            if not invite:
                raise ValueError("A valid invitation code is required for this role.")
            # Optional field: role_allowed
            allowed = (getattr(invite, "role_allowed", "") or "").lower()
            if allowed and allowed != role_txt:
                raise ValueError(f"Invitation is for role '{allowed}', not '{role_txt}'.")

        user = User(
            full_name=full_name,
            email=email,
            phone=phone or "",
            role=_role_text_to_value(role_txt),
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()  # ensure user.id

        if invite is not None:
            _consume_invite(db, invite, user.id)

        db.commit()
        return user
    except Exception:
        db.rollback()
        raise
    finally:
        if needs_close:
            db.close()
