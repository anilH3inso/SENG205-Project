# care_portal/services/notifications.py
from __future__ import annotations

from typing import Any, Iterable
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import (
    Notification,
    User,
    Role,
    Appointment,
    SupportTicket,
    TicketStatus,
)


# ---------------------------
# Helpers
# ---------------------------

def _role_value(obj: Any, name: str) -> Any:
    """Robustly fetch Role.<name>, supporting both Enum and plain string roles."""
    try:
        return getattr(Role, name)
    except Exception:
        return name


def _commit_and_refresh(db, obj):
    db.commit()
    try:
        db.refresh(obj)
    except Exception:
        pass
    return obj


# ---------------------------
# Low-level notification API
# ---------------------------

def send_user_notification(
    user_id: int,
    title: str,
    body: str = "",
    *,
    appointment_id: int | None = None,
    patient_id: int | None = None,
    from_user_id: int | None = None,
    db=None,
) -> Notification:
    """
    Create a Notification row for a specific user.
    Commits if we opened the session.
    Safe against schemas that do not include certain optional columns.
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        note = Notification(
            user_id=user_id,
            title=title,
            body=body,
        )

        # Attach optional fields only when present in the model.
        if appointment_id is not None and hasattr(note, "appointment_id"):
            note.appointment_id = appointment_id
        if patient_id is not None and hasattr(note, "patient_id"):
            note.patient_id = patient_id
        if from_user_id is not None and hasattr(note, "from_user_id"):
            note.from_user_id = from_user_id

        db.add(note)
        return _commit_and_refresh(db, note)
    finally:
        if close:
            db.close()


def send_bulk_notifications(
    user_ids: Iterable[int],
    title: str,
    body: str = "",
    *,
    db=None,
) -> int:
    """
    Create the same notification for many users. Returns count created.
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    created = 0
    try:
        for uid in set(u for u in user_ids if u):
            db.add(Notification(user_id=uid, title=title, body=body))
            created += 1
        if created:
            db.commit()
        return created
    finally:
        if close:
            db.close()


# ---------------------------
# Appointment-related helpers
# ---------------------------

def notify_receptionists_about_request(appointment_or_id, db=None) -> int:
    """
    Notify all users with Role.receptionist about a new appointment *request*.

    Accepts an Appointment instance OR an appointment id.
    Returns the number of notifications created.
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    try:
        # Resolve appointment
        if hasattr(appointment_or_id, "id"):
            ap = appointment_or_id
        else:
            ap = db.get(Appointment, int(appointment_or_id))

        if not ap:
            return 0

        # Find receptionists (support Enum or string role fields)
        receptionist_role = _role_value(Role, "receptionist")

        try:
            recip_ids = db.scalars(
                select(User.id).where(User.role == receptionist_role)
            ).all()
        except Exception:
            # Fallback: compare on lowercased string when role is stored as plain text
            recip_ids = db.scalars(
                select(User.id).where(func.lower(User.role) == "receptionist")  # type: ignore[attr-defined]
            ).all()

        # Build a readable body even if doctor_id is missing/0
        doc_label = f"Doctor #{getattr(ap, 'doctor_id', 0) or 'unassigned'}"
        body = (
            f"Patient #{getattr(ap, 'patient_id', '?')} requested "
            f"{getattr(ap, 'scheduled_for', ''):%Y-%m-%d %H:%M} with {doc_label}."
            if getattr(ap, "scheduled_for", None) else
            f"Patient #{getattr(ap, 'patient_id', '?')} submitted an appointment request."
        )

        made = send_bulk_notifications(recip_ids, "New appointment request", body, db=db)
        return made
    finally:
        if close:
            db.close()


# ---------------------------
# Support ticket notifications
# ---------------------------

def notify_ticket_created(ticket_id: int, *, db=None) -> int:
    """
    Notify Support team (or assignee if already set) that a new ticket was created.
    Returns the number of notifications created.
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    try:
        t = db.get(SupportTicket, ticket_id)
        if not t:
            return 0

        created_by = db.get(User, t.user_id) if t.user_id else None
        who = (created_by.full_name or created_by.email) if created_by else f"User#{t.user_id}"
        title = "New support ticket"
        body = f"{who} created ticket #{t.id}: {t.subject}"

        # If already assigned → notify assignee only; else notify all Support users
        if t.assignee_id:
            send_user_notification(t.assignee_id, title, body, db=db)
            return 1

        # Find all support-role users
        support_role = _role_value(Role, "support")
        try:
            support_ids = db.scalars(select(User.id).where(User.role == support_role)).all()
        except Exception:
            support_ids = db.scalars(
                select(User.id).where(func.lower(User.role) == "support")  # type: ignore[attr-defined]
            ).all()

        return send_bulk_notifications(support_ids, title, body, db=db)
    finally:
        if close:
            db.close()


def notify_ticket_reply_to_user(ticket_id: int, *, reply_author_id: int | None = None, db=None) -> int:
    """
    Notify the ticket owner that there is a new note/reply on their ticket.
    Returns the number of notifications created (0 or 1).
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    try:
        t = db.get(SupportTicket, ticket_id)
        if not t or not t.user_id:
            return 0

        author_name = None
        if reply_author_id:
            au = db.get(User, reply_author_id)
            if au:
                author_name = au.full_name or au.email

        title = "New reply on your support ticket"
        body = f"Your ticket #{t.id} received a new reply"
        if author_name:
            body += f" from {author_name}"
        body += "."

        send_user_notification(t.user_id, title, body, db=db)
        return 1
    finally:
        if close:
            db.close()


def notify_ticket_status_update(
    ticket_id: int,
    new_status: TicketStatus | str,
    *,
    updater_user_id: int | None = None,
    db=None,
) -> int:
    """
    Notify the ticket owner when status changes.
    Returns the number of notifications created (0 or 1).
    """
    close = False
    if db is None:
        db = SessionLocal()
        close = True

    try:
        t = db.get(SupportTicket, ticket_id)
        if not t or not t.user_id:
            return 0

        # Normalize new status to string for message
        try:
            status_text = new_status.value if isinstance(new_status, TicketStatus) else str(new_status)
        except Exception:
            status_text = str(new_status)

        updater = db.get(User, updater_user_id) if updater_user_id else None
        who = (updater.full_name or updater.email) if updater else None

        title = "Your support ticket was updated"
        body = f"Ticket #{t.id} status changed to '{status_text}'."
        if who:
            body += f" Updated by {who}."

        send_user_notification(t.user_id, title, body, db=db)
        return 1
    finally:
        if close:
            db.close()
