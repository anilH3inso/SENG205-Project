# care_portal/services/checkin.py
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Union

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    StaffCheckin,
    StaffCheckinStatus,
    StaffCheckinMethod,
    Role,
)

# -------------------------------------------------------------------
# Enum / status helpers (tolerant across slightly different DBs)
# -------------------------------------------------------------------

def _status_value(v):
    """Return the underlying string value for Enum-like objects (or the object if already a str)."""
    return getattr(v, "value", v)

def _is_checkin(st) -> bool:
    """True if status represents a 'check-in' event."""
    sv = (_status_value(st) or "").replace("-", "_").lower()
    return sv in {"in", "checked_in", "checkin"}

def _is_checkout(st) -> bool:
    """True if status represents an end-of-shift/checkout event."""
    sv = (_status_value(st) or "").replace("-", "_").lower()
    # Support several spellings and tolerant terminal states
    return sv in {"out", "checked_out", "checkout", "checkedout", "auto", "skipped"}

def _checkout_enum_value():
    """Return a valid enum member for a 'checkout' event in this DB."""
    for nm in ("checked_out", "auto", "skipped"):
        try:
            return getattr(StaffCheckinStatus, nm)
        except Exception:
            continue
    return getattr(StaffCheckinStatus, "checked_in", "checked_in")

def _allowed_statuses():
    """
    Only statuses that exist in your DB enum (for WHERE filters).
    Include both IN and OUT events so worked-time pairing is possible.
    """
    vals = []
    for nm in ("checked_in", "checked_out", "auto", "skipped"):
        try:
            vals.append(getattr(StaffCheckinStatus, nm))
        except Exception:
            pass
    return vals or ["checked_in"]

# -------------------------------------------------------------------
# Normalizers (friendly inputs -> schema-safe values)
# -------------------------------------------------------------------

def _normalize_role(role: Optional[Union[Role, str]], role_value: Optional[str]) -> Union[Role, str, None]:
    """Accept Role enum, a raw string, or legacy role_value=..."""
    if role is not None:
        return role
    if role_value:
        try:
            return Role(role_value)
        except Exception:
            return role_value
    return None

def _normalize_method(method: Optional[Union[StaffCheckinMethod, str]]) -> Optional[Union[StaffCheckinMethod, str]]:
    """Map friendly/legacy method strings to the enum; tolerate raw strings if schema allows."""
    if method is None:
        try:
            return StaffCheckinMethod.login
        except Exception:
            return None
    if isinstance(method, str):
        nm = method.lower().strip()
        alias = {"web": "login"}
        nm = alias.get(nm, nm)
        for choice in ("login", "manual", "remote", "kiosk"):
            if nm == choice:
                try:
                    return getattr(StaffCheckinMethod, choice)
                except Exception:
                    return nm
        return nm
    return method

def _normalize_status(status: Optional[Union[StaffCheckinStatus, str]]) -> Union[StaffCheckinStatus, str]:
    """Map friendly checkout strings to a proper terminal enum; default to checked_in."""
    if status is None:
        try:
            return StaffCheckinStatus.checked_in
        except Exception:
            return "checked_in"
    sv = _status_value(status)
    if isinstance(sv, str) and sv.lower() in {"out", "checkout", "checked_out", "checkedout"}:
        return _checkout_enum_value()
    return status

# -------------------------------------------------------------------
# Date range helper (index-friendly filtering)
# -------------------------------------------------------------------

def _today_range():
    now = datetime.utcnow()
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day1 = day0 + timedelta(days=1)
    return day0, day1

# -------------------------------------------------------------------
# Public API (optimized, index-friendly)
# -------------------------------------------------------------------

def today_checkins() -> List[StaffCheckin]:
    """
    Return today's check-in/out events (with user eager-loaded) as a LIST.
    Uses index-friendly timestamp range filtering.
    """
    day0, day1 = _today_range()
    with SessionLocal() as db:
        rows = db.scalars(
            select(StaffCheckin)
            .options(selectinload(StaffCheckin.user))
            .where(
                StaffCheckin.ts >= day0,
                StaffCheckin.ts < day1,
                StaffCheckin.status.in_(_allowed_statuses()),
            )
            .order_by(StaffCheckin.ts.asc())
        ).all()
    return rows

def today_checkin_by_user(user_id: int) -> List[StaffCheckin]:
    """
    Return today's check-in/out events for this user as a LIST.
    Uses index-friendly timestamp range filtering.
    """
    day0, day1 = _today_range()
    with SessionLocal() as db:
        rows = db.scalars(
            select(StaffCheckin)
            .where(
                StaffCheckin.user_id == user_id,
                StaffCheckin.ts >= day0,
                StaffCheckin.ts < day1,
                StaffCheckin.status.in_(_allowed_statuses()),
            )
            .order_by(StaffCheckin.ts.asc())
        ).all()
    return rows

def record_checkin(
    user_id: int,
    *,
    role: Optional[Union[Role, str]] = None,
    role_value: Optional[str] = None,
    method: Optional[Union[StaffCheckinMethod, str]] = None,
    location: Optional[str] = "Onsite",
    status: Optional[Union[StaffCheckinStatus, str]] = None,
    note: Optional[str] = None,
) -> StaffCheckin:
    """Create a StaffCheckin row with normalized inputs."""
    role = _normalize_role(role, role_value)
    method = _normalize_method(method)
    status = _normalize_status(status)

    row = StaffCheckin(
        user_id=user_id,
        role=role,
        status=status,
        method=method,
        ts=datetime.utcnow(),
        location=location or "Onsite",
    )

    if note is not None:
        try:
            setattr(row, "note", note)
        except Exception:
            pass

    with SessionLocal() as db:
        db.add(row)
        db.commit()
        db.refresh(row)

    return row

def record_checkout(
    user_id: int,
    *,
    role: Optional[Union[Role, str]] = None,
    role_value: Optional[str] = None,
    method: Optional[Union[StaffCheckinMethod, str]] = None,
    location: Optional[str] = "Onsite",
    note: Optional[str] = None,
) -> StaffCheckin:
    """Convenience helper to record a checkout event using the best available terminal status."""
    return record_checkin(
        user_id,
        role=role,
        role_value=role_value,
        method=method,
        location=location,
        status=_checkout_enum_value(),
        note=note,
    )
