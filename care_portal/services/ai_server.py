from __future__ import annotations
import os, re, json, time, sqlite3, threading, logging, sys
from datetime import datetime, timedelta, timezone, time as dtime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Iterable, Generator
from fastapi import FastAPI, APIRouter, Request, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

LOG_LEVEL = os.getenv("CARE_PORTAL_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("care_portal.ai_server")

HOST = os.getenv("CARE_PORTAL_HOST", "127.0.0.1")
PORT = int(os.getenv("CARE_PORTAL_PORT", "8001"))
ROOT = os.getenv("CARE_PORTAL_ROOT", os.getcwd())
DB_PATH = os.getenv("CARE_PORTAL_DB_PATH", os.path.join(ROOT, "care_portal", "care_portal.db"))
CORS_ALLOW = os.getenv("CARE_PORTAL_CORS", "*")
USE_LLM = bool(int(os.getenv("CARE_PORTAL_USE_LLM", "1")))
LLM_MODEL_PATH = os.getenv("CARE_PORTAL_LLM_PATH", os.path.join(ROOT, "care_portal", "models", "tinyllama.gguf"))
LLM_CTX = int(os.getenv("CARE_PORTAL_LLM_CTX", "1536"))
LLM_THREADS = int(os.getenv("CARE_PORTAL_LLM_THREADS", str(max(2, (os.cpu_count() or 4) // 2))))
LLM_MAXTOK = int(os.getenv("CARE_PORTAL_LLM_MAXTOK", "256"))
LLM_TEMP = float(os.getenv("CARE_PORTAL_LLM_TEMP", "0.20"))
LLM_TOP_P = float(os.getenv("CARE_PORTAL_LLM_TOP_P", "0.90"))

UTC = timezone.utc
def utcnow() -> datetime: return datetime.now(tz=UTC)
def _db_exists() -> bool: 
    try: return os.path.exists(DB_PATH)
    except: return False
def _sqlite_conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row; return c

_HAS_SQLA = False
try:
    from sqlalchemy import select, func, and_, or_
    from sqlalchemy.orm import Session
    try:
        from care_portal.db import SessionLocal as _SessionLocal
        from care_portal.models import User, Patient, Doctor, Appointment, AppointmentStatus, Notification, Billing, BillingStatus, PaymentMethod, Prescription
        SessionLocal = _SessionLocal
        _HAS_SQLA = True
    except Exception as _e:
        SessionLocal = None
except Exception:
    SessionLocal = None

STOP_TOKENS = ["\nUser:", "\nAssistant:", "\nSystem:", "\nQuestion:", "\nAnswer:"]
SYSTEM_PROMPT = ("You are Care Portal Assistant for a hospital desktop app. Stay strictly on portal topics: appointments, doctors, patients, pharmacy, billing, notifications, records, login. Be concise and structured.")

class TinyLlama:
    def __init__(self, model_path: str, n_ctx: int, n_threads: int):
        self.model_path = os.path.abspath(os.path.expanduser(model_path))
        self.n_ctx = int(n_ctx)
        self.n_threads = int(n_threads)
        self._llm = None
        self._lock = threading.RLock()
        self._loaded = False
        self._err: Optional[str] = None
    @property
    def is_loaded(self) -> bool: return bool(self._loaded and self._llm is not None)
    @property
    def last_error(self) -> Optional[str]: return self._err
    def load(self) -> bool:
        with self._lock:
            if self.is_loaded: return True
            try:
                from llama_cpp import Llama
            except Exception as e:
                self._err = f"llama-cpp-python not installed: {e}"; log.error(self._err); return False
            if not os.path.exists(self.model_path):
                self._err = f"LLM model path not found: {self.model_path}"; log.error(self._err); return False
            try:
                self._llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx, n_threads=self.n_threads)
                self._loaded = True; log.info("TinyLlama loaded: %s", self.model_path); return True
            except Exception as e:
                self._err = f"Failed to load TinyLlama: {e}"; log.exception(self._err); self._llm=None; self._loaded=False; return False
    def completion(self, prompt: str, max_tokens: int, temperature: float, top_p: float) -> str:
        with self._lock:
            if not self.is_loaded: return f"(LLM unavailable: {self._err or 'not loaded'})"
            try:
                out = self._llm.create_completion(prompt=prompt, max_tokens=max_tokens, temperature=temperature, top_p=top_p, stop=STOP_TOKENS)
                return out["choices"][0]["text"]
            except Exception as e:
                log.exception("LLM completion failed: %s", e); return f"(LLM error: {e})"

_llama_mgr = TinyLlama(LLM_MODEL_PATH, LLM_CTX, LLM_THREADS)
_HAS_LLAMA = False
_llm_lock = threading.RLock()

def ensure_llm():
    global _HAS_LLAMA
    if not USE_LLM: return None
    with _llm_lock:
        if _llama_mgr.is_loaded: _HAS_LLAMA=True; return _llama_mgr
        ok = _llama_mgr.load(); _HAS_LLAMA = bool(ok and _llama_mgr.is_loaded)
        return _llama_mgr if _HAS_LLAMA else None

def _clean(txt: str) -> str:
    if not txt: return ""
    txt = re.sub(r'(?im)^(?:rule|question|answer|user|assistant|system)\s*:\s*.*$', '', txt)
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    out = " ".join(lines).strip()
    return re.sub(r"\s{2,}", " ", out)

def llm_answer(user_text: str, ctx: str = "") -> str:
    mgr = ensure_llm()
    if not mgr: return "AI not available. Try: 'list doctors', 'my appointments', 'prescriptions', 'billing', 'notifications'."
    prompt = SYSTEM_PROMPT + ("\nContext:\n"+ctx if ctx else "") + f"\nUser: {user_text.strip()}\nAssistant:"
    return _clean(mgr.completion(prompt, LLM_MAXTOK, LLM_TEMP, LLM_TOP_P))

def _format_table(rows: List[Tuple], headers: List[str]) -> str:
    if not rows: return "_No data found._"
    colw = [max(len(h), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    head = " | ".join(h.ljust(colw[i]) for i, h in enumerate(headers))
    sep = "-+-".join("-"*colw[i] for i in range(len(headers)))
    body = "\n".join(" | ".join(str(r[i]).ljust(colw[i]) for i in range(len(headers))) for r in rows)
    return f"{head}\n{sep}\n{body}"

def _rows_to_table(rows: List[sqlite3.Row], headers: List[str]) -> str:
    if not rows: return "_No data found._"
    out: List[Tuple] = []
    for r in rows:
        out.append(tuple((str(r[h]) if h in r.keys() and r[h] is not None else "") for h in headers))
    return _format_table(out, headers)

def derive_patient_id(user_id: int) -> Optional[int]:
    if user_id <= 0: return None
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                pid = db.scalar(select(Patient.id).where(Patient.user_id == user_id))
                return int(pid) if pid else None
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                row = c.execute("SELECT id FROM patient WHERE user_id = ?", (user_id,)).fetchone()
                return int(row["id"]) if row else None
        except Exception: pass
    return None

def tool_list_doctors() -> str:
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                rows = db.execute(select(Doctor.id, User.full_name, User.email, Doctor.specialty).join(User, Doctor.user_id==User.id, isouter=True).order_by(Doctor.id.asc())).all()
                out = []
                for did, fullname, email, spec in rows:
                    name = fullname or email or f"Doctor#{did}"
                    out.append((did, name, spec or "General"))
                return _format_table(out, ["ID","Name","Specialty"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                rows = c.execute("""SELECT d.id as ID, COALESCE(u.full_name,u.email,'Doctor#'||d.id) as Name, COALESCE(d.specialty,'General') as Specialty FROM doctor d LEFT JOIN user u ON u.id=d.user_id ORDER BY d.id ASC""").fetchall()
                return _rows_to_table(rows, ["ID","Name","Specialty"])
        except Exception: pass
    return "_No data found._"

def tool_my_appointments(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id` in context to list your appointments."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                appts = db.scalars(select(Appointment).where(Appointment.patient_id==pid).order_by(Appointment.scheduled_for.desc())).all()
                if not appts: return "You have no appointments."
                doc_ids = {a.doctor_id for a in appts if a.doctor_id}
                names: Dict[int,str] = {}
                for did in doc_ids:
                    try:
                        d = db.get(Doctor, int(did)); u = db.get(User, getattr(d,"user_id",0)) if d else None
                        nm = (u.full_name or u.email) if u else f"Doctor {did}"
                        names[int(did)] = f"Dr. {nm} ({d.specialty or 'General'})" if d else f"Doctor#{did}"
                    except Exception: names[int(did)] = f"Doctor#{did}"
                rows=[]
                for a in appts:
                    when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    doc = names.get(int(a.doctor_id or 0), f"Doctor#{a.doctor_id or ''}")
                    status = getattr(a,"status","") if isinstance(getattr(a,"status",None), str) else getattr(getattr(a,"status",None),"value","")
                    rows.append((a.id, when, doc, a.reason or "", status))
                return _format_table(rows, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                rows = c.execute("""SELECT a.id as ID, COALESCE(strftime('%Y-%m-%d %H:%M', a.scheduled_for),'') as "When", COALESCE('Dr. '||COALESCE(u.full_name,u.email), 'Doctor#'||a.doctor_id) as Doctor, COALESCE(a.reason,'') as Reason, COALESCE(a.status,'') as Status FROM appointment a LEFT JOIN doctor d ON d.id=a.doctor_id LEFT JOIN user u ON u.id=d.user_id WHERE a.patient_id=? ORDER BY a.scheduled_for DESC""",(pid,)).fetchall()
                if not rows: return "You have no appointments."
                return _rows_to_table(rows, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"
def tool_get_user_name(user_id: int) -> str:
    """
    Return the logged-in user's full name from the database.
    """
    try:
        with SessionLocal() as db:
            u = db.query(User).filter(User.id == user_id).first()
            if not u:
                return "I couldn’t find your account."
            return f"Your name is {u.full_name or u.email}."
    except Exception as e:
        logger.exception("tool_get_user_name failed: %s", e)
        return "Sorry, I couldn’t retrieve your name right now."

def tool_list_prescriptions(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                rx = db.scalars(select(Prescription).where(Prescription.patient_id==pid).order_by(Prescription.id.desc())).all()
                if not rx: return "No prescriptions found."
                rows=[]
                for r in rx:
                    dt = r.created_at.strftime("%Y-%m-%d %H:%M") if getattr(r,"created_at",None) else ""
                    doc = "-"
                    if getattr(r,"doctor_id",None):
                        d = db.get(Doctor,r.doctor_id); u = db.get(User,getattr(d,"user_id",0)) if d else None
                        doc = (u.full_name or u.email) if u else f"Doctor#{r.doctor_id}"
                    status = "dispensed" if getattr(r,"is_dispensed",False) else "pending"
                    rows.append((r.id, dt, doc, f"{r.medication or ''} {r.dosage or ''}".strip(), status))
                return _format_table(rows, ["ID","Date","Doctor","Rx","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                rows = c.execute("""SELECT p.id as ID, COALESCE(strftime('%Y-%m-%d %H:%M',p.created_at),'') as "Date", COALESCE(u.full_name,u.email,'Doctor#'||p.doctor_id) as Doctor, TRIM(COALESCE(p.medication,'')||' '||COALESCE(p.dosage,'')) as Rx, CASE WHEN COALESCE(p.is_dispensed,0)=1 THEN 'dispensed' ELSE 'pending' END as Status FROM prescription p LEFT JOIN doctor d ON d.id=p.doctor_id LEFT JOIN user u ON u.id=d.user_id WHERE p.patient_id=? ORDER BY p.id DESC""",(pid,)).fetchall()
                if not rows: return "No prescriptions found."
                return _rows_to_table(rows, ["ID","Date","Doctor","Rx","Status"])
        except Exception: pass
    return "_No data found._"

# ----------------------------
# Appointment helper snippets
# ----------------------------
def _sa_doctor_name(db, did: int) -> str:
    try:
        d = db.get(Doctor, did)
        if not d: return f"Doctor#{did}"
        u = db.get(User, d.user_id) if d.user_id else None
        name = (u.full_name or u.email) if u else f"Doctor {did}"
        spec = d.specialty or "General"
        return f"Dr. {name} ({spec})"
    except Exception:
        return f"Doctor#{did}"

def _sa_next_upcoming(db, pid: int):
    try:
        q = db.query(Appointment)\
              .filter(Appointment.patient_id == pid, Appointment.scheduled_for >= datetime.now())\
              .order_by(Appointment.scheduled_for.asc())\
              .limit(1)
        return q.first()
    except Exception:
        return None

def _sqlite_row_to_dt(v):
    if isinstance(v, datetime): return v
    if not v: return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(v, fmt)
        except Exception:
            pass
    return None

# 1) Next upcoming for the user
def tool_next_appointment(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                a = _sa_next_upcoming(db, pid)
                if not a: return "No upcoming appointments."
                when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                doc = _sa_doctor_name(db, int(a.doctor_id or 0))
                st = _enum_value(getattr(a, "status", ""))
                return _format_table([(a.id, when, doc, a.reason or "", st)],
                                     ["ID", "When", "Doctor", "Reason", "Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                r = c.execute("""
                    SELECT a.id as ID,
                           strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason,
                           COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ? AND a.scheduled_for >= ?
                    ORDER BY a.scheduled_for ASC
                    LIMIT 1
                """,(pid, now)).fetchall()
                if not r: return "No upcoming appointments."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 2) Appointments on a specific date
def tool_appointments_on_date(user_id: int, day_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    day = _parse_date_only(day_text)
    if not day: return "I couldn't parse the date."
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.query(Appointment)\
                    .filter(Appointment.patient_id==pid,
                            Appointment.scheduled_for>=start,
                            Appointment.scheduled_for<end)\
                    .order_by(Appointment.scheduled_for.asc())\
                    .all()
                if not rows: return f"No appointments on {start:%Y-%m-%d}."
                out=[]
                for a in rows:
                    when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    out.append((a.id, when, _sa_doctor_name(db, int(a.doctor_id or 0)),
                                a.reason or "", _enum_value(getattr(a,"status",""))))
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r = c.execute("""
                    SELECT a.id as ID,
                           strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason,
                           COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ?
                      AND a.scheduled_for >= ?
                      AND a.scheduled_for < ?
                    ORDER BY a.scheduled_for ASC
                """,(pid, start.strftime("%Y-%m-%d %H:%M:%S"),
                     end.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
                if not r: return f"No appointments on {start:%Y-%m-%d}."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 3) Between dates
def tool_appointments_between(user_id: int, start_text: str, end_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    start = _parse_date_time_hybrid(start_text)
    end = _parse_date_time_hybrid(end_text)
    if not start or not end: return "I couldn't parse the date range."
    if start > end: start, end = end, start
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.query(Appointment)\
                    .filter(Appointment.patient_id==pid,
                            Appointment.scheduled_for>=start,
                            Appointment.scheduled_for<=end)\
                    .order_by(Appointment.scheduled_for.asc()).all()
                if not rows: return "No appointments in that range."
                out=[]
                for a in rows:
                    when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    out.append((a.id, when, _sa_doctor_name(db, int(a.doctor_id or 0)),
                                a.reason or "", _enum_value(getattr(a,"status",""))))
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r = c.execute("""
                    SELECT a.id as ID,
                           strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason,
                           COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ?
                      AND a.scheduled_for >= ?
                      AND a.scheduled_for <= ?
                    ORDER BY a.scheduled_for ASC
                """,(pid, start.strftime("%Y-%m-%d %H:%M:%S"),
                     end.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
                if not r: return "No appointments in that range."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 4) Upcoming count
def tool_upcoming_count(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    now = datetime.now()
    if ModelsOk:
        try:
            with SessionLocal() as db:
                n = db.query(Appointment).filter(
                    Appointment.patient_id==pid, Appointment.scheduled_for>=now
                ).count()
                return f"You have {n} upcoming appointment(s)."
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                n = c.execute("""
                    SELECT COUNT(*) as n FROM appointment
                    WHERE patient_id=? AND scheduled_for >= ?
                """,(pid, now.strftime("%Y-%m-%d %H:%M:%S"))).fetchone()["n"]
                return f"You have {int(n)} upcoming appointment(s)."
        except Exception: pass
    return "I couldn’t compute the count."

# 5) Recent past appointments
def tool_past_appointments(user_id: int, limit: int = 10) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    now = datetime.now()
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.query(Appointment)\
                    .filter(Appointment.patient_id==pid, Appointment.scheduled_for<now)\
                    .order_by(Appointment.scheduled_for.desc())\
                    .limit(limit).all()
                if not rows: return "No past appointments."
                out=[]
                for a in rows:
                    when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    out.append((a.id, when, _sa_doctor_name(db, int(a.doctor_id or 0)),
                                a.reason or "", _enum_value(getattr(a,"status",""))))
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID,
                           strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason,
                           COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ? AND a.scheduled_for < ?
                    ORDER BY a.scheduled_for DESC
                    LIMIT ?
                """,(pid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), limit)).fetchall()
                if not r: return "No past appointments."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 6) By doctor
def tool_appointments_by_doctor(user_id: int, doc_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    dref = _match_doctor_free(doc_text or "")
    if not dref: return "I couldn't identify the doctor."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.query(Appointment)\
                    .filter(Appointment.patient_id==pid, Appointment.doctor_id==dref.id)\
                    .order_by(Appointment.scheduled_for.desc()).all()
                if not rows: return f"No appointments with Doctor #{dref.id}."
                out=[]
                for a in rows:
                    when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    out.append((a.id, when, _sa_doctor_name(db, dref.id),
                                a.reason or "", _enum_value(getattr(a,"status",""))))
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID,
                           strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason,
                           COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ? AND a.doctor_id = ?
                    ORDER BY a.scheduled_for DESC
                """,(pid, dref.id)).fetchall()
                if not r: return f"No appointments with Doctor #{dref.id}."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 7) By specialty
def tool_appointments_by_specialty(user_id: int, spec_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    spec = (spec_text or "").strip().lower()
    if not spec: return "Please provide a specialty."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.execute(
                    select(Appointment.id, Appointment.scheduled_for, Appointment.reason, Appointment.status,
                           Doctor.id, Doctor.specialty, User.full_name, User.email)
                    .join(Doctor, Appointment.doctor_id==Doctor.id)
                    .join(User, Doctor.user_id==User.id, isouter=True)
                    .where(Appointment.patient_id==pid)
                ).all()
                filt=[]
                for ap_id, when, reason, status, did, ds, full, email in rows:
                    if spec in (ds or "").lower():
                        doc = f"Dr. {(full or email or f'Doctor#{did}')}" + f" ({ds or 'General'})"
                        filt.append((ap_id, when.strftime("%Y-%m-%d %H:%M") if when else "",
                                     doc, reason or "", _enum_value(status)))
                if not filt: return f"No appointments for {spec_text}."
                return _format_table(filt, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID,
                           strftime('%Y-%m-%d %H:%M',a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(d.specialty,'') as Specialty,
                           COALESCE(a.reason,'') as Reason,
                           COALESCE(a.status,'') as Status
                    FROM appointment a
                    JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user u ON u.id = d.user_id
                    WHERE a.patient_id = ?
                    ORDER BY a.scheduled_for DESC
                """,(pid,)).fetchall()
                filt=[(row["ID"], row["When"], f"{row['Doctor']} ({row['Specialty'] or 'General'})",
                       row["Reason"], row["Status"]) for row in r
                      if spec in (row["Specialty"] or "").lower()]
                if not filt: return f"No appointments for {spec_text}."
                return _format_table(filt, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 8) Next with specific doctor
def tool_next_with_doctor(user_id: int, doc_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    dref = _match_doctor_free(doc_text or "")
    if not dref: return "I couldn't identify the doctor."
    now = datetime.now()
    if ModelsOk:
        try:
            with SessionLocal() as db:
                a = db.query(Appointment)\
                      .filter(Appointment.patient_id==pid,
                              Appointment.doctor_id==dref.id,
                              Appointment.scheduled_for>=now)\
                      .order_by(Appointment.scheduled_for.asc()).first()
                if not a: return "No upcoming appointment with that doctor."
                when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                return _format_table([(a.id, when, _sa_doctor_name(db, dref.id),
                                       a.reason or "", _enum_value(getattr(a,"status","")))],
                                     ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID, strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason, COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ? AND a.doctor_id = ? AND a.scheduled_for >= ?
                    ORDER BY a.scheduled_for ASC LIMIT 1
                """,(pid, dref.id, now.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
                if not r: return "No upcoming appointment with that doctor."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 9) First available with a doctor
def tool_first_available_with_doctor(doc_text: str, day_text: Optional[str] = None) -> str:
    dref = _match_doctor_free(doc_text or "")
    if not dref: return "I couldn't identify the doctor."
    start = _parse_date_only(day_text) if day_text else datetime.now()
    if not start: start = datetime.now()
    for i in range(0, 14):
        day = (start + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        slots = _slots_for_day(dref.id, day)
        if slots:
            return f"Earliest for Dr. {dref.name} ({dref.specialty}): {day:%Y-%m-%d} at {slots[0]}"
    return "No availability found in the next 2 weeks."

# 10) First available by specialty
def tool_first_available_by_specialty(spec_text: str, day_text: Optional[str] = None) -> str:
    spec = (spec_text or "").strip().lower()
    if not spec: return "Please provide a specialty."
    start = _parse_date_only(day_text) if day_text else datetime.now()
    if not start: start = datetime.now()
    docs = _fetch_doctors()
    cand = [d for d in docs if spec in (d.specialty or "").lower()]
    if not cand: return f"No doctors found for {spec_text}."
    for i in range(0, 14):
        day = (start + timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        best = []
        for d in cand:
            slots = _slots_for_day(d.id, day)
            if slots: best.append((d, slots[0]))
        if best:
            d, slot = sorted(best, key=lambda x: x[1])[0]
            return f"Earliest {spec_text}: Dr. {d.name} on {day:%Y-%m-%d} at {slot}"
    return f"No availability for {spec_text} in the next 2 weeks."

# 11) Conflicts on a day
def tool_conflicts_on(user_id: int, day_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    day = _parse_date_only(day_text)
    if not day: return "I couldn't parse the date."
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    appts=[]
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.query(Appointment)\
                    .filter(Appointment.patient_id==pid,
                            Appointment.scheduled_for>=start,
                            Appointment.scheduled_for<end)\
                    .order_by(Appointment.scheduled_for.asc()).all()
                for a in rows:
                    appts.append((a.scheduled_for, _sa_doctor_name(db, int(a.doctor_id or 0)), a.id))
        except Exception: pass
    if not appts and _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id, a.scheduled_for,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as doc
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ? AND a.scheduled_for >= ? AND a.scheduled_for < ?
                    ORDER BY a.scheduled_for ASC
                """,(pid, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
                for row in r:
                    appts.append((_sqlite_row_to_dt(row["scheduled_for"]), row["doc"], row["id"]))
        except Exception: pass
    if not appts: return "No appointments that day."
    clashes=[]
    for i in range(len(appts)-1):
        a_dt, a_doc, a_id = appts[i]
        b_dt, b_doc, b_id = appts[i+1]
        if not a_dt or not b_dt: continue
        if (b_dt - a_dt) <= timedelta(minutes=15):
            clashes.append((a_id, a_dt.strftime("%H:%M"), a_doc))
            clashes.append((b_id, b_dt.strftime("%H:%M"), b_doc))
    if not clashes: return "No conflicts detected."
    return "Potential conflicts:\n" + _format_table(clashes, ["ID","Time","Doctor"])

# 12) Search appointments by keyword
def tool_search_appointments(user_id: int, q: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    kw = (q or "").strip().lower()
    if not kw: return "Please provide a keyword."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows = db.execute(
                    select(Appointment.id, Appointment.scheduled_for, Appointment.reason, Appointment.status,
                           Doctor.id, Doctor.specialty, User.full_name, User.email)
                    .join(Doctor, Appointment.doctor_id==Doctor.id, isouter=True)
                    .join(User, Doctor.user_id==User.id, isouter=True)
                    .where(Appointment.patient_id==pid)
                ).all()
                out=[]
                for ap_id, when, reason, status, did, ds, full, email in rows:
                    docname = (full or email or f"Doctor#{did}") if (full or email or did) else "Doctor"
                    if any(s for s in [reason, docname, ds] if s and kw in s.lower()):
                        out.append((ap_id,
                                    when.strftime("%Y-%m-%d %H:%M") if when else "",
                                    f"Dr. {docname} ({ds or 'General'})",
                                    reason or "",
                                    _enum_value(status)))
                if not out: return "No matches."
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID, a.scheduled_for, COALESCE(a.reason,'') as Reason,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(d.specialty,'') as Specialty, COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id = ?
                """,(pid,)).fetchall()
                out=[]
                for row in r:
                    when=_sqlite_row_to_dt(row["scheduled_for"])
                    doc=row["Doctor"]; spec=row["Specialty"]; reason=row["Reason"]
                    if any(s for s in [reason, doc, spec] if s and kw in s.lower()):
                        out.append((row["ID"], when.strftime("%Y-%m-%d %H:%M") if when else "",
                                    f"{doc} ({spec or 'General'})", reason, row["Status"]))
                if not out: return "No matches."
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 13) Appointment details card
def tool_appointment_details(user_id: int, appt_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                a = db.get(Appointment, appt_id)
                if not a or a.patient_id != pid: return "Appointment not found."
                when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                doc=_sa_doctor_name(db, int(a.doctor_id or 0))
                st=_enum_value(getattr(a,"status",""))
                lines=[
                    f"Appointment #{a.id}",
                    f"When: {when}",
                    f"Doctor: {doc}",
                    f"Reason: {a.reason or '-'}",
                    f"Status: {st}",
                ]
                return "\n".join(lines)
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                row=c.execute("""
                    SELECT a.*, COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as doc
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.id = ? AND a.patient_id = ?
                """,(appt_id, pid)).fetchone()
                if not row: return "Appointment not found."
                when=_sqlite_row_to_dt(row["scheduled_for"])
                lines=[
                    f"Appointment #{row['id']}",
                    f"When: {when.strftime('%Y-%m-%d %H:%M') if when else ''}",
                    f"Doctor: {row['doc']}",
                    f"Reason: {row['reason'] or '-'}",
                    f"Status: {row['status'] or ''}",
                ]
                return "\n".join(lines)
        except Exception: pass
    return "_No data found._"

# 14) Cancel next upcoming
def tool_cancel_next(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                a = _sa_next_upcoming(db, pid)
                if not a: return "No upcoming appointments to cancel."
                try: a.status = AppointmentStatus.cancelled
                except Exception: a.status = "cancelled"
                db.commit()
                return f"Appointment #{a.id} cancelled."
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                row=c.execute("""
                    SELECT id FROM appointment
                    WHERE patient_id=? AND scheduled_for>=?
                    ORDER BY scheduled_for ASC LIMIT 1
                """,(pid, now)).fetchone()
                if not row: return "No upcoming appointments to cancel."
                c.execute("UPDATE appointment SET status='cancelled' WHERE id=?", (row["id"],))
                c.commit()
                return f"Appointment #{row['id']} cancelled."
        except Exception: pass
    return "Cancel failed."

# 15) Reschedule next upcoming
def tool_reschedule_next(user_id: int, new_when_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    new_when = _parse_date_time_hybrid(new_when_text)
    if not new_when or new_when < datetime.now(): return "The new date/time is invalid or in the past."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                a = _sa_next_upcoming(db, pid)
                if not a: return "No upcoming appointments to move."
                a.scheduled_for = new_when
                try: a.status = AppointmentStatus.booked
                except Exception: a.status = "booked"
                db.commit()
                return f"Appointment #{a.id} moved to {new_when:%Y-%m-%d %H:%M}."
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                now=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                row=c.execute("""
                    SELECT id FROM appointment
                    WHERE patient_id=? AND scheduled_for>=?
                    ORDER BY scheduled_for ASC LIMIT 1
                """,(pid, now)).fetchone()
                if not row: return "No upcoming appointments to move."
                c.execute("UPDATE appointment SET scheduled_for=?, status='booked' WHERE id=?",
                          (new_when.strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
                c.commit()
                return f"Appointment #{row['id']} moved to {new_when:%Y-%m-%d %H:%M}."
        except Exception: pass
    return "Reschedule failed."

# 16) Day availability for a doctor
def tool_day_availability_for_doctor(doc_text: str, day_text: str) -> str:
    dref = _match_doctor_free(doc_text or "")
    if not dref: return "I couldn't identify the doctor."
    day = _parse_date_only(day_text or "")
    if not day: return "I couldn't parse the date."
    slots = _slots_for_day(dref.id, day)
    head = f"Available slots for Dr. {dref.name} ({dref.specialty}) on {day:%Y-%m-%d}"
    if not slots: return f"{head}\nNone"
    return head + "\n" + "\n".join(f"• {s}" for s in slots)

# 17) Week view (7 days) for the user
def tool_week_view(user_id: int, anchor_text: str = "this week") -> str:
    day = _parse_date_only(anchor_text) or datetime.now()
    start = day - timedelta(days=day.weekday())  # Monday
    end = start + timedelta(days=7)
    rows=[]
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if ModelsOk:
        try:
            with SessionLocal() as db:
                items = db.query(Appointment)\
                    .filter(Appointment.patient_id==pid,
                            Appointment.scheduled_for>=start,
                            Appointment.scheduled_for<end)\
                    .order_by(Appointment.scheduled_for.asc()).all()
                for a in items:
                    when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    rows.append((when, _sa_doctor_name(db, int(a.doctor_id or 0)), a.reason or "", _enum_value(getattr(a,"status",""))))
        except Exception: pass
    if not rows and _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason, COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user   u ON u.id = d.user_id
                    WHERE a.patient_id=?
                      AND a.scheduled_for >= ?
                      AND a.scheduled_for < ?
                    ORDER BY a.scheduled_for ASC
                """,(pid, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
                rows=[(row["When"], row["Doctor"], row["Reason"], row["Status"]) for row in r]
        except Exception: pass
    if not rows: return f"No appointments for the week starting {start:%Y-%m-%d}."
    return _format_table(rows, ["When","Doctor","Reason","Status"])

# 18) By status
def tool_appointments_by_status(user_id: int, st_text: str) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    want = (st_text or "").lower()
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows=db.query(Appointment).filter(Appointment.patient_id==pid).order_by(Appointment.scheduled_for.desc()).all()
                out=[]
                for a in rows:
                    st=_enum_value(getattr(a,"status","")).lower()
                    if want in st:
                        when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                        out.append((a.id, when, _sa_doctor_name(db, int(a.doctor_id or 0)),
                                    a.reason or "", _enum_value(getattr(a,"status",""))))
                if not out: return f"No {st_text} appointments."
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID, strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason, COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user u ON u.id = d.user_id
                    WHERE a.patient_id = ?
                    ORDER BY a.scheduled_for DESC
                """,(pid,)).fetchall()
                out=[(row["ID"], row["When"], row["Doctor"], row["Reason"], row["Status"]) for row in r
                     if want in (row["Status"] or "").lower()]
                if not out: return f"No {st_text} appointments."
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 19) Near a reference time (±window)
def tool_appointments_near_time(user_id: int, ref_text: str, window_minutes: int = 60) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    ref = _parse_date_time_hybrid(ref_text) or _parse_date_only(ref_text)
    if not ref: return "I couldn't parse the reference time."
    start = ref - timedelta(minutes=window_minutes)
    end = ref + timedelta(minutes=window_minutes)
    if ModelsOk:
        try:
            with SessionLocal() as db:
                rows=db.query(Appointment)\
                       .filter(Appointment.patient_id==pid,
                               Appointment.scheduled_for>=start,
                               Appointment.scheduled_for<=end)\
                       .order_by(Appointment.scheduled_for.asc()).all()
                if not rows: return "No appointments near that time."
                out=[]
                for a in rows:
                    when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    out.append((a.id, when, _sa_doctor_name(db, int(a.doctor_id or 0)),
                                a.reason or "", _enum_value(getattr(a,"status",""))))
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                r=c.execute("""
                    SELECT a.id as ID, strftime('%Y-%m-%d %H:%M', a.scheduled_for) as When,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                           COALESCE(a.reason,'') as Reason, COALESCE(a.status,'') as Status
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user u ON u.id = d.user_id
                    WHERE a.patient_id = ?
                      AND a.scheduled_for >= ?
                      AND a.scheduled_for <= ?
                    ORDER BY a.scheduled_for ASC
                """,(pid, start.strftime("%Y-%m-%d %H:%M:%S"),
                     end.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
                if not r: return "No appointments near that time."
                return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    return "_No data found._"

# 20) Summary card: next + count + last 3
def tool_appointments_summary_card(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    now = datetime.now()
    next_part = "Next: none"
    count_part = "Upcoming: 0"
    recent_part = "Recent:\nNone"
    # SQLAlchemy path
    if ModelsOk:
        try:
            with SessionLocal() as db:
                nxt = db.query(Appointment)\
                        .filter(Appointment.patient_id==pid, Appointment.scheduled_for>=now)\
                        .order_by(Appointment.scheduled_for.asc()).first()
                if nxt:
                    when=nxt.scheduled_for.strftime("%Y-%m-%d %H:%M") if nxt.scheduled_for else ""
                    next_part=f"Next: #{nxt.id} • {when} • {_sa_doctor_name(db, int(nxt.doctor_id or 0))}"
                cnt = db.query(Appointment).filter(Appointment.patient_id==pid, Appointment.scheduled_for>=now).count()
                count_part=f"Upcoming: {cnt}"
                rec = db.query(Appointment)\
                        .filter(Appointment.patient_id==pid, Appointment.scheduled_for<now)\
                        .order_by(Appointment.scheduled_for.desc()).limit(3).all()
                if rec:
                    lines=[]
                    for a in rec:
                        when=a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                        lines.append(f"• #{a.id} {when} — {_sa_doctor_name(db, int(a.doctor_id or 0))}")
                    recent_part="Recent:\n"+"\n".join(lines)
                return "\n".join([next_part, count_part, recent_part])
        except Exception: pass
    # SQLite path
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                now_s = now.strftime("%Y-%m-%d %H:%M:%S")
                nxt=c.execute("""
                    SELECT a.id, a.scheduled_for,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as doc
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user u ON u.id = d.user_id
                    WHERE a.patient_id=? AND a.scheduled_for>=?
                    ORDER BY a.scheduled_for ASC LIMIT 1
                """,(pid, now_s)).fetchone()
                if nxt:
                    dt=_sqlite_row_to_dt(nxt["scheduled_for"])
                    next_part=f"Next: #{nxt['id']} • {dt.strftime('%Y-%m-%d %H:%M') if dt else ''} • {nxt['doc']}"
                cnt=c.execute("""
                    SELECT COUNT(*) as n FROM appointment
                    WHERE patient_id=? AND scheduled_for>=?
                """,(pid, now_s)).fetchone()["n"]
                count_part=f"Upcoming: {int(cnt)}"
                rec=c.execute("""
                    SELECT a.id, a.scheduled_for,
                           COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as doc
                    FROM appointment a
                    LEFT JOIN doctor d ON d.id = a.doctor_id
                    LEFT JOIN user u ON u.id = d.user_id
                    WHERE a.patient_id=? AND a.scheduled_for<?
                    ORDER BY a.scheduled_for DESC LIMIT 3
                """,(pid, now_s)).fetchall()
                if rec:
                    lines=[]
                    for r in rec:
                        dt=_sqlite_row_to_dt(r["scheduled_for"])
                        lines.append(f"• #{r['id']} {dt.strftime('%Y-%m-%d %H:%M') if dt else ''} — {r['doc']}")
                    recent_part="Recent:\n"+"\n".join(lines)
                return "\n".join([next_part, count_part, recent_part])
        except Exception: pass
    return "No data."

def tool_list_billing(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                rows = db.execute(select(Billing.id,Billing.description,Billing.amount,Billing.status,Billing.paid_at).join(Appointment, Billing.appointment_id==Appointment.id).where(Appointment.patient_id==pid).order_by(Billing.id.desc())).all()
                if not rows: return "You have no bills."
                out=[]
                for bid, desc, amt, status, paid_at in rows:
                    st = status.value if getattr(status,"value",None) else str(status or "")
                    paid = paid_at.strftime("%Y-%m-%d %H:%M") if paid_at else ""
                    out.append((bid, desc or "", f"{(amt or 0):.2f}", st, paid))
                return _format_table(out, ["ID","Description","Amount","Status","Paid At"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                rows = c.execute("""SELECT b.id as ID, COALESCE(b.description,'') as Description, printf('%.2f',COALESCE(b.amount,0)) as Amount, COALESCE(b.status,'') as Status, COALESCE(strftime('%Y-%m-%d %H:%M',b.paid_at),'') as "Paid At" FROM billing b JOIN appointment a ON a.id=b.appointment_id WHERE a.patient_id=? ORDER BY b.id DESC""",(pid,)).fetchall()
                if not rows: return "You have no bills."
                return _rows_to_table(rows, ["ID","Description","Amount","Status","Paid At"])
        except Exception: pass
    return "_No data found._"

def tool_list_notifications(user_id: int) -> str:
    if user_id <= 0: return "I need your `user_id`."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                notes = db.scalars(select(Notification).where(Notification.user_id==user_id).order_by(Notification.created_at.desc())).all()
                if not notes: return "No notifications."
                rows=[]
                for n in notes:
                    when = n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else ""
                    rows.append((n.id, when, n.title or "", "yes" if bool(getattr(n,"read",False)) else ""))
                return _format_table(rows, ["ID","Time","Title","Read"])
        except Exception: pass
    if _db_exists():
        try:
            with _sqlite_conn() as c:
                rows = c.execute("""SELECT id as ID, COALESCE(strftime('%Y-%m-%d %H:%M',created_at),'') as Time, COALESCE(title,'') as Title, CASE WHEN COALESCE(read,0)=1 THEN 'yes' ELSE '' END as Read FROM notification WHERE user_id=? ORDER BY created_at DESC""",(user_id,)).fetchall()
                if not rows: return "No notifications."
                return _rows_to_table(rows, ["ID","Time","Title","Read"])
        except Exception: pass
    return "_No data found._"

try:
    from rapidfuzz import process as rf_process, fuzz as rf_fuzz
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False
try:
    import dateparser
    _HAS_DATEPARSER = True
except Exception:
    _HAS_DATEPARSER = False

def _norm(s: str) -> str: return re.sub(r"\s+"," ", s or "").strip().lower()
def _today_local() -> datetime: return datetime.now()
_COMMON_TIMEWORDS = {"morning":(dtime(8,0),dtime(11,59)),"noon":(dtime(12,0),dtime(13,0)),"afternoon":(dtime(12,30),dtime(17,0)),"evening":(dtime(17,0),dtime(20,0))}
def _parse_date_only(text: str, base: Optional[datetime]=None) -> Optional[datetime]:
    if _HAS_DATEPARSER:
        try:
            base = base or _today_local()
            dt = dateparser.parse(text, settings={"RELATIVE_BASE":base,"PREFER_DATES_FROM":"future","RETURN_AS_TIMEZONE_AWARE":False})
            if dt: return datetime(dt.year, dt.month, dt.day)
        except Exception: pass
    t=_norm(text)
    if "today" in t:
        d=_today_local(); return datetime(d.year,d.month,d.day)
    if any(w in t for w in ("tomorrow","tmrw","tmr")):
        d=_today_local()+timedelta(days=1); return datetime(d.year,d.month,d.day)
    return None
def _parse_date_time_hybrid(text: str, base: Optional[datetime]=None) -> Optional[datetime]:
    if not text: return None
    s=text.strip()
    m=re.search(r"\b(20\d{2}[-/]\d{1,2}[-/]\d{1,2})(?:[ T](\d{1,2}:\d{2}))?\b", s)
    if m:
        ds,ts=m.group(1),m.group(2); ds=ds.replace("/","-")
        if ts:
            try: return datetime.strptime(f"{ds} {ts}","%Y-%m-%d %H:%M")
            except Exception: pass
        try:
            d=datetime.strptime(ds,"%Y-%m-%d"); return d.replace(hour=10,minute=0,second=0,microsecond=0)
        except Exception: pass
    for key,(t0,t1) in _COMMON_TIMEWORDS.items():
        if key in s.lower():
            day = _parse_date_only(s, base)
            if day:
                mid=int(((t0.hour*60+t0.minute)+(t1.hour*60+t1.minute))/2); hh,mm=divmod(mid,60)
                return day.replace(hour=hh,minute=mm,second=0,microsecond=0)
    if _HAS_DATEPARSER:
        try:
            base=base or _today_local()
            dt=dateparser.parse(s, settings={"RELATIVE_BASE":base,"PREFER_DATES_FROM":"future","RETURN_AS_TIMEZONE_AWARE":False})
            return dt
        except Exception: pass
    day=_parse_date_only(s, base)
    if day: return day.replace(hour=10,minute=0,second=0,microsecond=0)
    return None

@dataclass
class DoctorRef: id:int; name:str; specialty:str
def _fetch_doctors() -> List[DoctorRef]:
    rows: List[DoctorRef]=[]
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                res = db.execute(select(Doctor.id, User.full_name, User.email, Doctor.specialty).join(User, Doctor.user_id==User.id, isouter=True)).all()
                for did,full,email,spec in res:
                    name = full or email or f"Doctor#{did}"
                    rows.append(DoctorRef(int(did), name, spec or "General"))
                return rows
        except Exception: pass
    try:
        with _sqlite_conn() as c:
            r=c.execute("""SELECT d.id as id, COALESCE(u.full_name,u.email,'Doctor#'||d.id) as name, COALESCE(d.specialty,'General') as specialty FROM doctor d LEFT JOIN user u ON u.id=d.user_id ORDER BY d.id""").fetchall()
            for row in r: rows.append(DoctorRef(int(row["id"]), row["name"], row["specialty"]))
    except Exception: pass
    return rows
def _match_doctor_free(text: str) -> Optional[DoctorRef]:
    mid = re.search(r"\bdoctor\s*(\d+)\b", text, re.I) or re.search(r"\bdr\s*(\d+)\b", text, re.I)
    if mid:
        did=int(mid.group(1))
        for d in _fetch_doctors():
            if d.id==did: return d
    needle=_norm(re.sub(r"\b(dr|doctor)\b","", text))
    if not needle: return None
    docs=_fetch_doctors()
    for d in docs:
        if needle in _norm(d.name) or needle in _norm(d.specialty): return d
    if _HAS_RAPIDFUZZ:
        choices=[f"{d.name} // {d.specialty} // #{d.id}" for d in docs]
        m=rf_process.extractOne(needle, choices, scorer=rf_fuzz.QRatio, score_cutoff=62)
        if m:
            tail=m[0].split("//")[-1].strip()
            if tail.startswith("#"):
                did=int(tail[1:])
                for d in docs:
                    if d.id==did: return d
    for d in docs:
        if _norm(d.name).startswith(needle) or _norm(d.specialty).startswith(needle): return d
    return None

def _slots_for_day(doctor_id: int, day: datetime) -> List[str]:
    slots=[]; base=day.replace(hour=9,minute=0,second=0,microsecond=0)
    for i in range(0,(16-9)*60+30,30):
        t=base+timedelta(minutes=i)
        if t.hour==12 and t.minute in (0,30): continue
        slots.append(t.strftime("%H:%M"))
    return slots

def _ensure_patient_id(user_id: int, context: Dict[str,Any]) -> Optional[int]:
    pid = context.get("patient_id"); 
    if pid: 
        try: return int(pid)
        except: return None
    if user_id: return derive_patient_id(user_id)
    return None

def book_appointment(user_id: int, context: Dict[str,Any], doctor_text: str, when_text: str, reason: str="") -> str:
    pid=_ensure_patient_id(user_id, context)
    if not pid: return "I need your `patient_id` to book."
    dref=_match_doctor_free(doctor_text)
    if not dref: return "I couldn't identify the doctor. Try 'book appointment with Doctor 3 on 2025-10-12 09:30 reason: checkup'."
    when=_parse_date_time_hybrid(when_text)
    if not when or when<_today_local(): return "The date/time looks invalid or in the past. Use a future time like '2025-10-12 09:30'."
    if when.minute%5!=0: when=when.replace(minute=(when.minute//5)*5, second=0, microsecond=0)
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                ap=Appointment(patient_id=pid, doctor_id=dref.id, scheduled_for=when, reason=reason or None)
                try: ap.status=AppointmentStatus.booked
                except: ap.status="booked"
                db.add(ap); db.commit(); db.refresh(ap)
                u=db.get(User, db.get(Doctor, dref.id).user_id) if db.get(Doctor, dref.id) else None
                label=f"Dr. {(u.full_name or u.email) if u else dref.name} ({db.get(Doctor,dref.id).specialty if db.get(Doctor,dref.id) else dref.specialty})"
                return f"Booked appointment #{ap.id} at {when:%Y-%m-%d %H:%M} with {label}."
        except Exception: pass
    try:
        with _sqlite_conn() as c:
            cur=c.execute("INSERT INTO appointment (patient_id,doctor_id,scheduled_for,reason,status) VALUES (?,?,?,?, 'booked')",(pid,dref.id,when.strftime("%Y-%m-%d %H:%M:%S"),reason or None))
            appt_id=cur.lastrowid; c.commit()
            r=c.execute("SELECT COALESCE(u.full_name,u.email,'Doctor') name, COALESCE(d.specialty,'General') spec FROM doctor d LEFT JOIN user u ON u.id=d.user_id WHERE d.id=?",(dref.id,)).fetchone()
            label=f"Dr. {r['name']} ({r['spec']})" if r else f"Doctor#{dref.id}"
            return f"Booked appointment #{appt_id} at {when:%Y-%m-%d %H:%M} with {label}."
    except Exception: return "Booking failed due to a database error."

def _pick_closest(appts: List[Any], when: datetime) -> Optional[Any]:
    target=None; best=10**12
    for a in appts:
        try:
            d=a.scheduled_for
            if not isinstance(d, datetime): continue
            diff=abs((d-when).total_seconds())
            if diff<best: best=diff; target=a
        except: pass
    return target
def _pick_closest_sqlite(rows, when: datetime):
    target=None; best=10**12
    for r in rows:
        v=r["scheduled_for"]; dt=None
        if isinstance(v,str):
            for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M"):
                try: dt=datetime.strptime(v,fmt); break
                except: continue
        elif isinstance(v,datetime): dt=v
        if not dt: continue
        diff=abs((dt-when).total_seconds())
        if diff<best: best=diff; target=r
    return target

def cancel_appointment(user_id: int, context: Dict[str,Any], appt_id: Optional[int], doctor_text: Optional[str], when_text: Optional[str]) -> str:
    if appt_id: 
        if _HAS_SQLA and SessionLocal:
            try:
                with SessionLocal() as db:
                    ap=db.get(Appointment, appt_id)
                    if not ap: return f"Appointment #{appt_id} not found."
                    try: ap.status=AppointmentStatus.cancelled
                    except: ap.status="cancelled"
                    db.commit(); return f"Appointment #{appt_id} cancelled."
            except Exception: pass
        try:
            with _sqlite_conn() as c:
                row=c.execute("SELECT id FROM appointment WHERE id = ?",(appt_id,)).fetchone()
                if not row: return f"Appointment #{appt_id} not found."
                c.execute("UPDATE appointment SET status='cancelled' WHERE id=?",(appt_id,)); c.commit()
                return f"Appointment #{appt_id} cancelled."
        except Exception: return "Cancel failed due to a database error."
    dref=_match_doctor_free(doctor_text or ""); when=_parse_date_time_hybrid(when_text or "")
    if not dref or not when: return "Please specify appointment ID, or provide both doctor and date/time."
    pid=_ensure_patient_id(user_id, context)
    if not pid: return "I need your `patient_id` to cancel."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                ap = db.execute(select(Appointment).where(Appointment.patient_id==pid, Appointment.doctor_id==dref.id)).scalars().all()
                target=_pick_closest(ap, when)
                if not target: return "No matching appointment found."
                try: target.status=AppointmentStatus.cancelled
                except: target.status="cancelled"
                db.commit(); return f"Appointment #{target.id} cancelled."
        except Exception: pass
    try:
        with _sqlite_conn() as c:
            appts=c.execute("SELECT * FROM appointment WHERE patient_id=? AND doctor_id=?", (pid,dref.id)).fetchall()
            if not appts: return "No matching appointment found."
            target=_pick_closest_sqlite(appts, when)
            if not target: return "No matching appointment found."
            c.execute("UPDATE appointment SET status='cancelled' WHERE id=?", (target["id"],)); c.commit()
            return f"Appointment #{target['id']} cancelled."
    except Exception: return "Cancel failed due to a database error."

def reschedule_appointment(user_id: int, context: Dict[str,Any], appt_id: Optional[int], doctor_text: Optional[str], old_when_text: Optional[str], new_when_text: str) -> str:
    new_when=_parse_date_time_hybrid(new_when_text)
    if not new_when or new_when<_today_local(): return "The new date/time is invalid or in the past."
    if appt_id:
        if _HAS_SQLA and SessionLocal:
            try:
                with SessionLocal() as db:
                    ap=db.get(Appointment, appt_id)
                    if not ap: return f"Appointment #{appt_id} not found."
                    ap.scheduled_for=new_when
                    try: ap.status=AppointmentStatus.booked
                    except: ap.status="booked"
                    db.commit(); return f"Appointment #{appt_id} moved to {new_when:%Y-%m-%d %H:%M}."
            except Exception: pass
        try:
            with _sqlite_conn() as c:
                row=c.execute("SELECT id FROM appointment WHERE id=?",(appt_id,)).fetchone()
                if not row: return f"Appointment #{appt_id} not found."
                c.execute("UPDATE appointment SET scheduled_for=?, status='booked' WHERE id=?", (new_when.strftime("%Y-%m-%d %H:%M:%S"), appt_id)); c.commit()
                return f"Appointment #{appt_id} moved to {new_when:%Y-%m-%d %H:%M}."
        except Exception: return "Reschedule failed due to a database error."
    dref=_match_doctor_free(doctor_text or ""); old_when=_parse_date_time_hybrid(old_when_text or "")
    if not dref or not old_when: return "Please provide appointment ID, or both doctor and old date/time."
    pid=_ensure_patient_id(user_id, context)
    if not pid: return "I need your `patient_id` to reschedule."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                ap = db.execute(select(Appointment).where(Appointment.patient_id==pid, Appointment.doctor_id==dref.id)).scalars().all()
                target=_pick_closest(ap, old_when)
                if not target: return "No matching appointment found."
                target.scheduled_for=new_when
                try: target.status=AppointmentStatus.booked
                except: target.status="booked"
                db.commit(); return f"Appointment #{target.id} moved to {new_when:%Y-%m-%d %H:%M}."
        except Exception: pass
    try:
        with _sqlite_conn() as c:
            appts=c.execute("SELECT * FROM appointment WHERE patient_id=? AND doctor_id=?", (pid,dref.id)).fetchall()
            target=_pick_closest_sqlite(appts, old_when)
            if not target: return "No matching appointment found."
            c.execute("UPDATE appointment SET scheduled_for=?, status='booked' WHERE id=?", (new_when.strftime("%Y-%m-%d %H:%M:%S"), int(target["id"]))); c.commit()
            return f"Appointment #{int(target['id'])} moved to {new_when:%Y-%m-%d %H:%M}."
    except Exception: return "Reschedule failed due to a database error."

def show_availability(doctor_text: str, day_text: str) -> str:
    dref=_match_doctor_free(doctor_text or "")
    if not dref: return "I couldn't identify the doctor."
    day=_parse_date_only(day_text or "")
    if not day: return "I couldn't parse the date. Try '2025-10-12'."
    slots=_slots_for_day(dref.id, day)
    head=f"Available slots for Dr. {dref.name} ({dref.specialty}) on {day:%Y-%m-%d}"
    if not slots: return head+"\nNone"
    return head + "\n" + "\n".join(f"• {s}" for s in slots)

def tool_book_from_free_text(message: str, context: Dict[str,Any]) -> str:
    m=re.search(r"\bbook\b.*?(?:appointment\s+with\s+)?(?P<doc>dr\.?\s*[a-z0-9\-']+|doctor\s*\d+|[a-z][a-z\s\-']+?)\s+(?:on|at|for|,)?\s*(?P<when>[^,]+?)(?:\s+reason[:\-]\s*(?P<reason>.*))?$", message, re.I)
    if not m:
        rs=re.search(r"reason[:\-]\s*(.*)$", message, re.I); reason=rs.group(1).strip() if rs else ""
        w=_parse_date_time_hybrid(message); doc_guess=_match_doctor_free(message)
        if doc_guess and w: return book_appointment(int(context.get("user_id") or 0), context, doc_guess.name, message, reason)
        return "To book, say: `book appointment with doctor 3 on 2025-10-12 09:30 reason: checkup`."
    return book_appointment(int(context.get("user_id") or 0), context, m.group("doc"), m.group("when"), (m.group("reason") or "").strip())

def tool_cancel_from_free_text(message: str, context: Dict[str,Any]) -> str:
    mid=re.search(r"\b(cancel|delete)\b.*?\bappointment\b.*?(\d+)", message, re.I)
    if mid: return cancel_appointment(int(context.get("user_id") or 0), context, int(mid.group(2)), None, None)
    m2=re.search(r"\bcancel\b.*?(?:appointment\s+)?(?:with\s+)?(?P<doc>dr\.?\s*[a-z0-9\-']+|doctor\s*\d+|[a-z][a-z\s\-']+?)\s+(?:on|at|for)\s+(?P<when>.+)$", message, re.I)
    if m2: return cancel_appointment(int(context.get("user_id") or 0), context, None, m2.group("doc"), m2.group("when"))
    m3=re.search(r"\bcancel\b.*?(?P<when>today|tomorrow|tmr|tmrw|next\s+\w+).*?(?:with\s+)?(?P<doc>dr\.?\s*[a-z0-9\-']+|doctor\s*\d+|[a-z][a-z\s\-']+)", message, re.I)
    if m3: return cancel_appointment(int(context.get("user_id") or 0), context, None, m3.group("doc"), m3.group("when"))
    return "Please specify the appointment ID or the doctor and date/time to cancel."

def tool_reschedule_from_free_text(message: str, context: Dict[str,Any]) -> str:
    m=re.search(r"\b(reschedule|move)\b.*?\bappointment\b.*?(\d+).*?(?:to|->|new|on|at)\s+(.+)$", message, re.I)
    if m: return reschedule_appointment(int(context.get("user_id") or 0), context, int(m.group(2)), None, None, m.group(3).strip())
    m2=re.search(r"\b(reschedule|move)\b.*?(?:with\s+)?(?P<doc>dr\.?\s*[a-z0-9\-']+|doctor\s*\d+|[a-z][a-z\s\-']+).*(?:on|at)\s+(?P<old>.+?)\s+(?:to|->|new|at|on)\s+(?P<new>.+)$", message, re.I)
    if m2: return reschedule_appointment(int(context.get("user_id") or 0), context, None, m2.group("doc"), m2.group("old"), m2.group("new"))
    return "Please specify the appointment ID or the doctor + old time and new time."

def tool_availability_from_free_text(message: str) -> str:
    m=re.search(r"\b(availability|available|slots|times?)\b.*?(?:with\s+|for\s+)?(?P<doc>dr\.?\s*[a-z0-9\-']+|doctor\s*\d+|[a-z][a-z\s\-']+).*(?:on|at|for)\s+(?P<day>.+)$", message, re.I)
    if not m:
        parts=re.split(r"\bon\b", message, flags=re.I)
        if len(parts)>=2:
            doc_guess=_match_doctor_free(parts[0])
            if doc_guess: return show_availability(doc_guess.name, parts[1])
        return "Say: 'show availability for Dr Derek on 2025-10-12'."
    return show_availability(m.group("doc"), m.group("day"))
# ========= Intent detection (drop-in) =========

def _is_greeting(msg: str) -> bool:
    m = (msg or "").strip().lower()
    return m in {"hi", "hello", "hey", "yo", "hiya", "gday", "g'day"} or m.startswith(("hi ", "hello ", "hey "))

def _g(rx: re.Pattern, msg: str, group: Union[int, str], default: str = "") -> str:
    """
    Safe group extractor: returns default if no match/group.
    """
    try:
        m = rx.search(msg or "")
        if not m:
            return default
        val = m.group(group)
        return (val or "").strip()
    except Exception:
        return default

# Build intent table ONCE. Put specific patterns before generic to avoid shadowing.
_INTENT_PATTERNS: List[Tuple[str, re.Pattern, Callable[[Dict[str, Any], str], str]]] = [

    # ---- Advanced appointment intents (your 20) ----
    ("next_appt",
     re.compile(r"\b(next|upcoming)\b.*\bappointment\b", re.I),
     lambda ctx, msg: tool_next_appointment(int(ctx.get("user_id") or 0))),

    ("get_name",
    re.compile(r"\b(my|what\s+is\s+my)\s+name\b", re.I),
    lambda ctx, msg: tool_get_user_name(int(ctx.get("user_id") or 0))),
    ("on_date",
     re.compile(r"\bappointments?\b.*\b(on|for)\b\s+(.+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"\b(on|for)\b\s+(.+)", re.I):
        tool_appointments_on_date(int(ctx.get("user_id") or 0), _g(_rx, msg, 2))),

    ("between",
     re.compile(r"\bappointments?\b.*\bfrom\b(.+?)\bto\b(.+)", re.I),
     lambda ctx, msg, _rx_from=re.compile(r"\bfrom\b(.+?)\bto\b", re.I), _rx_to=re.compile(r"\bto\b(.+)", re.I):
        tool_appointments_between(int(ctx.get("user_id") or 0), _g(_rx_from, msg, 1), _g(_rx_to, msg, 1))),

    ("upcoming_count",
     re.compile(r"\bhow\s+many\b.*\b(upcoming|future)\b.*\bappointments?\b", re.I),
     lambda ctx, msg: tool_upcoming_count(int(ctx.get("user_id") or 0))),

    ("past_appts",
     re.compile(r"\b(past|previous)\b.*\bappointments?\b", re.I),
     lambda ctx, msg: tool_past_appointments(int(ctx.get("user_id") or 0))),

    ("by_doctor",
     re.compile(r"\bappointments?\b.*\bwith\b\s+(.+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"\bwith\b\s+(.+)", re.I):
        tool_appointments_by_doctor(int(ctx.get("user_id") or 0), _g(_rx, msg, 1))),

    ("by_specialty",
     re.compile(r"\bappointments?\b.*\b(cardiology|dermatology|orthopedics?|neurology|ent|trauma|pediatrics|general)\b", re.I),
     lambda ctx, msg, _rx=re.compile(r"(cardiology|dermatology|orthopedics?|neurology|ent|trauma|pediatrics|general)", re.I):
        tool_appointments_by_specialty(int(ctx.get("user_id") or 0), _g(_rx, msg, 1))),

    ("next_with_doc",
     re.compile(r"\bnext\b.*\bwith\b\s+(.+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"\bwith\b\s+(.+)", re.I):
        tool_next_with_doctor(int(ctx.get("user_id") or 0), _g(_rx, msg, 1))),

    ("first_avail_doc",
     re.compile(r"\b(first|earliest)\b.*\bwith\b\s+(.+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"\bwith\b\s+(.+)", re.I):
        tool_first_available_with_doctor(_g(_rx, msg, 1))),

    ("first_avail_spec",
     re.compile(r"\b(first|earliest)\b.*\b(cardiology|dermatology|orthopedics?|neurology|ent)\b", re.I),
     lambda ctx, msg, _rx=re.compile(r"(cardiology|dermatology|orthopedics?|neurology|ent)", re.I):
        tool_first_available_by_specialty(_g(_rx, msg, 1))),

    ("conflicts",
     re.compile(r"\b(conflicts?|double[-\s]?book(?:ed)?)\b.*\b(on|for)\b\s+(.+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"\b(on|for)\b\s+(.+)", re.I):
        tool_conflicts_on(int(ctx.get("user_id") or 0), _g(_rx, msg, 2))),

    ("search_appts",
     re.compile(r"\b(find|search)\b\s+['\"]?(.+?)['\"]?\s+\bappointments?\b", re.I),
     lambda ctx, msg, _rx=re.compile(r"\b(find|search)\b\s+['\"]?(.+?)['\"]?\s+\bappointments?\b", re.I):
        tool_search_appointments(int(ctx.get("user_id") or 0), _g(_rx, msg, 2))),

    ("details",
     re.compile(r"\bdetails?\b.*\bappointment\b.*?(\d+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"(\d+)", re.I):
        tool_appointment_details(int(ctx.get("user_id") or 0), int(_g(_rx, msg, 1) or 0))),

    ("cancel_next",
     re.compile(r"\bcancel\b.*\bnext\b.*\bappointment\b", re.I),
     lambda ctx, msg: tool_cancel_next(int(ctx.get("user_id") or 0))),

    ("resched_next",
     re.compile(r"\b(reschedul|move)\b.*\bnext\b.*\bappointment\b.*\b(to|at|on)\b\s+(.+)", re.I),
     lambda ctx, msg, _rx=re.compile(r"\b(to|at|on)\b\s+(.+)", re.I):
        tool_reschedule_next(int(ctx.get("user_id") or 0), _g(_rx, msg, 2))),

    ("day_avail_doc",
     re.compile(r"\b(availability|slots|times?)\b.*\bfor\b\s+(.+?)\s+\b(on|for)\b\s+(.+)", re.I),
     lambda ctx, msg, _rx_doc=re.compile(r"\bfor\b\s+(.+?)\s+\b(on|for)\b", re.I),
                    _rx_day=re.compile(r"\b(on|for)\b\s+(.+)", re.I):
        tool_day_availability_for_doctor(_g(_rx_doc, msg, 1), _g(_rx_day, msg, 2))),

    ("week_view",
     re.compile(r"\b(this|next)\s+week\b.*\bappointments?\b", re.I),
     lambda ctx, msg, _rx=re.compile(r"(this|next)\s+week", re.I):
        tool_week_view(int(ctx.get("user_id") or 0), _g(_rx, msg, 0))),

    ("by_status",
     re.compile(r"\b(show|list)\b.*\b(cancelled|canceled|completed|booked)\b.*\bappointments?\b", re.I),
     lambda ctx, msg, _rx=re.compile(r"(cancelled|canceled|completed|booked)", re.I):
        tool_appointments_by_status(int(ctx.get("user_id") or 0), _g(_rx, msg, 1))),

    ("near_time",
     re.compile(r"\b(around|about|near)\b\s+(.+?)\b.*\bappointments?\b", re.I),
     lambda ctx, msg, _rx=re.compile(r"\b(around|about|near)\b\s+(.+?)\b", re.I):
        tool_appointments_near_time(int(ctx.get("user_id") or 0), _g(_rx, msg, 2))),

    ("summary_card",
     re.compile(r"\b(summary|overview|digest)\b.*\bappointments?\b", re.I),
     lambda ctx, msg: tool_appointments_summary_card(int(ctx.get("user_id") or 0))),

    # ---- Base intents (generic) ----
    ("list_doctors",
     re.compile(r"\b(list|show|see|view)\b.*\bdoctors?\b", re.I),
     lambda ctx, msg: tool_list_doctors()),

    ("my_appointments",
     re.compile(r"\b(my|show|list|view|booking|bookings)\b.*\b(appointment|booking)", re.I),
     lambda ctx, msg: tool_my_appointments(int(ctx.get("user_id") or 0))),

    ("prescriptions",
     re.compile(r"\b(list|show|view)\b.*\b(prescriptions?|rx)\b", re.I),
     lambda ctx, msg: tool_list_prescriptions(int(ctx.get("user_id") or 0))),

    ("billing",
     re.compile(r"\b(billing|bills|invoices|payments?)\b", re.I),
     lambda ctx, msg: tool_list_billing(int(ctx.get("user_id") or 0))),

    ("notifications",
     re.compile(r"\b(notifications?|alerts?|messages|reminders)\b", re.I),
     lambda ctx, msg: tool_list_notifications(int(ctx.get("user_id") or 0))),

    ("book",
     re.compile(r"\b(book|make)\b.*\bappointment\b", re.I),
     lambda ctx, msg: tool_book_from_free_text(msg, ctx)),

    ("cancel",
     re.compile(r"\b(cancel|delete)\b.*\bappointment\b", re.I),
     lambda ctx, msg: tool_cancel_from_free_text(msg, ctx)),

    ("reschedule",
     re.compile(r"\b(reschedule|move)\b.*\bappointment\b", re.I),
     lambda ctx, msg: tool_reschedule_from_free_text(msg, ctx)),

    ("availability",
     re.compile(r"\b(availability|available|slots|times?)\b.*\b(dr|doctor|\d+|[a-z])", re.I),
     lambda ctx, msg: tool_availability_from_free_text(msg)),
]

def route_intent(message: str, context: Dict[str, Any], allow_tools: bool = True) -> Tuple[Optional[str], Optional[str]]:
    """
    Try patterns in order; on first hit, call its handler with (context, message).
    Returns (answer, intent_name) or (None, None) when no match.
    """
    if not allow_tools:
        return (None, None)
    msg = (message or "").strip()
    for name, rx, handler in _INTENT_PATTERNS:
        if rx.search(msg):
            try:
                ans = handler(context, msg)
                # normalize/clean just in case a tool returned multi-line+extra spaces
                return (_clean(str(ans)), name)
            except Exception as e:
                log.exception("Intent '%s' failed: %s", name, e)
                return (f"An error occurred while handling '{name}'.", name)
    return (None, None)


class ChatIn(BaseModel):
    user_id: int = 0
    session_id: str = "sess-local"
    message: str = Field(..., min_length=1)
    context: Dict[str, Any] = Field(default_factory=dict)
    allow_tools: bool = True

class ChatOut(BaseModel):
    answer: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

def _enrich_context(inp: ChatIn) -> ChatIn:
    ctx=dict(inp.context or {})
    uid=int(ctx.get("user_id") or inp.user_id or 0)
    if uid and "patient_id" not in ctx:
        pid=derive_patient_id(uid)
        if pid: ctx["patient_id"]=int(pid)
    return ChatIn(user_id=inp.user_id, session_id=inp.session_id, message=inp.message, context=ctx, allow_tools=inp.allow_tools)
def _status_str(v: Any) -> str:
    if v is None: return ""
    try:
        return getattr(v, "value") if hasattr(v, "value") else str(v)
    except Exception:
        return str(v)

def tool_my_appointments_recent1(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id` in context to list your appointments."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                a = db.scalars(
                    select(Appointment).where(Appointment.patient_id==pid).order_by(Appointment.scheduled_for.desc()).limit(1)
                ).first()
                if not a: return "You have no appointments."
                when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                doc = "-"
                if a.doctor_id:
                    d = db.get(Doctor, a.doctor_id)
                    u = db.get(User, getattr(d,"user_id",0)) if d else None
                    doc = f"Dr. {(u.full_name or u.email) if u else ('#'+str(a.doctor_id))} ({(d.specialty or 'General') if d else ''})"
                return _format_table([(a.id, when, doc, a.reason or "", _status_str(getattr(a, "status", "")))],
                                     ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        with _sqlite_conn() as c:
            r = c.execute("""
              SELECT a.id as ID,
                     COALESCE(strftime('%Y-%m-%d %H:%M', a.scheduled_for),'') as "When",
                     COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                     COALESCE(a.reason,'') as Reason,
                     COALESCE(a.status,'') as Status
              FROM appointment a
              LEFT JOIN doctor d ON d.id=a.doctor_id
              LEFT JOIN user u   ON u.id=d.user_id
              WHERE a.patient_id=?
              ORDER BY a.scheduled_for DESC
              LIMIT 1
            """,(pid,)).fetchall()
            if not r: return "You have no appointments."
            return _rows_to_table(r, ["ID","When","Doctor","Reason","Status"])
    return "_No data found._"

def tool_count_appointments(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                from sqlalchemy import func
                n = db.scalar(select(func.count(Appointment.id)).where(Appointment.patient_id==pid)) or 0
                return f"You have {int(n)} appointments."
        except Exception: pass
    if _db_exists():
        with _sqlite_conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM appointment WHERE patient_id=?", (pid,)).fetchone()["n"]
            return f"You have {int(n)} appointments."
    return "You have 0 appointments."

def tool_appointments_in_month(user_id: int, month_name_or_num: str, year: Optional[int]) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    mtxt = month_name_or_num.strip().lower()
    months = { 'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'sept':9,'oct':10,'nov':11,'dec':12 }
    if mtxt.isdigit():
        month = int(mtxt)
    else:
        month = months.get(mtxt[:3], None)
    if not month or not (1 <= month <= 12): return "I couldn't parse the month."
    today = datetime.now()
    y = year or today.year
    start = datetime(y, month, 1, 0, 0, 0)
    if month == 12:
        end = datetime(y+1, 1, 1, 0, 0, 0)
    else:
        end = datetime(y, month+1, 1, 0, 0, 0)
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                appts = db.scalars(
                    select(Appointment).where(Appointment.patient_id==pid, Appointment.scheduled_for>=start, Appointment.scheduled_for<end).order_by(Appointment.scheduled_for.desc())
                ).all()
                if not appts: return f"No appointments in {y}-{month:02d}."
                rows=[]
                for a in appts:
                    when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                    doc="-"
                    if a.doctor_id:
                        d=db.get(Doctor,a.doctor_id); u=db.get(User,getattr(d,"user_id",0)) if d else None
                        doc=f"Dr. {(u.full_name or u.email) if u else ('#'+str(a.doctor_id))} ({(d.specialty or 'General') if d else ''})"
                    rows.append((a.id, when, doc, a.reason or "", _status_str(getattr(a,"status",""))))
                return _format_table(rows, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        with _sqlite_conn() as c:
            rows=c.execute("""
              SELECT a.id as ID,
                     COALESCE(strftime('%Y-%m-%d %H:%M', a.scheduled_for),'') as "When",
                     COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                     COALESCE(a.reason,'') as Reason,
                     COALESCE(a.status,'') as Status
              FROM appointment a
              LEFT JOIN doctor d ON d.id=a.doctor_id
              LEFT JOIN user u   ON u.id=d.user_id
              WHERE a.patient_id=? AND a.scheduled_for>=? AND a.scheduled_for<?
              ORDER BY a.scheduled_for DESC
            """,(pid, start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))).fetchall()
            if not rows: return f"No appointments in {y}-{month:02d}."
            return _rows_to_table(rows, ["ID","When","Doctor","Reason","Status"])
    return "_No data found._"

def tool_appointments_heart(user_id: int) -> str:
    pid = derive_patient_id(user_id)
    if not pid: return "I need your `patient_id`."
    heart_specs = {"cardiology","cardiothoracic","cardiac","cardiovascular"}
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                rows = db.execute(
                    select(Appointment, Doctor, User)
                    .join(Doctor, Appointment.doctor_id==Doctor.id, isouter=True)
                    .join(User, Doctor.user_id==User.id, isouter=True)
                    .where(Appointment.patient_id==pid)
                    .order_by(Appointment.scheduled_for.desc())
                ).all()
                out=[]
                for a,d,u in rows:
                    spec=(getattr(d,"specialty","") or "").lower()
                    if any(k in spec for k in heart_specs):
                        when = a.scheduled_for.strftime("%Y-%m-%d %H:%M") if a.scheduled_for else ""
                        doc = f"Dr. {(getattr(u,'full_name',None) or getattr(u,'email',None) or f'#{getattr(d,'id',None)}')} ({getattr(d,'specialty','General') or 'General'})"
                        out.append((a.id, when, doc, a.reason or "", _status_str(getattr(a,"status",""))))
                if not out: return "No heart-related appointments found."
                return _format_table(out, ["ID","When","Doctor","Reason","Status"])
        except Exception: pass
    if _db_exists():
        with _sqlite_conn() as c:
            rows=c.execute("""
              SELECT a.id as ID,
                     COALESCE(strftime('%Y-%m-%d %H:%M', a.scheduled_for),'') as "When",
                     COALESCE('Dr. '||COALESCE(u.full_name,u.email),'Doctor#'||a.doctor_id) as Doctor,
                     COALESCE(d.specialty,'General') as Specialty,
                     COALESCE(a.reason,'') as Reason,
                     COALESCE(a.status,'') as Status
              FROM appointment a
              LEFT JOIN doctor d ON d.id=a.doctor_id
              LEFT JOIN user   u ON u.id=d.user_id
              WHERE a.patient_id=?
              ORDER BY a.scheduled_for DESC
            """,(pid,)).fetchall()
            out=[]
            for r in rows:
                if any(k in (r["Specialty"] or "").lower() for k in heart_specs):
                    out.append((r["ID"], r["When"], f'{r["Doctor"]} ({r["Specialty"]})', r["Reason"], r["Status"]))
            if not out: return "No heart-related appointments found."
            return _format_table(out, ["ID","When","Doctor","Reason","Status"])
    return "_No data found._"

def tool_count_doctors() -> str:
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                from sqlalchemy import func
                n = db.scalar(select(func.count(Doctor.id))) or 0
                return f"There are {int(n)} doctors."
        except Exception: pass
    if _db_exists():
        with _sqlite_conn() as c:
            n=c.execute("SELECT COUNT(*) AS n FROM doctor").fetchone()["n"]
            return f"There are {int(n)} doctors."
    return "There are 0 doctors."

def tool_list_doctor_names() -> str:
    if _HAS_SQLA and SessionLocal:
        try:
            with SessionLocal() as db:
                rows=db.execute(select(Doctor.id, User.full_name, User.email).join(User, Doctor.user_id==User.id, isouter=True).order_by(Doctor.id.asc())).all()
                names=[(did, (fn or em or f"Doctor#{did}")) for did,fn,em in rows]
                return _format_table(names, ["ID","Name"])
        except Exception: pass
    if _db_exists():
        with _sqlite_conn() as c:
            rows=c.execute("""SELECT d.id as ID, COALESCE(u.full_name,u.email,'Doctor#'||d.id) as Name FROM doctor d LEFT JOIN user u ON u.id=d.user_id ORDER BY d.id ASC""").fetchall()
            return _rows_to_table(rows, ["ID","Name"])
    return "_No data found._"


def route_intent(message: str, context: Dict[str,Any], allow_tools: bool=True) -> Tuple[Optional[str], Optional[str]]:
    if not allow_tools: return (None,None)
    for name, rx, handler in _INTENT_PATTERNS:
        if rx.search(message):
            try: return (_clean(handler(context, message)), name)
            except Exception as e: log.exception("Tool %s failed: %s", name, e); return (f"An error occurred while handling '{name}'.", name)
    return (None, None)

app = FastAPI(title="Care Portal AI Server", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"] if CORS_ALLOW=="*" else [CORS_ALLOW], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.exception_handler(Exception)
def on_error(request: Request, exc: Exception):
    log.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail":"Internal server error"})

@app.on_event("startup")
def _startup():
    log.info("AI Server starting on %s:%s", HOST, PORT)
    if USE_LLM: ensure_llm()

@app.on_event("shutdown")
def _shutdown():
    log.info("AI Server stopping.")

@app.get("/ai/health")
def ai_health():
    return {"ok":True,"db_detected":bool(_db_exists()),"db_path":str(DB_PATH),"tools":[n for (n,_,_) in _INTENT_PATTERNS],"llm_enabled":bool(USE_LLM),"llm_loaded":bool(_HAS_LLAMA),"model":"TinyLlama.gguf" if _HAS_LLAMA else "disabled","time":utcnow().isoformat()}

@app.post("/ai/chat", response_model=ChatOut)
def ai_chat(inp: ChatIn = Body(...)):
    inp=_enrich_context(inp)
    try:
        if _is_greeting(inp.message): return ChatOut(answer="Hi! How can I help with your Care Portal today?", metadata={"greeting":True})
        if re.search(r"\b(book|make)\b.*\bappointment\b", inp.message, re.I) and not re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}", inp.message):
            return ChatOut(answer="To book, say: `book appointment with doctor 3 on 2025-10-12 09:30 reason: checkup`.", metadata={"hint":"booking_format"})
        ans,intent=route_intent(inp.message, inp.context, allow_tools=inp.allow_tools)
        if ans is not None: return ChatOut(answer=ans, metadata={"tool":True,"intent":intent})
        if USE_LLM: return ChatOut(answer=_clean(llm_answer(inp.message)), metadata={"tool":False,"model":"tinyllama"})
        return ChatOut(answer="LLM disabled. Try: 'list doctors', 'my appointments', 'prescriptions', 'billing', or 'notifications'.", metadata={"tool":False,"model":"disabled"})
    except Exception as e:
        log.exception("ai_chat error: %s", e); raise HTTPException(500, "Internal error in /ai/chat")

@app.post("/ai/stream")
def ai_stream(inp: ChatIn = Body(...)):
    inp=_enrich_context(inp)
    def emit(text: str, n: int=120):
        text=_clean(text)
        for i in range(0,len(text),n):
            yield "data: "+json.dumps({"type":"token","text":text[i:i+n]})+"\n\n"; time.sleep(0.012)
    def gen():
        try:
            yield "data: "+json.dumps({"type":"start"})+"\n\n"
            if _is_greeting(inp.message):
                yield from emit("Hi! How can I help with your Care Portal today?")
                yield "data: "+json.dumps({"type":"end"})+"\n\n"; return
            if re.search(r"\b(book|make)\b.*\bappointment\b", inp.message, re.I) and not re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}", inp.message):
                yield from emit("To book, say: `book appointment with doctor 3 on 2025-10-12 09:30 reason: checkup`.")
                yield "data: "+json.dumps({"type":"end"})+"\n\n"; return
            ans,intent=route_intent(inp.message, inp.context, allow_tools=inp.allow_tools)
            if ans is not None:
                yield from emit(ans, 160); yield "data: "+json.dumps({"type":"end","intent":intent,"tool":True})+"\n\n"; return
            if USE_LLM and ensure_llm():
                txt=llm_answer(inp.message); yield from emit(txt)
                yield "data: "+json.dumps({"type":"end","tool":False,"model":"tinyllama"})+"\n\n"; return
            else:
                yield from emit("LLM disabled. Try: 'list doctors', 'my appointments', 'prescriptions', 'billing', or 'notifications'.")
                yield "data: "+json.dumps({"type":"end","tool":False,"model":"disabled"})+"\n\n"; return
        except Exception as e:
            log.exception("/ai/stream error: %s", e)
            yield "data: "+json.dumps({"type":"token","text":"(stream failed)"})+"\n\n"
            yield "data: "+json.dumps({"type":"end"})+"\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.post("/chat/session/reset")
def reset_session():
    return {"ok": True, "ts": utcnow().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
