# ai_server.py — Care Portal AI Chatbot Server (clean, robust)
from __future__ import annotations

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from fastapi import (
    FastAPI, Depends, HTTPException, status, Request, WebSocket, WebSocketDisconnect,
    UploadFile, File, Query
)
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm, HTTPBasic, HTTPBasicCredentials
from jose import JWTError, jwt
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

import uvicorn

# ─────────────────────────────────────────────────────────────────────────────
# App / Settings / Logging
# ─────────────────────────────────────────────────────────────────────────────
APP_NAME = "Care Portal AI Chatbot"
APP_VERSION = "1.1.0"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ai_server")

SECRET_KEY = os.getenv("CARE_PORTAL_SECRET_KEY", "change_me_dev_only")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MIN", "180"))
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "120"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
try:
    import openai  # type: ignore
    openai.api_key = OPENAI_API_KEY
except Exception:
    openai = None  # gracefully handle missing lib

# ─────────────────────────────────────────────────────────────────────────────
# Domain Imports (your project models/services)
# ─────────────────────────────────────────────────────────────────────────────
from care_portal.db import SessionLocal
from care_portal.models import (
    User, Role, Patient, Doctor, Receptionist, AdminProfile,
    Pharmacist, SupportAgent, FinanceOfficer,
    Appointment, AppointmentStatus,
    MedicalRecord, RecordAuthor, Billing, BillingStatus, PaymentMethod,
    Prescription, Notification, SupportTicket, InviteCode,
    DisciplinaryRecord, DisciplinaryStatus,
    StaffCheckin, Feedback,
    PasswordReset,  # assumed existing
)
from care_portal.auth import authenticate_user, verify_password, hash_password
from care_portal.services.appointments import AppointmentService

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI app & CORS
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title=APP_NAME, version=APP_VERSION, description="Care Portal backend + AI chat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# DB dependency
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ─────────────────────────────────────────────────────────────────────────────
# Auth / JWT helpers
# ─────────────────────────────────────────────────────────────────────────────
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
basic_auth = HTTPBasic()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MIN))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub")
        if uid is None:
            raise cred_exc
    except JWTError:
        raise cred_exc
    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise cred_exc
    return user

def require_role(user: User, allowed: List[Role]):
    if user.role not in allowed:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user

def admin_basic(credentials: HTTPBasicCredentials = Depends(basic_auth), db: Session = Depends(get_db)) -> User:
    user = db.query(User).filter(User.email == credentials.username).first()
    if not user or user.role != Role.admin or not verify_password(credentials.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    return user

# ─────────────────────────────────────────────────────────────────────────────
# Session timeout middleware
# ─────────────────────────────────────────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware
class SessionTimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        token = request.headers.get("authorization", "").replace("Bearer ", "")
        if token:
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                exp = payload.get("exp")
                if exp and datetime.utcfromtimestamp(exp) < datetime.utcnow():
                    return JSONResponse(status_code=401, content={"detail": "Session expired"})
            except Exception:
                return JSONResponse(status_code=401, content={"detail": "Invalid session"})
        return await call_next(request)
app.add_middleware(SessionTimeoutMiddleware)

# ─────────────────────────────────────────────────────────────────────────────
# Exception handlers
# ─────────────────────────────────────────────────────────────────────────────
@app.exception_handler(HTTPException)
def http_exc_handler(_: Request, exc: HTTPException):
    log.warning(f"HTTPException {exc.status_code}: {exc.detail}")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(Exception)
def any_exc_handler(_: Request, exc: Exception):
    log.error("Unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────
def paginate(query, page: int, size: int) -> Tuple[List[Any], int]:
    total = query.count()
    items = query.offset((page - 1) * size).limit(size).all()
    return items, total

def doctor_label(db: Session, doctor_id: int) -> str:
    d = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not d: return f"Doctor#{doctor_id}"
    u = db.query(User).filter(User.id == d.user_id).first() if d.user_id else None
    name = (u.full_name or u.email) if u else f"Doctor#{doctor_id}"
    spec = d.specialty or "General"
    return f"Dr. {name} ({spec})"

def patient_for_user(db: Session, user: User) -> Patient:
    pat = db.query(Patient).filter(Patient.user_id == user.id).first()
    if not pat:
        raise HTTPException(404, "Patient profile not found")
    return pat

def doctor_for_user(db: Session, user: User) -> Doctor:
    doc = db.query(Doctor).filter(Doctor.user_id == user.id).first()
    if not doc:
        raise HTTPException(404, "Doctor profile not found")
    return doc

def log_audit(db: Session, user: User, action: str, details: Dict[str, Any]):
    # For brevity, store in memory; adapt to DB table if needed
    notif = Notification(user_id=user.id, title=f"AUDIT: {action}", body=json.dumps(details))
    db.add(notif); db.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str

class MeOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    role: str
    phone: str | None

class AppointmentOut(BaseModel):
    id: int
    when: str
    doctor: str
    reason: str | None
    status: str

class AppointmentIn(BaseModel):
    doctor_id: int
    when: str = Field(..., description="ISO 8601 datetime")
    reason: str = ""

    @validator("when")
    def _iso_dt(cls, v):
        try:
            datetime.fromisoformat(v)
        except Exception:
            raise ValueError("when must be ISO 8601 datetime")
        return v

class AppointmentRescheduleIn(BaseModel):
    new_when: str

    @validator("new_when")
    def _iso_dt(cls, v):
        try:
            datetime.fromisoformat(v)
        except Exception:
            raise ValueError("new_when must be ISO 8601 datetime")
        return v

class BillingOut(BaseModel):
    id: int
    description: str
    amount: float
    status: str
    paid_at: str | None
    method: str | None

class BillingPayIn(BaseModel):
    method: str
    transaction_id: str | None = None

class PrescriptionOut(BaseModel):
    id: int
    date: str
    doctor: str
    medication: str
    dosage: str | None
    instructions: str | None
    repeats: int | None
    status: str

class NotificationOut(BaseModel):
    id: int
    time: str
    title: str
    body: str
    read: bool

class ChatIn(BaseModel):
    message: str
    history: List[Dict[str, str]] = []

class ChatOut(BaseModel):
    answer: str
    sources: list = []

class PasswordResetRequestIn(BaseModel):
    email: str

class PasswordResetConfirmIn(BaseModel):
    token: str
    new_password: str

# ─────────────────────────────────────────────────────────────────────────────
# Auth endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/token", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    u = authenticate_user(form.username, form.password)
    if not u:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    token = create_access_token({"sub": u.id, "role": u.role.value})
    return TokenOut(access_token=token, role=u.role.value)

@app.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return MeOut(
        id=user.id, email=user.email, full_name=user.full_name,
        role=user.role.value, phone=user.phone
    )

# ─────────────────────────────────────────────────────────────────────────────
# Health & version
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/healthz")
def healthz():
    return {"status": "ok", "utc": datetime.utcnow().isoformat(), "version": APP_VERSION}

@app.get("/version")
def version():
    return {"version": APP_VERSION}

# ─────────────────────────────────────────────────────────────────────────────
# Patient endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/patient/appointments", response_model=List[AppointmentOut])
def patient_appointments(
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    pat = patient_for_user(db, user)
    q = db.query(Appointment).filter(Appointment.patient_id == pat.id).order_by(Appointment.scheduled_for.desc())
    items, _ = paginate(q, page, size)
    out = []
    for a in items:
        out.append(AppointmentOut(
            id=a.id,
            when=a.scheduled_for.isoformat() if a.scheduled_for else "",
            doctor=doctor_label(db, a.doctor_id),
            reason=a.reason,
            status=a.status.value if a.status else "",
        ))
    return out

@app.post("/patient/appointments/book", response_model=AppointmentOut)
def patient_book(data: AppointmentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pat = patient_for_user(db, user)
    when_dt = datetime.fromisoformat(data.when)
    appt = Appointment(
        patient_id=pat.id, doctor_id=data.doctor_id, scheduled_for=when_dt,
        reason=data.reason, status=AppointmentStatus.booked
    )
    try:
        db.add(appt); db.commit()
    except SQLAlchemyError as e:
        db.rollback(); raise HTTPException(400, f"Booking failed: {e}")
    notify_msg = f"New appointment at {appt.scheduled_for}"
    db.add(Notification(user_id=user.id, title="Appointment Scheduled", body=notify_msg)); db.commit()
    log_audit(db, user, "book_appointment", {"doctor_id": data.doctor_id, "when": data.when, "reason": data.reason})
    return AppointmentOut(
        id=appt.id, when=appt.scheduled_for.isoformat(), doctor=doctor_label(db, appt.doctor_id),
        reason=appt.reason, status=appt.status.value
    )

@app.post("/patient/appointments/{appt_id}/cancel")
def patient_cancel(appt_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pat = patient_for_user(db, user)
    appt = db.query(Appointment).filter(Appointment.id == appt_id, Appointment.patient_id == pat.id).first()
    if not appt: raise HTTPException(404, "Appointment not found")
    appt.status = AppointmentStatus.cancelled
    db.commit()
    log_audit(db, user, "cancel_appointment", {"appointment_id": appt_id})
    return {"msg": "Appointment cancelled"}

@app.post("/patient/appointments/{appt_id}/reschedule")
def patient_reschedule(appt_id: int, data: AppointmentRescheduleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pat = patient_for_user(db, user)
    appt = db.query(Appointment).filter(Appointment.id == appt_id, Appointment.patient_id == pat.id).first()
    if not appt: raise HTTPException(404, "Appointment not found")
    appt.scheduled_for = datetime.fromisoformat(data.new_when)
    appt.status = AppointmentStatus.booked
    db.commit()
    log_audit(db, user, "reschedule_appointment", {"appointment_id": appt_id, "new_when": data.new_when})
    return {"msg": "Appointment rescheduled"}

@app.get("/patient/billing", response_model=List[BillingOut])
def patient_billing(
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    pat = patient_for_user(db, user)
    q = db.query(Billing).join(Appointment, Billing.appointment_id == Appointment.id)\
        .filter(Appointment.patient_id == pat.id).order_by(Billing.id.desc())
    items, _ = paginate(q, page, size)
    out = []
    for b in items:
        out.append(BillingOut(
            id=b.id, description=b.description or "", amount=float(b.amount or 0),
            status=b.status.value if b.status else "", paid_at=b.paid_at.isoformat() if b.paid_at else None,
            method=b.payment_method.value if b.payment_method else None
        ))
    return out

@app.post("/patient/billing/{bill_id}/pay")
def pay_bill(bill_id: int, data: BillingPayIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pat = patient_for_user(db, user)
    bill = db.query(Billing).join(Appointment, Billing.appointment_id == Appointment.id)\
        .filter(Billing.id == bill_id, Appointment.patient_id == pat.id).first()
    if not bill: raise HTTPException(404, "Bill not found")
    if bill.status == BillingStatus.paid: raise HTTPException(400, "Bill already paid")
    try:
        bill.status = BillingStatus.paid
        bill.payment_method = PaymentMethod(data.method)
        bill.paid_at = datetime.utcnow()
        if data.transaction_id: bill.transaction_id = data.transaction_id
        db.commit()
    except Exception as e:
        db.rollback(); raise HTTPException(400, f"Payment failed: {e}")
    return {"msg": "Bill marked as paid"}

@app.get("/patient/prescriptions", response_model=List[PrescriptionOut])
def patient_prescriptions(
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    pat = patient_for_user(db, user)
    q = db.query(Prescription).filter(Prescription.patient_id == pat.id).order_by(Prescription.id.desc())
    items, _ = paginate(q, page, size)
    out = []
    for rx in items:
        out.append(PrescriptionOut(
            id=rx.id,
            date=rx.created_at.isoformat() if rx.created_at else "",
            doctor=doctor_label(db, rx.doctor_id) if rx.doctor_id else "-",
            medication=rx.medication, dosage=rx.dosage, instructions=rx.instructions,
            repeats=rx.repeats, status="dispensed" if rx.is_dispensed else "pending"
        ))
    return out

@app.get("/patient/notifications", response_model=List[NotificationOut])
def patient_notifications(
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    q = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc())
    items, _ = paginate(q, page, size)
    return [
        NotificationOut(
            id=n.id, time=n.created_at.isoformat() if n.created_at else "",
            title=n.title, body=n.body, read=bool(n.read)
        ) for n in items
    ]

@app.post("/notifications/{notif_id}/read")
def notif_mark_read(notif_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == notif_id, Notification.user_id == user.id).first()
    if not n: raise HTTPException(404, "Notification not found")
    n.read = True; db.commit()
    return {"msg": "Marked as read"}

# ─────────────────────────────────────────────────────────────────────────────
# Doctor endpoints (essentials)
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/doctor/appointments", response_model=List[AppointmentOut])
def doctor_appointments(
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    require_role(user, [Role.doctor])
    doc = doctor_for_user(db, user)
    q = db.query(Appointment).filter(Appointment.doctor_id == doc.id).order_by(Appointment.scheduled_for.desc())
    items, _ = paginate(q, page, size)
    return [
        AppointmentOut(
            id=a.id, when=a.scheduled_for.isoformat() if a.scheduled_for else "",
            doctor="My schedule", reason=a.reason,
            status=a.status.value if a.status else ""
        ) for a in items
    ]

# ─────────────────────────────────────────────────────────────────────────────
# Admin endpoints (minimal, useful)
# ─────────────────────────────────────────────────────────────────────────────
class UserOut(BaseModel):
    id: int
    email: str
    full_name: str | None
    role: str
    phone: str | None

@app.get("/admin/users", response_model=List[UserOut])
def admin_users(
    page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    require_role(user, [Role.admin])
    q = db.query(User).order_by(User.id.desc())
    items, _ = paginate(q, page, size)
    return [UserOut(id=u.id, email=u.email, full_name=u.full_name, role=u.role.value, phone=u.phone) for u in items]

@app.get("/admin/analytics")
def admin_analytics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, [Role.admin])
    return {
        "users": db.query(User).count(),
        "patients": db.query(Patient).count(),
        "doctors": db.query(Doctor).count(),
        "appointments": db.query(Appointment).count(),
        "prescriptions": db.query(Prescription).count(),
        "bills": db.query(Billing).count(),
        "outstanding_bills": db.query(Billing).filter(Billing.status != BillingStatus.paid).count(),
        "utc_now": datetime.utcnow().isoformat(),
    }

# Export small CSV examples
@app.get("/admin/audit/export_csv")
def export_audit_csv(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_role(user, [Role.admin])
    # Demo: export notifications with AUDIT prefix as "audit"
    logs = db.query(Notification).filter(Notification.title.like("AUDIT:%")).order_by(Notification.id.asc()).all()
    import io, csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "title", "body", "created_at", "user_id"])
    for n in logs:
        w.writerow([n.id, n.title, n.body, n.created_at.isoformat() if n.created_at else "", n.user_id])
    buf.seek(0)
    return StreamingResponse(buf, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit.csv"})

# Admin CLI ping (basic auth)
@app.get("/admin/cli/ping")
def admin_cli_ping(_: User = Depends(admin_basic)):
    return {"msg": "pong"}

# ─────────────────────────────────────────────────────────────────────────────
# Password reset (basic demo flow; assumes PasswordReset model)
# ─────────────────────────────────────────────────────────────────────────────
from uuid import uuid4

@app.post("/auth/password_reset/request")
def password_reset_request(data: PasswordResetRequestIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == data.email).first()
    if not user:
        return {"msg": "If the email exists, a reset link will be sent."}
    token = uuid4().hex
    reset = PasswordReset(
        user_id=user.id, token=token,
        requested_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=2),
        used_at=None
    )
    db.add(reset); db.commit()
    log.info(f"Password reset token for {user.email}: {token}")
    return {"msg": "If the email exists, a reset link will be sent."}

@app.post("/auth/password_reset/confirm")
def password_reset_confirm(data: PasswordResetConfirmIn, db: Session = Depends(get_db)):
    reset = db.query(PasswordReset).filter(
        PasswordReset.token == data.token,
        PasswordReset.used_at.is_(None),
        PasswordReset.expires_at > datetime.utcnow(),
    ).first()
    if not reset: raise HTTPException(400, "Invalid or expired token.")
    user = db.query(User).filter(User.id == reset.user_id).first()
    if not user: raise HTTPException(404, "User not found.")
    user.password_hash = hash_password(data.new_password)
    reset.used_at = datetime.utcnow()
    db.commit()
    return {"msg": "Password has been reset."}

# ─────────────────────────────────────────────────────────────────────────────
# AI chat (optional)
# ─────────────────────────────────────────────────────────────────────────────
def build_patient_context(user: User, db: Session) -> str:
    pat = db.query(Patient).filter(Patient.user_id == user.id).first()
    if not pat: return "Patient not found."
    appts = db.query(Appointment).filter(Appointment.patient_id == pat.id).count()
    rx = db.query(Prescription).filter(Prescription.patient_id == pat.id).count()
    bills = db.query(Billing).join(Appointment, Billing.appointment_id == Appointment.id)\
        .filter(Appointment.patient_id == pat.id).all()
    outstanding = len([b for b in bills if b.status != BillingStatus.paid])
    return f"You are answering for patient {user.full_name or user.email}. Upcoming appointments: {appts}. Prescriptions: {rx}. Outstanding bills: {outstanding}."

def build_doctor_context(user: User, db: Session) -> str:
    doc = db.query(Doctor).filter(Doctor.user_id == user.id).first()
    if not doc: return "Doctor not found."
    appts = db.query(Appointment).filter(Appointment.doctor_id == doc.id).count()
    return f"You are answering for doctor {user.full_name or user.email} ({doc.specialty}). Upcoming appointments: {appts}."

def build_context(user: User, db: Session) -> str:
    if user.role == Role.patient: return build_patient_context(user, db)
    if user.role == Role.doctor: return build_doctor_context(user, db)
    return "You are a helpful hospital portal assistant."

def build_prompt(context: str, message: str, history: list) -> list:
    prompt = [{"role": "system", "content": "Answer accurately and concisely based on the hospital portal context."}]
    if context: prompt.append({"role": "system", "content": context})
    for h in history:
        prompt.append({"role": h.get("role","user"), "content": h.get("content","")})
    prompt.append({"role": "user", "content": message})
    return prompt

def ask_llm(prompt: list) -> str:
    if not openai or not OPENAI_API_KEY:
        return "AI not configured."
    try:
        resp = openai.ChatCompletion.create(model=OPENAI_MODEL, messages=prompt, max_tokens=512, temperature=0.2)
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"OpenAI error: {e}")
        return "Sorry, I couldn't generate a response."

@app.post("/chat", response_model=ChatOut)
def chat(data: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    context = build_context(user, db)
    answer = ask_llm(build_prompt(context, data.message, data.history))
    return ChatOut(answer=answer, sources=[])

# ─────────────────────────────────────────────────────────────────────────────
# WebSocket chat + notifications (simple)
# ─────────────────────────────────────────────────────────────────────────────
from collections import defaultdict
active_ws: Dict[int, List[WebSocket]] = defaultdict(list)

@app.websocket("/ws/chat")
async def ws_chat(ws: WebSocket, token: str):
    await ws.accept()
    db = SessionLocal()
    user: Optional[User] = None
    try:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id = int(payload.get("sub", 0))
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                await ws.send_json({"type":"error", "msg":"Invalid user"}); await ws.close(); return
        except Exception as e:
            await ws.send_json({"type":"error", "msg":f"Auth error: {e}"}); await ws.close(); return

        active_ws[user.id].append(ws)
        await ws.send_json({"type":"hello", "msg":f"Welcome, {user.full_name or user.email}!"})

        while True:
            data = await ws.receive_json()
            msg = data.get("message",""); hist = data.get("history",[])
            answer = ask_llm(build_prompt(build_context(user, db), msg, hist))
            await ws.send_json({"type":"chat", "answer":answer})
    except WebSocketDisconnect:
        pass
    finally:
        if user and ws in active_ws.get(user.id, []):
            active_ws[user.id].remove(ws)
        db.close()

def push_notification(user_id: int, notif: dict):
    for ws in active_ws.get(user_id, []):
        try:
            ws.send_json({"type":"notification", **notif})
        except Exception:
            continue

# ─────────────────────────────────────────────────────────────────────────────
# Startup / Shutdown
# ─────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_start():
    log.info(f"{APP_NAME} v{APP_VERSION} started")

@app.on_event("shutdown")
def on_stop():
    log.info(f"{APP_NAME} shutting down")

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run("ai_server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=bool(int(os.getenv("RELOAD","0"))))

