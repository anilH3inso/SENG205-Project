# care_portal/services/ai_server.py
from __future__ import annotations

import os, re, json, time, logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

# ------------------------- Config -------------------------
HOST = os.getenv("CARE_PARTNER_HOST") or os.getenv("CARE_PORTAL_HOST", "127.0.0.1")
PORT = int(os.getenv("CARE_PORTAL_PORT", "8001"))
ROOT = os.getenv("CARE_PORTAL_ROOT", os.getcwd())
USE_LLM = bool(int(os.getenv("CARE_PORTAL_USE_LLM", "1")))
LLM_MODEL_PATH = os.getenv("CARE_PORTAL_LLM_PATH", os.path.join(ROOT, "care_portal", "models", "tinyllama.gguf"))
SECRET_KEY = os.getenv("CARE_PORTAL_SECRET_KEY", "change_this_secret")
ENV = os.getenv("ENV", "dev")

# ------------------------- Logging ------------------------
logging.basicConfig(
    level=logging.INFO if ENV != "dev" else logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("care-portal-ai")

# ------------------------- DB & Models --------------------
from care_portal.db import SessionLocal
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, or_

from care_portal.models import (
    User, Role, Patient, Doctor,
    Appointment, AppointmentStatus,
    Notification, Billing, BillingStatus, PaymentMethod,
    Prescription
)

from care_portal.auth import authenticate_user, verify_password, hash_password

# Optional Appointment service for live availability
try:
    from care_portal.services.appointments import AppointmentService
    HAS_APPT_SERVICE = True
except Exception:
    HAS_APPT_SERVICE = False

# ------------------------- Security -----------------------
from jose import JWTError, jwt
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 180
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/ai/token")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": int(expire.timestamp())})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_err = HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid: int = payload.get("sub")
        if uid is None:
            raise cred_err
    except JWTError:
        raise cred_err
    user = db.get(User, uid)
    if not user:
        raise cred_err
    return user


def require_role(user: User, allowed: List[Role]):
    if user.role not in allowed:
        raise HTTPException(403, f"Only {', '.join(r.value for r in allowed)} allowed")


def current_patient(db: Session, user: User) -> Patient:
    if user.role != Role.patient:
        raise HTTPException(403, "Only patients allowed")
    pat = db.scalar(select(Patient).where(Patient.user_id == user.id))
    if not pat:
        raise HTTPException(404, "Patient profile not found")
    return pat


# ------------------------- LLM (TinyLlama) ----------------
_llm = None
try:
    from llama_cpp import Llama
    HAS_LLAMA = True
except Exception:
    HAS_LLAMA = False


def ensure_llm() -> Optional["Llama"]:
    global _llm
    if not (USE_LLM and HAS_LLAMA):
        return None
    if _llm is None:
        if not (LLM_MODEL_PATH and os.path.exists(LLM_MODEL_PATH)):
            log.warning("LLM model path not found; LLM disabled")
            return None
        log.info(f"Loading TinyLlama model from {LLM_MODEL_PATH} ...")
        _llm = Llama(model_path=LLM_MODEL_PATH, n_ctx=2048, n_threads=max(2, (os.cpu_count() or 4) // 2))
    return _llm


SYSTEM_PROMPT = (
    "You are Care Portal Assistant for a hospital desktop app. "
    "Answer briefly and clearly in plain text (no markdown tables). "
    "Stay within Care Portal topics: appointments & scheduling; doctors/providers; "
    "patients/accounts; prescriptions; billing/payments; notifications; roles/staff/support; "
    "records/results; login/security. For actions, prefer the app’s commands when you can.\n"
)


def llm_complete(prompt: str, max_tokens: int = 256) -> str:
    llm = ensure_llm()
    if not llm:
        return "LLM not available."
    out = llm.create_completion(
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=0.2,
        top_p=0.9,
        stop=["\nUser:", "\nAssistant:", "\nSystem:"]
    )
    return (out["choices"][0]["text"] or "").strip()


# ------------------------- App init -----------------------
app = FastAPI(title="Care Portal AI Chatbot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if ENV != "prod" else ["http://localhost", "https://your.domain"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------- Schemas ------------------------
class ChatIn(BaseModel):
    message: str
    context: Dict[str, Any] = {}
    allow_tools: bool = True


class ChatOut(BaseModel):
    answer: str
    metadata: Dict[str, Any] = {}


# ------------------------- Utils --------------------------
def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'(?im)^(?:rule|question|answer|assistant|system|user)\s*:\s*.*$', '', text)
    text = " ".join([ln.strip() for ln in text.splitlines() if ln.strip()])
    return re.sub(r"\s{2,}", " ", text).strip()


def is_greeting(msg: str) -> bool:
    m = msg.strip().lower()
    return m in {"hi", "hello", "hey", "yo"} or m.startswith(("hi ", "hello ", "hey "))


def extract_datetime(msg: str) -> Optional[datetime]:
    """
    Handles common formats:
    - 2025-10-09 14:30
    - 9 Oct 2025 2pm / Oct 9 2pm / Oct 9 at 14:30
    - today/tomorrow + times
    Defaults to local server time.
    """
    now = datetime.now()
    msgl = msg.lower()

    # ISO full
    m = re.search(r"(20\d{2}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", msgl)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        except Exception:
            pass

    # ISO date only with 'morning/afternoon'
    m = re.search(r"(20\d{2}-\d{2}-\d{2})\s*(morning|afternoon|evening|noon|night)?", msgl)
    if m:
        base = datetime.strptime(m.group(1), "%Y-%m-%d")
        hint = m.group(2) or ""
        hour = 9 if "morning" in hint else 14 if "afternoon" in hint else 10
        return base.replace(hour=hour, minute=0)

    # Named months
    months = "|".join(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"])
    m = re.search(fr"(\d{{1,2}})\s*({months})[a-z]*\s*(\d{{4}})?(?:\s*(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)?)?", msgl)
    if m:
        day = int(m.group(1))
        mon_s = m.group(2)[:3]
        year = int(m.group(3) or now.year)
        hour = int(m.group(4) or 10)
        minute = int(m.group(5) or 0)
        ampm = (m.group(6) or "").lower()
        mon_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
        if ampm == "pm" and hour < 12: hour += 12
        if ampm == "am" and hour == 12: hour = 0
        try:
            return datetime(year, mon_map[mon_s], day, hour, minute)
        except Exception:
            pass

    # today / tomorrow quick picks
    if "today" in msgl:
        hhmm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", msgl)
        hour, minute = 10, 0
        if hhmm:
            hour = int(hhmm.group(1)); minute = int(hhmm.group(2) or 0)
            ampm = (hhmm.group(3) or "").lower()
            if ampm == "pm" and hour < 12: hour += 12
            if ampm == "am" and hour == 12: hour = 0
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if "tomorrow" in msgl or "tmrw" in msgl or "tmr" in msgl:
        base = now + timedelta(days=1)
        hhmm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", msgl)
        hour, minute = 10, 0
        if hhmm:
            hour = int(hhmm.group(1)); minute = int(hhmm.group(2) or 0)
            ampm = (hhmm.group(3) or "").lower()
            if ampm == "pm" and hour < 12: hour += 12
            if ampm == "am" and hour == 12: hour = 0
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    return None


def resolve_doctor_by_name(db: Session, name_or_spec: str) -> List[Tuple[int, str, str]]:
    q = name_or_spec.strip().lower()
    docs = db.execute(
        select(Doctor.id, Doctor.specialty, User.full_name, User.email)
        .join(User, Doctor.user_id == User.id, isouter=True)
        .order_by(Doctor.id.asc())
    ).all()
    out = []
    for did, spec, full, email in docs:
        nm = (full or email or f"Doctor#{did}")
        if q in nm.lower() or q in (spec or "").lower():
            out.append((did, nm, spec or "General"))
    return out


def patient_id_for_user(db: Session, user: User) -> Optional[int]:
    pat = db.scalar(select(Patient.id).where(Patient.user_id == user.id))
    return int(pat) if pat else None


def doctor_label(db: Session, did: int) -> str:
    d = db.get(Doctor, did)
    if not d:
        return f"Doctor#{did}"
    u = db.get(User, d.user_id) if d.user_id else None
    spec = d.specialty or "General"
    name = (u.full_name or u.email) if u else f"Doctor {did}"
    return f"Dr. {name} ({spec})"


# ------------------------- Intent Handlers ----------------
def handle_identity(db: Session, user: User, msg: str) -> Optional[str]:
    m = msg.lower()
    if "who am i" in m or "who am i logged in as" in m or "what is my patient name" in m:
        if user.role == Role.patient:
            return user.full_name or user.email
        return f"You are logged in as {user.role.value}: {user.full_name or user.email}"
    if "which doctor account" in m or "which doctor" in m:
        if user.role == Role.doctor:
            return user.full_name or user.email
        return "You are not logged in as a doctor."
    if "am i logged in" in m:
        return "Yes, you are logged in."
    if "what is my role" in m or "my role right now" in m:
        return user.role.value
    if "patient id" in m or "tell me my patient id" in m:
        pid = patient_id_for_user(db, user)
        return str(pid) if pid else "No patient profile linked."
    return None


def handle_list_doctors(db: Session, msg: str) -> Optional[str]:
    if not re.search(r"\b(list|show)\b.*\bdoctors?\b", msg, re.I):
        return None
    rows = db.execute(
        select(Doctor.id, User.full_name, User.email, Doctor.specialty)
        .join(User, Doctor.user_id == User.id, isouter=True)
        .order_by(Doctor.id.asc())
    ).all()
    if not rows:
        return "No doctors found."
    body = []
    for did, full, email, spec in rows:
        name = full or email or f"Doctor#{did}"
        body.append(f"{did} | {name} | {spec or 'General'}")
    return "ID | Name | Specialty\n" + "\n".join(body)


def handle_availability(db: Session, msg: str) -> Optional[str]:
    if not re.search(r"\b(is|check|what|slots|availability)\b.*\b(dr|doctor)\b", msg, re.I):
        return None
    dt = extract_datetime(msg) or datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    # resolve doctor by name
    name_match = re.search(r"(dr\.?\s*[a-z][a-z\s'.-]+|doctor\s+[a-z][a-z\s'.-]+)", msg, re.I)
    if not name_match:
        return "Tell me the doctor’s name and date (e.g., 'Is Dr. Derek free on 9 Oct at 2pm?')."
    needle = name_match.group(0).replace("dr.", "").replace("doctor", "").strip()
    matches = resolve_doctor_by_name(db, needle)
    if not matches:
        return f"No doctor matched '{needle}'."
    did, nm, spec = matches[0]
    if not HAS_APPT_SERVICE:
        return f"{nm} availability requires AppointmentService. If enabled, ask again."
    slots = AppointmentService.get_available_slots(did, dt.date())
    if not slots:
        return f"No open slots for {nm} on {dt.date()}."
    return f"Open slots for {nm} on {dt.date()}: " + ", ".join(slots[:12])


def handle_booking(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(book|make|schedule|set)\b.*\b(appointment|appt)\b", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can book from chat."
    pat_id = patient_id_for_user(db, user)
    if not pat_id:
        return "No patient profile linked."

    dt = extract_datetime(msg)
    if not dt:
        return "I need a date/time (e.g., 'Book Dr George on Oct 1 at 10am for checkup')."
    # doctor by explicit ID
    m_doc_id = re.search(r"doctor\s*(\d+)", msg, re.I)
    did: Optional[int] = int(m_doc_id.group(1)) if m_doc_id else None
    if not did:
        # resolve by name or specialty
        name_match = re.search(r"(dr\.?\s*[a-z][a-z\s'.-]+|doctor\s+[a-z][a-z\s'.-]+|cardio|derma|surgery|pediatrics|orth|trauma)", msg, re.I)
        if not name_match:
            return "Tell me the doctor’s name or specialty."
        needle = name_match.group(0).replace("dr.", "").replace("doctor", "").strip()
        matches = resolve_doctor_by_name(db, needle)
        if not matches:
            return f"No doctor matched '{needle}'."
        did = matches[0][0]

    reason_match = re.search(r"(for|about|reason[:\-])\s*(.+)$", msg, re.I)
    reason = reason_match.group(2).strip() if reason_match else "Checkup"

    if HAS_APPT_SERVICE:
        try:
            ap = AppointmentService.book(pat_id, did, dt, reason)
            return f"Booked appointment #{ap.id} with {doctor_label(db, did)} at {dt:%Y-%m-%d %H:%M}."
        except Exception as e:
            return f"Booking failed: {e}"

    ap = Appointment(patient_id=pat_id, doctor_id=did, scheduled_for=dt, reason=reason, status=AppointmentStatus.booked)
    db.add(ap); db.commit(); db.refresh(ap)
    return f"Booked appointment #{ap.id} with {doctor_label(db, did)} at {dt:%Y-%m-%d %H:%M}."


def handle_cancel(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(cancel|delete)\b.*\b(appointment|appt)\b", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can cancel from chat."
    m = re.search(r"(?:appointment|appt)\s*(\d+)", msg, re.I)
    if not m:
        return "Provide the appointment ID (e.g., 'cancel appointment 91')."
    appt_id = int(m.group(1))
    ap = db.get(Appointment, appt_id)
    if not ap:
        return f"Appointment #{appt_id} not found."
    ap.status = AppointmentStatus.cancelled
    db.commit()
    return f"Appointment #{appt_id} cancelled."


def handle_reschedule(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(reschedule|move|change)\b.*\b(appointment|appt)\b", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can reschedule from chat."
    m = re.search(r"(?:appointment|appt)\s*(\d+)", msg, re.I)
    if not m:
        return "Provide the appointment ID and new time (e.g., 'reschedule appointment 12 to 2025-10-02 14:30')."
    appt_id = int(m.group(1))
    ap = db.get(Appointment, appt_id)
    if not ap:
        return f"Appointment #{appt_id} not found."
    new_dt = extract_datetime(msg)
    if not new_dt:
        return "I need the new date/time."
    ap.scheduled_for = new_dt
    ap.status = AppointmentStatus.booked
    db.commit()
    return f"Appointment #{appt_id} moved to {new_dt:%Y-%m-%d %H:%M}."


def handle_status(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(status|show|list|what(?:'| i)?s the status)\b.*\b(appointment|appt|booking)", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can view their appointment status here."
    pid = patient_id_for_user(db, user)
    if not pid:
        return "No patient profile linked."
    # doctor filter?
    doc_name = None
    m = re.search(r"(with\s+(dr\.?|doctor)\s+[a-z][a-z\s'.-]+)", msg, re.I)
    if m:
        doc_name = m.group(0).replace("with", "").strip()

    q = select(Appointment).where(Appointment.patient_id == pid).order_by(Appointment.scheduled_for.desc())
    appts = list(db.scalars(q).all())
    if not appts:
        return "You have no appointments."
    lines = []
    for a in appts[:10]:
        dl = doctor_label(db, a.doctor_id)
        if doc_name and doc_name[doc_name.lower().find("dr"):].replace("doctor","").replace("dr.","").strip().lower() not in dl.lower():
            continue
        lines.append(f"{a.id} | {a.scheduled_for:%Y-%m-%d %H:%M} | {dl} | {a.reason or ''} | {getattr(a.status, 'value', a.status)}")
    if not lines:
        return "No matching appointments."
    return "ID | When | Doctor | Reason | Status\n" + "\n".join(lines)


def handle_upcoming(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(today|tomorrow|this week|upcoming|next)\b.*\b(appointment|appt|booking)s?\b", msg, re.I) and \
       not re.search(r"\bdo I have any appointments today\b", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can view their schedule here."
    pid = patient_id_for_user(db, user)
    if not pid:
        return "No patient profile linked."

    now = datetime.now()
    end = now + timedelta(days=7) if "week" in msg.lower() or "upcoming" in msg.lower() else now.replace(hour=23, minute=59)
    q = select(Appointment).where(and_(Appointment.patient_id == pid, Appointment.scheduled_for >= now, Appointment.scheduled_for <= end)).order_by(Appointment.scheduled_for.asc())
    appts = list(db.scalars(q).all())
    if not appts:
        return "No upcoming appointments."
    lines = [f"{a.id} | {a.scheduled_for:%Y-%m-%d %H:%M} | {doctor_label(db, a.doctor_id)} | {a.reason or ''}" for a in appts[:10]]
    return "ID | When | Doctor | Reason\n" + "\n".join(lines)


def handle_notifications(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(notifications?|alerts?|reminders?|messages)\b", msg, re.I):
        return None
    notes = db.execute(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc())
    ).scalars().all()
    if not notes:
        return "No notifications."
    lines = [f"{n.id} | {n.created_at:%Y-%m-%d %H:%M} | {n.title} | {'read' if n.read else 'unread'}" for n in notes[:10]]
    return "ID | Time | Title | Read\n" + "\n".join(lines)


def handle_billing(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(billing|bills|invoices|payments?)\b", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can view billing here."
    pid = patient_id_for_user(db, user)
    if not pid:
        return "No patient profile linked."
    rows = db.execute(
        select(Billing, Appointment).join(Appointment, Billing.appointment_id == Appointment.id).where(Appointment.patient_id == pid).order_by(Billing.id.desc())
    ).all()
    if not rows:
        return "You have no bills."
    lines = []
    for b, ap in rows[:10]:
        status = getattr(b.status, "value", b.status)
        paid = b.paid_at.strftime("%Y-%m-%d %H:%M") if b.paid_at else ""
        lines.append(f"{b.id} | {b.description or ''} | {float(b.amount or 0):.2f} | {status} | {paid}")
    return "ID | Description | Amount | Status | Paid At\n" + "\n".join(lines)


def handle_prescriptions(db: Session, user: User, msg: str) -> Optional[str]:
    if not re.search(r"\b(prescriptions?|rx)\b", msg, re.I):
        return None
    if user.role != Role.patient:
        return "Only patients can view prescriptions here."
    pid = patient_id_for_user(db, user)
    if not pid:
        return "No patient profile linked."
    rx_list = db.execute(
        select(Prescription).where(Prescription.patient_id == pid).order_by(Prescription.id.desc())
    ).scalars().all()
    if not rx_list:
        return "No prescriptions found."
    lines = []
    for r in rx_list[:10]:
        dt = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""
        status = "dispensed" if getattr(r, "is_dispensed", False) else "pending"
        lines.append(f"{r.id} | {dt} | {r.medication or r.title or ''} | {status}")
    return "ID | Date | Medication | Status\n" + "\n".join(lines)


def handle_password_help(msg: str) -> Optional[str]:
    if not re.search(r"\b(reset|forgot|change)\b.*\bpassword\b", msg, re.I):
        return None
    return "To reset your password: go to Login → ‘Forgot password’, enter your email, then follow the code/link you receive."


INTENTS = [
    handle_identity,
    handle_list_doctors,
    handle_availability,
    handle_booking,
    handle_cancel,
    handle_reschedule,
    handle_status,
    handle_upcoming,
    handle_notifications,
    handle_billing,
    handle_prescriptions,
    lambda db, user, msg: handle_password_help(msg),
]


def route_intents(db: Session, user: User, msg: str) -> Optional[str]:
    for handler in INTENTS:
        try:
            ans = handler(db, user, msg) if handler is not handle_password_help else handler(msg)
            if ans:
                return ans
        except Exception as e:
            log.exception("Intent handler error")
            return f"Error: {e}"
    return None


# ------------------------- API: Auth ----------------------
@app.post("/ai/token")
def login_token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(form.username, form.password)
    if not user:
        raise HTTPException(401, "Incorrect username or password")
    token = create_access_token({"sub": user.id, "role": user.role.value})
    return {"access_token": token, "token_type": "bearer", "role": user.role.value}


# ------------------------- API: Health --------------------
@app.get("/ai/health")
def health():
    return {
        "ok": True,
        "env": ENV,
        "llm_available": bool(ensure_llm()),
        "has_appt_service": HAS_APPT_SERVICE,
    }


# ------------------------- API: Chat ----------------------
@app.post("/ai/chat", response_model=ChatOut)
def chat(inp: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = inp.message.strip()
    if is_greeting(msg):
        return ChatOut(answer="Hi! How can I help with your Care Portal today?", metadata={"greeting": True})

    # Intent pass
    ans = route_intents(db, user, msg)
    if ans:
        return ChatOut(answer=clean_text(ans), metadata={"intent": True})

    # LLM fallback with safety rails
    if USE_LLM and HAS_LLAMA and ensure_llm():
        prompt = f"System: {SYSTEM_PROMPT}\nUser: {msg}\nAssistant:"
        ans = llm_complete(prompt)
        return ChatOut(answer=clean_text(ans), metadata={"llm": True})

    return ChatOut(answer="Try: ‘list doctors’, ‘my appointments’, ‘billing’, ‘prescriptions’, ‘notifications’.", metadata={"hint": True})


@app.post("/ai/stream")
def chat_stream(inp: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    msg = inp.message.strip()

    def gen():
        yield "data: " + json.dumps({"type": "start"}) + "\n\n"

        if is_greeting(msg):
            txt = "Hi! How can I help with your Care Portal today?"
            for chunk in re.findall(r".{1,120}", txt):
                yield "data: " + json.dumps({"type": "token", "text": chunk}) + "\n\n"
                time.sleep(0.01)
            yield "data: " + json.dumps({"type": "end"}) + "\n\n"
            return

        ans = route_intents(db, user, msg)
        if not ans and USE_LLM and HAS_LLAMA and ensure_llm():
            prompt = f"System: {SYSTEM_PROMPT}\nUser: {msg}\nAssistant:"
            ans = llm_complete(prompt, max_tokens=256)

        txt = clean_text(ans or "Sorry, I couldn't help with that.")
        for chunk in re.findall(r".{1,160}", txt):
            yield "data: " + json.dumps({"type": "token", "text": chunk}) + "\n\n"
            time.sleep(0.01)
        yield "data: " + json.dumps({"type": "end"}) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ------------------------- Error Handling ----------------
@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ------------------------- Main --------------------------
if __name__ == "__main__":
    import uvicorn
    log.info("Starting Care Portal AI Server...")
    uvicorn.run(app, host=HOST, port=PORT)
