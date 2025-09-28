# care_portal/services/password_reset.py
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, or_, func
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import User, PasswordReset
from ..auth import hash_password

RESET_TTL_MINUTES = 30

# >>> DEMO MODE: accept any code and skip token checks (offline)
DEMO_ALLOW_ANY_CODE = True   # set False if you later want real tokens

def _find_user_by_key(db, key: str) -> User | None:
    if not key:
        return None
    return db.scalar(
        select(User).where(
            or_(func.lower(User.email) == key.lower(),
                func.lower(User.full_name) == key.lower())
        )
    )

def create_reset_token_for_user(key: str) -> str:
    """
    Create a one-time token for the user identified by email/full_name.
    In DEMO mode, we still return a code (purely informational).
    """
    with SessionLocal() as db:
        user = _find_user_by_key(db, key)
        if not user:
            raise ValueError("No user found for that email or name.")
        token = secrets.token_urlsafe(24)
        now = datetime.utcnow()
        # Persist as usual so you can turn DEMO off later without code changes
        rec = PasswordReset(
            user_id=user.id,
            token=token,
            requested_at=now,
            expires_at=now + timedelta(minutes=RESET_TTL_MINUTES),
            used_at=None,
        )
        db.add(rec)
        db.commit()
        return token

def force_reset_password_for_user(key: str, new_password: str) -> None:
    """
    DEMO helper: directly set a new password for a user located by email/full_name.
    """
    with SessionLocal() as db:
        user = _find_user_by_key(db, key)
        if not user:
            raise ValueError("No user found for that email or name.")
        user.password_hash = hash_password(new_password)
        db.commit()

def apply_reset_with_token(token: str, new_password: str, *, user_key: str | None = None) -> None:
    """
    Validate token and reset password.
    In DEMO mode (DEMO_ALLOW_ANY_CODE=True), ANY token is accepted IF a user_key is provided.
    """
    if DEMO_ALLOW_ANY_CODE:
        if not user_key:
            raise ValueError("In offline demo mode, please provide your email or full name.")
        return force_reset_password_for_user(user_key, new_password)

    # --- Normal (non-demo) path ---
    with SessionLocal() as db:
        rec = db.scalar(select(PasswordReset).where(PasswordReset.token == token))
        if not rec:
            raise ValueError("Invalid reset code.")
        now = datetime.utcnow()
        if rec.used_at is not None:
            raise ValueError("This reset code has already been used.")
        if now > rec.expires_at:
            raise ValueError("This reset code has expired.")

        user = db.get(User, rec.user_id)
        if not user:
            raise RuntimeError("User not found for this reset code.")

        user.password_hash = hash_password(new_password)
        rec.used_at = now
        db.commit()
