# care_portal/api/app_bot.py
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    User, Patient, Doctor, Appointment, AppointmentStatus,
    MedicalRecord, RecordAuthor, Billing, BillingStatus, PaymentMethod
)
from ..services.appointments import AppointmentService

app = FastAPI(title="Care Portal AI API")


# ---------- Pydantic I/O ----------
class ChatRequest(BaseModel):
    user_id: int
    session_id: str
    message: str
    context: Dict[str, Any] = {}
    allow_tools: bool = True


class ToolCall(BaseModel):
    name: str
    args: Dict[str, Any]


class ChatResponse(BaseModel):
    answer: str
    intent: str
    confidence: float = 0.9
    entities: Dict[str, Any] = {}
    tool_calls: List[ToolCall] = []
    tool_results: Optional[Dict[str, Any]] = None
    followup_needed: bool = False
    suggested_replies: List[str] = []


# ---------- tiny NLP helpers (regexy & simple) ----------
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")        # YYYY-MM-DD
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")   # HH:MM

def parse_date_words(text: str) -> Optional[str]:
    t = text.lower()
    today = datetime.now().date()
    if "today" in t:
        return today.strftime("%Y-%m-%d")
    if "tomorrow" in t:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    m = DATE_RE.search(text)
    if m: return m.group(1)
    return None

def parse_time(text: str) -> Optional[str]:
    m = TIME_RE.search(text)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2))
        return f"{hh:02d}:{mm:02d}"
    return None

def parse_doctor_name(text: str) -> Optional[str]:
    # look for "dr <name>" or a capitalized token after dr.
    m = re.search(r"\bdr\.?\s+([A-Za-z][A-Za-z\-']+)", text, re.IGNORECASE)
    if m: return m.group(1)
    # or a plain capitalized surname token
    m = re.search(r"\b([A-Z][a-z]{2,})\b", text)
    if m: return m.group(1)
    return None


# ---------- DB helpers ----------
def get_patient_by_user_id(db, user_id: int) -> Optional[Patient]:
    return db.scalar(select(Patient).where(Patient.user_id == user_id))

def find_doctor(db, name_like: Optional[str]) -> Optional[Doctor]:
    if not name_like:
        return None
    stmt = (
        select(Doctor)
        .options(selectinload(Doctor.user))
        .join(User, Doctor.user_id == User.id)
        .where(func.lower(User.full_name).like(f"%{name_like.lower()}%"))
        .limit(1)
    )
    return db.scalar(stmt)

def doctor_label(doc: Doctor) -> str:
    return f"Dr. {doc.user.full_name or doc.user.email} ({doc.specialty})"


# ---------- Intent router ----------
def detect_intent(q: str) -> str:
    t = q.lower()
    if any(w in t for w in ["slot", "available", "availability", "free time"]):
        return "doctor_availability"
    if "book" in t or "appointment" in t or "schedule" in t:
        return "book_appointment"
    if "cancel" in t:
        return "cancel_appointment"
    if "resched" in t or "change time" in t:
        return "reschedule_appointment"
    if any(w in t for w in ["prescription", "medicine", "rx"]):
        return "prescriptions_view"
    if "allerg" in t:
        return "allergies_update"
    if any(w in t for w in ["bill", "invoice", "payment", "pay"]):
        return "billing"
    if any(w in t for w in ["ticket", "support", "helpdesk"]):
        return "ticket"
    return "portal_help"


def suggest(intent: str) -> List[str]:
    return {
        "doctor_availability": ["Dr Demo tomorrow", "Dr Lee 2025-09-01"],
        "book_appointment": ["Book 10:00", "Show availability", "Cancel"],
        "billing": ["Show outstanding", "Show paid", "Pay invoice 1 by card"],
    }.get(intent, ["Help", "My appointments", "Billing"])


# ---------- Main endpoint ----------
@app.post("/ai/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    q = req.message.strip()
    intent = detect_intent(q)

    # Pre-extract common entities
    date_s = parse_date_words(q)
    time_s = parse_time(q)
    doc_name = parse_doctor_name(q)
    entities: Dict[str, Any] = {}

    with SessionLocal() as db:
        patient = get_patient_by_user_id(db, req.user_id)

        # Try to resolve doctor
        doctor: Optional[Doctor] = None
        if "doctor_id" in req.context and req.context["doctor_id"]:
            doctor = db.get(Doctor, int(req.context["doctor_id"]))
        if not doctor and doc_name:
            doctor = find_doctor(db, doc_name)
        if doctor:
            entities["doctor_id"] = doctor.id
            entities["doctor_name"] = doctor.user.full_name or doctor.user.email

        # ------- doctor_availability -------
        if intent == "doctor_availability":
            if not doctor:
                return ChatResponse(
                    answer="Which doctor should I check?",
                    intent=intent, entities=entities, followup_needed=True,
                    suggested_replies=suggest(intent)
                )
            if not date_s:
                return ChatResponse(
                    answer=f"For {doctor_label(doctor)}, which date?",
                    intent=intent, entities=entities, followup_needed=True,
                    suggested_replies=["today", "tomorrow", "2025-09-01"]
                )
            day = datetime.strptime(date_s, "%Y-%m-%d")
            slots = AppointmentService.get_available_slots(doctor.id, day)
            if not slots:
                return ChatResponse(
                    answer=f"No free slots for {doctor_label(doctor)} on {date_s}. Try another day.",
                    intent=intent, entities={**entities, "date": date_s},
                    followup_needed=True, suggested_replies=["Show tomorrow", "Next week"]
                )
            return ChatResponse(
                answer=f"{doctor_label(doctor)} on {date_s}: " + ", ".join(slots),
                intent=intent, entities={**entities, "date": date_s, "slots": slots},
                suggested_replies=[f"Book {slots[0]}", "Show other days"] if slots else []
            )

        # ------- book_appointment -------
        if intent == "book_appointment":
            if not patient:
                return ChatResponse(
                    answer="I couldn't find your patient profile. Please sign in as a patient.",
                    intent=intent, followup_needed=False
                )
            if not doctor:
                return ChatResponse(
                    answer="Which doctor would you like to book with?",
                    intent=intent, entities=entities, followup_needed=True
                )
            if not date_s:
                return ChatResponse(
                    answer="Which date would you like?",
                    intent=intent, entities=entities, followup_needed=True
                )
            if not time_s:
                return ChatResponse(
                    answer="What time would you like? (e.g., 14:00)",
                    intent=intent, entities={**entities, "date": date_s},
                    followup_needed=True
                )
            # Reason: try to pick a short trailing piece if present
            reason = ""
            m = re.search(r"(because|for|re:)\s+(.+)$", q, re.IGNORECASE)
            if m: reason = m.group(2)[:120]

            when = datetime.strptime(f"{date_s} {time_s}", "%Y-%m-%d %H:%M")
            try:
                ap = AppointmentService.book(patient.id, doctor.id, when, reason)
                return ChatResponse(
                    answer=f"Booked appointment #{ap.id} with {doctor_label(doctor)} at {when:%Y-%m-%d %H:%M}.",
                    intent=intent, entities={**entities, "datetime": when.strftime("%Y-%m-%d %H:%M")},
                    followup_needed=False
                )
            except ValueError:
                return ChatResponse(
                    answer="That time was just taken. Want me to list the available slots?",
                    intent=intent, entities=entities, followup_needed=True,
                    suggested_replies=[f"Slots for {date_s}", "Show tomorrow"]
                )

        # ------- billing (overview or ‘pay … by …’) -------
        if intent == "billing":
            if not patient:
                return ChatResponse(
                    answer="I couldn't find your patient profile for billing.",
                    intent=intent
                )
            # simple "pay <id> by <method>" parser
            pay_match = re.search(r"pay\s+(\d+)\s*(?:by|with)?\s*(cash|card|online)?", q, re.IGNORECASE)
            if pay_match:
                bill_id = int(pay_match.group(1))
                method = pay_match.group(2) or "cash"
                b = db.get(Billing, bill_id)
                if not b:
                    return ChatResponse(answer=f"Bill {bill_id} not found.", intent=intent)
                if b.status == BillingStatus.paid:
                    return ChatResponse(answer=f"Bill {bill_id} is already paid.", intent=intent)
                b.status = BillingStatus.paid
                b.payment_method = PaymentMethod(method.lower())
                b.paid_at = datetime.now()
                db.commit()
                return ChatResponse(answer=f"Marked bill {bill_id} as paid by {method}.", intent=intent)

            # otherwise list overview
            rows = db.scalars(
                select(Billing).join(Appointment).where(Appointment.patient_id == patient.id)
            ).all()
            if not rows:
                return ChatResponse(answer="You have no bills on file.", intent=intent)
            out_unpaid = [r for r in rows if r.status == BillingStatus.unpaid]
            total_unpaid = sum([float(r.amount) for r in out_unpaid], 0.0)
            return ChatResponse(
                answer=f"You have {len(out_unpaid)} unpaid bill(s), total ${total_unpaid:.2f}. "
                       f"Say 'pay <id> by card' to pay one.",
                intent=intent
            )

        # ------- allergies_update (just echo guidance; UI has button to edit) -------
        if intent == "allergies_update":
            return ChatResponse(
                answer="Go to the Medical Records tab, click 'Edit & Save' under Allergies, and enter the text.",
                intent=intent
            )

        # ------- ticket (simple create: “ticket: subject - body…”) -------
        if intent == "ticket":
            return ChatResponse(
                answer="Tell me a subject and a short description, e.g., 'ticket: Portal login - page freezes on submit'.",
                intent=intent
            )

        # ------- prescriptions / portal help -------
        if intent == "prescriptions_view":
            return ChatResponse(
                answer="Your prescriptions appear under the Medical Records tab (linked to appointments).",
                intent=intent
            )

        # default
        return ChatResponse(
            answer="I can help with availability, booking, billing, and records. Try: "
                   "'slots for Dr Demo tomorrow' or 'book 2025-09-01 10:00 with Dr Demo'.",
            intent=intent, suggested_replies=["Show my next appointment", "Billing overview"]
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
