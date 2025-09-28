# care_portal/ui/receptionist.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta, date

# Calendar support (used for availability painting & picking)
try:
    from tkcalendar import DateEntry, Calendar
    HAS_TKCAL = True
except Exception:
    HAS_TKCAL = False
    DateEntry = Calendar = None  # type: ignore

from sqlalchemy import select, func, and_, or_, delete, cast, String
from sqlalchemy.orm import joinedload, selectinload

from ..db import SessionLocal
from ..models import (
    User, Patient, Doctor,
    Appointment, AppointmentStatus,
    Attendance, AttendanceMethod,
    DoctorAvailability,
    MedicalRecord, Prescription,
    SupportTicket, TicketStatus,
    Notification,
)

# Optional billing models — work even if missing
try:
    from ..models import Invoice, InvoiceItem, Payment
except Exception:
    Invoice = InvoiceItem = Payment = None  # type: ignore

# Optional staff check-in models
try:
    from ..models import StaffCheckin, StaffCheckinStatus, StaffCheckinMethod
except Exception:
    StaffCheckin = StaffCheckinStatus = StaffCheckinMethod = None  # type: ignore

# Services
from ..services.appointments import AppointmentService
try:
    from ..services.checkin import today_checkins  # returns rows with .user eager-loaded
except Exception:
    today_checkins = None  # type: ignore

DATE_FMT = "%Y-%m-%d %H:%M"
DAY_FMT  = "%Y-%m-%d"
TIME_FMT = "%H:%M"

# Calendar coloring
LIGHT_GREEN = "#c9f7d4"
LIGHT_RED   = "#ffd9d9"

# ---------- helpers ----------
def _today_range():
    now = datetime.utcnow()
    d0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return d0, d0 + timedelta(days=1)

def _status_value(v):
    return getattr(v, "value", v)

def _is_checkin(st) -> bool:
    sv = (_status_value(st) or "").replace("-", "_")
    return sv in {"in", "checked_in", "checkin"}

def _is_checkout(st) -> bool:
    sv = (_status_value(st) or "").replace("-", "_")
    return sv in {"out", "checked_out", "checkout", "checkedout", "auto", "skipped"}

def _checkout_enum_value():
    if StaffCheckinStatus is None:
        return "checked_out"
    for name in ("checked_out", "auto", "skipped"):
        try:
            return getattr(StaffCheckinStatus, name)
        except Exception:
            pass
    return "checked_out"

def _allowed_statuses():
    if StaffCheckinStatus is None:
        return ("checked_in", "checked_out", "auto", "skipped")
    vals = []
    for nm in ("checked_in", "checked_out", "auto", "skipped"):
        try:
            vals.append(getattr(StaffCheckinStatus, nm))
        except Exception:
            pass
    return vals or [getattr(StaffCheckinStatus, "checked_in")]

# ======================================================================
# Receptionist UI
# ======================================================================
class ReceptionistFrame(ttk.Frame):
    title = "Receptionist Dashboard"

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        # booking calendar caches
        # (availability paint period)
        self._available_dates: set[str] = set()
        self._date_window_days = 90

        # Header
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(4, 2))
        ttk.Button(top, text="Profile", command=self._open_profile_dialog).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="Refresh All", command=self._refresh_all).pack(side="right")

        # Notebook
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)

        self.tab_staff   = ttk.Frame(self.nb)
        self.tab_sched   = ttk.Frame(self.nb)
        self.tab_book    = ttk.Frame(self.nb)
        self.tab_req     = ttk.Frame(self.nb)
        self.tab_bill    = ttk.Frame(self.nb)
        self.tab_pat     = ttk.Frame(self.nb)
        self.tab_notif   = ttk.Frame(self.nb)
        self.tab_support = ttk.Frame(self.nb)

        self.nb.add(self.tab_staff,   text="Staff")
        self.nb.add(self.tab_sched,   text="Schedule")
        self.nb.add(self.tab_book,    text="Book")
        self.nb.add(self.tab_req,     text="Requests")
        self.nb.add(self.tab_bill,    text="Billing")
        self.nb.add(self.tab_pat,     text="Patients")
        self.nb.add(self.tab_notif,   text="Notifications")
        self.nb.add(self.tab_support, text="Support")

        # Build tabs
        self._build_staff_tab()
        self._build_schedule_tab()
        self._build_book_tab()
        self._build_requests_tab()
        self._build_billing_tab()
        self._build_patients_tab()
        self._build_notifications_tab()
        self._build_support_tab()

        # Doctor/Patient choices for filters & booking
        self._load_reference_data()

        # Initial content
        self._refresh_all()

    # ------------------------------------------------------
    # Reference data (doctors/patients for dropdowns)
    # ------------------------------------------------------
    def _load_reference_data(self):
        self._doc_by_label: dict[str, int] = {}
        self._pat_by_label: dict[str, int] = {}
        self.doc_choices: list[str] = []
        self.patient_choices: list[str] = []

        with SessionLocal() as db:
            docs = db.scalars(select(Doctor).options(selectinload(Doctor.user)).order_by(Doctor.id.asc())).all()
            for d in docs:
                u = d.user
                label = f"{(u.full_name or u.email or f'Doctor#{d.id}')} • #{d.id}"
                self._doc_by_label[label] = d.id
                self.doc_choices.append(label)

            pats = db.scalars(select(Patient).options(selectinload(Patient.user)).order_by(Patient.id.asc())).all()
            for p in pats:
                u = p.user
                label = f"{(u.full_name or u.email or f'Patient#{p.id}')} • {u.email or ''} • MRN:{getattr(p,'mrn',p.id)}"
                self._pat_by_label[label] = p.id
                self.patient_choices.append(label)

        # refresh combos if already built
        if hasattr(self, "b_doc"):
            self.b_doc["values"] = self.doc_choices
        if hasattr(self, "b_patient"):
            self.b_patient["values"] = self.patient_choices
        if hasattr(self, "s_doc"):
            self.s_doc["values"] = ["(any)"] + self.doc_choices
        if hasattr(self, "s_pat"):
            self.s_pat["values"] = ["(any)"] + self.patient_choices

        # repaint book calendar & times
        try:
            self._recompute_available_dates()
            self._maybe_jump_to_next_available_date()
            self._refresh_book_slots()
        except Exception:
            pass

    # ------------------------------------------------------
    # Staff tab
    # ------------------------------------------------------
    def _build_staff_tab(self):
        wrap = ttk.Frame(self.tab_staff, padding=8)
        wrap.pack(fill="both", expand=True)

        you = ttk.LabelFrame(wrap, text="My Check-in", padding=8)
        you.pack(fill="x")
        ttk.Button(you, text="Check-in (Now)", command=self._my_checkin).pack(side="left")
        ttk.Button(you, text="Check-out (Now)", command=self._my_checkout).pack(side="left", padx=(6, 0))

        box = ttk.LabelFrame(wrap, text="Today’s Staff Check-ins", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))

        cols = ("when", "name", "role", "status", "method", "location", "worked")
        self.staff_tv = ttk.Treeview(box, columns=cols, show="headings", height=9)
        heads = {
            "when": ("When", 110),
            "name": ("Name", 220),
            "role": ("Role", 110),
            "status": ("Status", 110),
            "method": ("Method", 110),
            "location": ("Location", 150),
            "worked": ("Worked Today", 120),
        }
        for c in cols:
            title, w = heads[c]
            self.staff_tv.heading(c, text=title)
            self.staff_tv.column(c, width=w, anchor="center" if c != "name" else "w")
        self.staff_tv.pack(fill="both", expand=True)

        tools = ttk.Frame(wrap)
        tools.pack(fill="x", pady=(6, 0))
        ttk.Button(tools, text="Refresh", command=self._refresh_staff).pack(side="left")

    def _my_checkin(self):
        if StaffCheckin is None:
            messagebox.showerror("Unavailable", "Staff check-in model not available.")
            return
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        with SessionLocal() as db:
            d0, d1 = _today_range()
            open_row = db.scalar(
                select(StaffCheckin)
                .where(
                    StaffCheckin.user_id == user.id,
                    StaffCheckin.ts >= d0, StaffCheckin.ts < d1,
                    StaffCheckin.status.in_(_allowed_statuses())
                )
                .order_by(StaffCheckin.id.desc())
            )
            if open_row and _is_checkin(getattr(open_row, "status", None)):
                messagebox.showinfo("Already checked-in", "You are already checked-in. Please check-out first.")
                return
            db.add(StaffCheckin(
                user_id=user.id,
                role=getattr(user, "role", None),
                status=getattr(StaffCheckinStatus, "checked_in", "checked_in"),
                method=StaffCheckinMethod.login if StaffCheckinMethod else None,
                ts=datetime.utcnow(),
                location="Onsite",
            ))
            db.commit()
        self._refresh_staff()

    def _my_checkout(self):
        if StaffCheckin is None:
            messagebox.showerror("Unavailable", "Staff check-in model not available.")
            return
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        with SessionLocal() as db:
            d0, d1 = _today_range()
            last = db.scalar(
                select(StaffCheckin)
                .where(
                    StaffCheckin.user_id == user.id,
                    StaffCheckin.ts >= d0, StaffCheckin.ts < d1,
                    StaffCheckin.status.in_(_allowed_statuses())
                )
                .order_by(StaffCheckin.id.desc())
            )
            if not last or not _is_checkin(getattr(last, "status", None)):
                messagebox.showinfo("No open check-in", "You are not currently checked-in.")
                return
            db.add(StaffCheckin(
                user_id=user.id,
                role=getattr(user, "role", None),
                status=_checkout_enum_value(),
                method=StaffCheckinMethod.login if StaffCheckinMethod else None,
                ts=datetime.utcnow(),
                location="Onsite",
            ))
            db.commit()
        self._refresh_staff()

    def _refresh_staff(self):
        for i in self.staff_tv.get_children():
            self.staff_tv.delete(i)

        rows = []
        if today_checkins:
            try:
                rows = today_checkins()
            except Exception:
                rows = []
        elif StaffCheckin is not None:
            with SessionLocal() as db:
                d0, d1 = _today_range()
                rows = db.scalars(
                    select(StaffCheckin)
                    .options(selectinload(StaffCheckin.user))
                    .where(
                        StaffCheckin.ts >= d0, StaffCheckin.ts < d1,
                        StaffCheckin.status.in_(_allowed_statuses())
                    )
                    .order_by(StaffCheckin.ts.asc())
                ).all()

        worked = {}
        last_in = {}
        for r in rows:
            uid = r.user_id
            if _is_checkin(getattr(r, "status", None)):
                last_in[uid] = r.ts
            elif _is_checkout(getattr(r, "status", None)):
                if uid in last_in:
                    mins = int((r.ts - last_in[uid]).total_seconds() // 60)
                    worked[uid] = worked.get(uid, 0) + max(0, mins)
                    last_in.pop(uid, None)

        now = datetime.utcnow()
        for uid, ts_in in last_in.items():
            mins = int((now - ts_in).total_seconds() // 60)
            worked[uid] = worked.get(uid, 0) + max(0, mins)

        for r in rows:
            who = getattr(r.user, "full_name", None) or getattr(r.user, "email", "Unknown")
            role = getattr(getattr(r.user, "role", None), "value", getattr(r.user, "role", "-"))
            wmins = worked.get(r.user_id, 0)
            hh, mm = divmod(wmins, 60)
            self.staff_tv.insert(
                "", "end",
                values=(
                    r.ts.strftime("%H:%M"),
                    who, role,
                    getattr(r.status, "value", r.status),
                    getattr(r.method, "value", r.method),
                    getattr(r, "location", "") or "",
                    f"{hh:02d}:{mm:02d}"
                )
            )

    # ------------------------------------------------------
    # Schedule tab
    # ------------------------------------------------------
    def _build_schedule_tab(self):
        wrap = ttk.Frame(self.tab_sched, padding=6)
        wrap.pack(fill="both", expand=True)

        # Filters
        left = ttk.LabelFrame(wrap, text="Filters", padding=8)
        left.pack(side="left", fill="y")

        ttk.Label(left, text="Date").grid(row=0, column=0, sticky="w")
        if HAS_TKCAL:
            self.s_date = DateEntry(left, width=16, date_pattern="yyyy-mm-dd")
        else:
            self.s_date = ttk.Entry(left, width=18)
            self.s_date.insert(0, datetime.now().strftime(DAY_FMT))
        self.s_date.grid(row=1, column=0, sticky="ew", pady=(2, 2))

        self.s_all_dates = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="All dates", variable=self.s_all_dates, command=self._refresh_schedule)\
            .grid(row=2, column=0, sticky="w", pady=(0, 6))

        ttk.Label(left, text="Doctor").grid(row=3, column=0, sticky="w")
        self.s_doc = ttk.Combobox(left, width=24, values=["(any)"] + getattr(self, "doc_choices", []))
        self.s_doc.grid(row=4, column=0, sticky="ew", pady=(2, 6))
        self.s_doc.set("(any)")

        ttk.Label(left, text="Patient").grid(row=5, column=0, sticky="w")
        self.s_pat = ttk.Combobox(left, width=24, values=["(any)"] + getattr(self, "patient_choices", []))
        self.s_pat.grid(row=6, column=0, sticky="ew", pady=(2, 6))
        self.s_pat.set("(any)")

        ttk.Label(left, text="Status").grid(row=7, column=0, sticky="w")
        self.s_status = ttk.Combobox(left, state="readonly",
                                     values=["(any)", "booked", "completed", "cancelled", "requested"])
        self.s_status.current(0)
        self.s_status.grid(row=8, column=0, sticky="ew", pady=(2, 6))

        ttk.Button(left, text="↻ Refresh lists", command=self._load_reference_data).grid(row=9, column=0, sticky="ew", pady=(4, 2))
        ttk.Button(left, text="Refresh", command=self._refresh_schedule).grid(row=10, column=0, sticky="ew", pady=(4, 0))

        # Table
        cols = ("id", "time", "doctor", "patient", "reason", "status", "checked_in")
        mid = ttk.Frame(wrap)
        mid.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.tv_sched = ttk.Treeview(mid, columns=cols, show="headings", height=16)
        heads = {
            "id": ("Appt ID", 70), "time": ("Time", 130), "doctor": ("Doctor", 200),
            "patient": ("Patient", 220), "reason": ("Reason", 260),
            "status": ("Status", 100), "checked_in": ("Checked-in", 90)
        }
        for c in cols:
            t, w = heads[c]
            self.tv_sched.heading(c, text=t)
            self.tv_sched.column(c, width=w)
        self.tv_sched.pack(fill="both", expand=True)

        # Actions
        btns = ttk.Frame(mid)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Check-in", command=self._sched_checkin).pack(side="left")
        ttk.Button(btns, text="Undo Check-in", command=self._sched_undo_checkin).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self._sched_cancel).pack(side="left", padx=6)
        ttk.Button(btns, text="Open Patient", command=self._sched_open_patient).pack(side="left", padx=6)

    def _refresh_schedule(self):
        for i in self.tv_sched.get_children():
            self.tv_sched.delete(i)

        use_all_dates = bool(self.s_all_dates.get())
        day0 = day1 = None
        if not use_all_dates:
            date_str = self.s_date.get() if not HAS_TKCAL else self.s_date.get_date().strftime(DAY_FMT)
            try:
                day0 = datetime.strptime(date_str, DAY_FMT)
                day1 = day0 + timedelta(days=1)
            except ValueError:
                messagebox.showerror("Invalid date", "Use YYYY-MM-DD")
                return

        doc_label = (self.s_doc.get() or "").strip()
        pat_label = (self.s_pat.get() or "").strip()
        doc_id = self._doc_by_label.get(doc_label) if doc_label and doc_label != "(any)" else None
        pat_id = self._pat_by_label.get(pat_label) if pat_label and pat_label != "(any)" else None
        status = (self.s_status.get() or "").strip()

        with SessionLocal() as db:
            stmt = (
                select(Appointment)
                .options(
                    selectinload(Appointment.patient).selectinload(Patient.user),
                    selectinload(Appointment.doctor).selectinload(Doctor.user),
                )
                .order_by(Appointment.scheduled_for.asc())
            )
            if not use_all_dates and day0 and day1:
                stmt = stmt.where(Appointment.scheduled_for >= day0, Appointment.scheduled_for < day1)
            if doc_id:
                stmt = stmt.where(Appointment.doctor_id == doc_id)
            if pat_id:
                stmt = stmt.where(Appointment.patient_id == pat_id)
            if status and status != "(any)":
                try:
                    stmt = stmt.where(Appointment.status == AppointmentStatus(status))
                except Exception:
                    stmt = stmt.where(func.lower(Appointment.status) == status.lower())

            rows = db.scalars(stmt).all()
            ap_ids = [a.id for a in rows]
            checked = set()
            if ap_ids:
                checked = set(
                    db.scalars(
                        select(Attendance.appointment_id).where(Attendance.appointment_id.in_(ap_ids))
                    ).all()
                )

            total = 0
            for a in rows:
                doc_name = (a.doctor.user.full_name if a.doctor and a.doctor.user else None) or f"Dr#{a.doctor_id}"
                pat_name = (a.patient.user.full_name if a.patient and a.patient.user else None) or f"Pt#{a.patient_id}"
                self.tv_sched.insert(
                    "", "end",
                    values=(
                        a.id,
                        a.scheduled_for.strftime(DATE_FMT),
                        doc_name,
                        pat_name,
                        a.reason or "",
                        getattr(a.status, "value", a.status),
                        "yes" if a.id in checked else ""
                    )
                )
                total += 1

        self.nb.tab(self.tab_sched, text=f"Schedule ({total})" if total else "Schedule")

    def _select_appt_in_schedule(self, appt_id: int) -> bool:
        """Select a row by appointment id in the schedule table; returns True if found."""
        for iid in self.tv_sched.get_children():
            vals = self.tv_sched.item(iid, "values")
            if not vals:
                continue
            try:
                if int(vals[0]) == int(appt_id):
                    self.tv_sched.selection_set(iid)
                    self.tv_sched.see(iid)
                    return True
            except Exception:
                pass
        return False

    def _sel_sched_id(self):
        sel = self.tv_sched.selection()
        if not sel:
            return None
        return int(self.tv_sched.item(sel[0], "values")[0])

    def _sched_checkin(self):
        ap_id = self._sel_sched_id()
        if not ap_id:
            return
        with SessionLocal() as db:
            db.add(Attendance(appointment_id=ap_id, checkin_method=AttendanceMethod.web))
            db.commit()
        self._refresh_schedule()

    def _sched_undo_checkin(self):
        ap_id = self._sel_sched_id()
        if not ap_id:
            return
        with SessionLocal() as db:
            db.execute(delete(Attendance).where(Attendance.appointment_id == ap_id))
            db.commit()
        self._refresh_schedule()

    def _sched_cancel(self):
        ap_id = self._sel_sched_id()
        if not ap_id:
            return
        if not messagebox.askyesno("Cancel", "Cancel this appointment?"):
            return
        with SessionLocal() as db:
            a = db.get(Appointment, ap_id)
            if a:
                a.status = AppointmentStatus.cancelled
                db.commit()
        self._refresh_schedule()

    def _sched_open_patient(self):
        ap_id = self._sel_sched_id()
        if not ap_id:
            return
        with SessionLocal() as db:
            a = db.get(Appointment, ap_id)
            if not a:
                return
            p = db.get(Patient, a.patient_id)
            if not p:
                return
            u = db.get(User, p.user_id)
        info = (
            f"Name: {(u.full_name or u.email) if u else '-'}\n"
            f"MRN: {getattr(p,'mrn','-')}\nDOB: {getattr(p,'dob','-')}\n"
            f"Allergies: {getattr(p,'allergies','')}\nConditions: {getattr(p,'chronic_conditions','')}\n"
        )
        messagebox.showinfo("Patient", info)

    # ------------------------------------------------------
    # Book tab
    # ------------------------------------------------------
    def _build_book_tab(self):
        wrap = ttk.Frame(self.tab_book, padding=8)
        wrap.pack(fill="both", expand=True)

        header = ttk.Frame(wrap)
        header.pack(fill="x")
        ttk.Label(header, text="Create Appointment", font=("TkDefaultFont", 10, "bold")).pack(side="left")
        ttk.Button(header, text="↻ Refresh lists", command=self._load_reference_data).pack(side="right")

        body = ttk.Frame(wrap)
        body.pack(fill="both", expand=True)

        # Calendar
        if HAS_TKCAL:
            left = ttk.LabelFrame(body, text="Availability", padding=6)
            left.pack(side="left", fill="y", padx=(0, 8))
            today = date.today()
            self.b_cal = Calendar(left, selectmode="day", year=today.year, month=today.month, day=today.day,
                                  date_pattern="yyyy-mm-dd")
            self.b_cal.pack()
            self.b_cal.tag_config("avail", background=LIGHT_GREEN)
            self.b_cal.tag_config("blocked", background=LIGHT_RED)
            self.b_cal.bind("<<CalendarSelected>>", lambda _e: self._sync_date_from_calendar())
        else:
            self.b_cal = None

        # Form
        form = ttk.LabelFrame(body, text="Details", padding=8)
        form.pack(side="left", fill="both", expand=True)

        ttk.Label(form, text="Patient").grid(row=0, column=0, sticky="w")
        ttk.Label(form, text="Doctor").grid(row=0, column=1, sticky="w")

        self.b_patient = ttk.Combobox(form, width=40, values=getattr(self, "patient_choices", []))
        self.b_patient.grid(row=1, column=0, sticky="w", padx=(0, 6))

        self.b_doc = ttk.Combobox(form, width=32, values=getattr(self, "doc_choices", []))
        self.b_doc.grid(row=1, column=1, sticky="w")
        self.b_doc.bind(
            "<<ComboboxSelected>>",
            lambda _e: (self._recompute_available_dates(),
                        self._maybe_jump_to_next_available_date(),
                        self._refresh_book_slots())
        )

        ttk.Label(form, text="Date").grid(row=2, column=0, sticky="w")
        if HAS_TKCAL:
            self.b_date = DateEntry(form, width=16, date_pattern="yyyy-mm-dd")
        else:
            self.b_date = ttk.Entry(form, width=16)
            self.b_date.insert(0, datetime.now().strftime(DAY_FMT))
        self.b_date.grid(row=3, column=0, sticky="w", pady=(0, 6))
        if HAS_TKCAL:
            try:
                self.b_date.bind("<<DateEntrySelected>>", lambda _e: self._refresh_book_slots())
            except Exception:
                pass

        ttk.Label(form, text="Available Time").grid(row=2, column=1, sticky="w")
        self.b_time = ttk.Combobox(form, state="readonly", width=14, values=[])
        self.b_time.grid(row=3, column=1, sticky="w", pady=(0, 6))

        ttk.Label(form, text="Reason").grid(row=2, column=2, sticky="w")
        self.b_reason = ttk.Entry(form, width=40)
        self.b_reason.grid(row=3, column=2, sticky="w", pady=(0, 6))

        ttk.Button(form, text="Find Slots", command=self._refresh_book_slots).grid(row=4, column=1, sticky="w", pady=(0, 6))
        ttk.Button(form, text="Create", command=self._create_booking).grid(row=4, column=2, sticky="w", padx=(6, 0))

        self.book_list = tk.Listbox(wrap, height=8)
        self.book_list.pack(fill="both", expand=True, pady=(8, 0))

        # Initial paint
        try:
            self._recompute_available_dates()
            self._maybe_jump_to_next_available_date()
            self._refresh_book_slots()
        except Exception:
            pass

    def _selected_book_date_str(self) -> str:
        if HAS_TKCAL and self.b_cal is not None:
            d = self.b_cal.get_date()
            return d if isinstance(d, str) else d.strftime(DAY_FMT)
        return self.b_date.get() if not HAS_TKCAL else self.b_date.get_date().strftime(DAY_FMT)

    def _recompute_available_dates(self):
        label = self.b_doc.get().strip()
        doctor_id = self._doc_by_label.get(label) if label else None
        self._available_dates.clear()

        if not doctor_id:
            if HAS_TKCAL and self.b_cal is not None:
                self.b_cal.calevent_remove("all")
            return

        today_dt = datetime.now()
        end_dt = today_dt + timedelta(days=self._date_window_days)
        try:
            avail_list = AppointmentService.get_available_dates(doctor_id, today_dt, end_dt)
        except Exception:
            avail_list = []

        self._available_dates = set(avail_list)

        if HAS_TKCAL and self.b_cal is not None:
            self.b_cal.calevent_remove("all")
            try:
                self.b_cal.tag_config("avail", background=LIGHT_GREEN)
                self.b_cal.tag_config("blocked", background=LIGHT_RED)
            except Exception:
                pass

            cur_month_first = date.today().replace(day=1)
            all_days = [cur_month_first + timedelta(days=i) for i in range(370)]
            avail_dates = {datetime.strptime(s, DAY_FMT).date() for s in self._available_dates}

            for d in all_days:
                tag = "avail" if d in avail_dates else "blocked"
                self.b_cal.calevent_create(d, "", tag)

            try:
                self.b_cal.update_idletasks()
            except Exception:
                pass

    def _maybe_jump_to_next_available_date(self):
        if not self._available_dates:
            return
        sel_str = self._selected_book_date_str()
        if sel_str in self._available_dates:
            return
        today_str = date.today().strftime(DAY_FMT)
        future = sorted(d for d in self._available_dates if d >= today_str)
        pick = future[0] if future else sorted(self._available_dates)[0]
        try:
            if HAS_TKCAL and self.b_cal is not None:
                self.b_cal.selection_set(datetime.strptime(pick, DAY_FMT).date())
            if HAS_TKCAL:
                self.b_date.set_date(datetime.strptime(pick, DAY_FMT).date())
            else:
                self.b_date.delete(0, "end")
                self.b_date.insert(0, pick)
        except Exception:
            pass

    def _sync_date_from_calendar(self):
        if not HAS_TKCAL or self.b_cal is None:
            return
        try:
            d = self.b_cal.get_date()
            d_str = d if isinstance(d, str) else d.strftime(DAY_FMT)
            self.b_date.set_date(datetime.strptime(d_str, DAY_FMT).date())
        except Exception:
            pass
        self._refresh_book_slots()

    def _refresh_book_slots(self):
        doc_label = self.b_doc.get().strip()
        d_id = self._doc_by_label.get(doc_label)
        if not d_id:
            self.b_time["values"] = []
            self.b_time.set("")
            return

        date_str = self._selected_book_date_str()
        if self._available_dates and date_str not in self._available_dates:
            self.b_time["values"] = []
            self.b_time.set("")
            return

        try:
            day = datetime.strptime(date_str, DAY_FMT)
        except ValueError:
            messagebox.showerror("Date", "Use YYYY-MM-DD")
            self.b_time["values"] = []
            self.b_time.set("")
            return

        try:
            slots = AppointmentService.get_available_slots(d_id, day)
        except Exception:
            slots = []

        self.b_time["values"] = slots
        if slots:
            cur = self.b_time.get()
            self.b_time.set(cur if cur in slots else slots[0])
        else:
            self.b_time.set("")

    def _create_booking(self):
        pat_label = self.b_patient.get().strip()
        p_id = self._pat_by_label.get(pat_label)
        if not p_id:
            messagebox.showwarning("Patient", "Pick a patient from the list.")
            return

        reason = self.b_reason.get().strip()
        if not reason:
            messagebox.showwarning("Missing", "Enter a reason.")
            return

        t = self.b_time.get().strip()
        if not t:
            messagebox.showwarning("Missing", "Pick an available time (Find Slots).")
            return

        date_str = self._selected_book_date_str()
        try:
            when = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Time", "Bad time format.")
            return

        d_id = self._doc_by_label.get(self.b_doc.get().strip())
        if not d_id:
            messagebox.showerror("Doctor", "Pick a doctor and click Find Slots.")
            return

        # final race check
        try:
            current = set(AppointmentService.get_available_slots(d_id, when))
            if t not in current:
                self._refresh_book_slots()
                messagebox.showerror("Taken", "That slot was just taken. Please pick another.")
                return
        except Exception:
            pass

        with SessionLocal() as db:
            p = db.get(Patient, p_id)
            if not p:
                messagebox.showerror("Patient", "Patient not found.")
                return

            conflict = db.scalar(
                select(Appointment.id).where(
                    Appointment.doctor_id == d_id,
                    Appointment.scheduled_for == when,
                    Appointment.status != AppointmentStatus.cancelled
                )
            )
            if conflict:
                messagebox.showerror("Taken", "That slot is already booked.")
                return

            ap = Appointment(
                patient_id=p_id, doctor_id=d_id,
                scheduled_for=when, status=AppointmentStatus.booked,
                reason=reason
            )
            db.add(ap)
            db.commit()
            ap_id = ap.id

            u = db.scalar(select(User).join(Patient).where(Patient.id == p_id))
            email = (u.email if u else f"pt#{p_id}")

        self.book_list.insert("end", f"{when:%Y-%m-%d %H:%M}  #{ap_id}  {email}  booked")
        messagebox.showinfo("Created", f"Appointment #{ap_id} created.")
        self._recompute_available_dates()
        self._refresh_book_slots()

    # ------------------------------------------------------
    # Requests tab  (UPDATED)
    # ------------------------------------------------------
    def _build_requests_tab(self):
        wrap = ttk.Frame(self.tab_req, padding=6)
        wrap.pack(fill="both", expand=True)

        cols = ("id", "requested_for", "doctor", "patient", "reason")
        self.tv_req = ttk.Treeview(wrap, columns=cols, show="headings", height=14)
        heads = {
            "id": ("ID", 70),
            "requested_for": ("Requested For", 160),
            "doctor": ("Doctor", 200),
            "patient": ("Patient", 220),
            "reason": ("Reason", 360),
        }
        for c in cols:
            t, w = heads[c]
            self.tv_req.heading(c, text=t)
            self.tv_req.column(c, width=w)
        self.tv_req.pack(fill="both", expand=True)

        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Open", command=self._req_open_patient).pack(side="left")
        ttk.Button(btns, text="Approve", command=self._req_approve).pack(side="left", padx=6)
        ttk.Button(btns, text="Assign Slot…", command=self._req_assign).pack(side="left", padx=6)
        ttk.Button(btns, text="Decline", command=self._req_decline).pack(side="left", padx=6)
        ttk.Button(btns, text="Refresh", command=self._refresh_requests).pack(side="left", padx=6)

    def _refresh_requests(self):
        for i in self.tv_req.get_children():
            self.tv_req.delete(i)

        count = 0
        with SessionLocal() as db:
            # Robust for Enum or string statuses (requested/request/pending)
            status_text = func.lower(cast(Appointment.status, String))
            requested_match = or_(
                Appointment.status == AppointmentStatus.requested,  # Enum case (if using Enum)
                status_text == "requested",
                status_text == "request",
                status_text == "pending",
            )

            rows = db.scalars(
                select(Appointment)
                .options(
                    selectinload(Appointment.patient).selectinload(Patient.user),
                    selectinload(Appointment.doctor).selectinload(Doctor.user),
                )
                .where(requested_match)
                .order_by(Appointment.scheduled_for.asc())
            ).all()
            for a in rows:
                doc_label = (a.doctor.user.full_name if a.doctor and a.doctor.user else None) or (
                    "Unassigned" if not a.doctor_id else f"Dr#{a.doctor_id}"
                )
                pat_label = (a.patient.user.full_name if a.patient and a.patient.user else None) or f"Pt#{a.patient_id}"
                when = a.scheduled_for.strftime(DATE_FMT) if getattr(a, "scheduled_for", None) else "-"
                self.tv_req.insert("", "end", values=(a.id, when, doc_label, pat_label, a.reason or ""))
                count += 1

        self.nb.tab(self.tab_req, text=("Requests" if count == 0 else f"Requests ({count})"))

    def _sel_req_id(self):
        sel = self.tv_req.selection()
        return int(self.tv_req.item(sel[0], "values")[0]) if sel else None

    def _req_open_patient(self):
        ap_id = self._sel_req_id()
        if not ap_id:
            messagebox.showwarning("No selection", "Select a request.")
            return
        with SessionLocal() as db:
            ap = db.get(Appointment, ap_id)
            if not ap:
                return
            p = db.get(Patient, ap.patient_id)
            u = db.get(User, p.user_id) if p else None
        info = (
            f"Name: {(u.full_name or u.email) if u else '-'}\n"
            f"MRN: {getattr(p,'mrn','-')}\nDOB: {getattr(p,'dob','-')}\n"
            f"Allergies: {getattr(p,'allergies','')}\nConditions: {getattr(p,'chronic_conditions','')}\n"
        )
        messagebox.showinfo("Patient", info)

    def _req_approve(self):
        ap_id = self._sel_req_id()
        if not ap_id:
            messagebox.showwarning("No selection", "Select a request.")
            return

        with SessionLocal() as db:
            ap = db.get(Appointment, ap_id)
            if not ap:
                return

            target_doc_id = ap.doctor_id  # may be None/0 if unassigned

            # Conflict check only if a doctor is already assigned
            if target_doc_id:
                conflict = db.scalar(
                    select(Appointment.id).where(
                        Appointment.doctor_id == target_doc_id,
                        Appointment.scheduled_for == ap.scheduled_for,
                        Appointment.id != ap.id,
                        Appointment.status != AppointmentStatus.cancelled,
                    )
                )
                if conflict:
                    messagebox.showinfo("Taken", "Requested time is no longer free. Use 'Assign Slot…' to pick another.")
                    return

            ap.status = AppointmentStatus.booked
            db.commit()

        self._refresh_requests()
        self._refresh_schedule()
        messagebox.showinfo("Approved", "Request approved and booked.")

    def _req_assign(self):
        ap_id = self._sel_req_id()
        if not ap_id:
            messagebox.showwarning("No selection", "Select a request.")
            return

        with SessionLocal() as db:
            ap = db.get(Appointment, ap_id)
            if not ap:
                return
            cur = ap.scheduled_for
            target_doc_id = ap.doctor_id
            if not target_doc_id:
                messagebox.showinfo("No doctor", "This request has no doctor assigned. Please assign a doctor first.")
                return

        top = tk.Toplevel(self)
        top.title(f"Assign Slot #{ap_id}")
        top.transient(self.winfo_toplevel())
        try:
            top.lift(); top.after_idle(lambda: top.grab_set())
        except Exception:
            pass

        ttk.Label(top, text=f"Requested: {cur:%Y-%m-%d %H:%M}").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(top, text="Date").grid(row=1, column=0, sticky="w", padx=8)
        if HAS_TKCAL:
            de = DateEntry(top, width=16, date_pattern="yyyy-mm-dd"); de.set_date(cur.date())
        else:
            de = ttk.Entry(top, width=16); de.insert(0, cur.strftime(DAY_FMT))
        de.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(top, text="Available Time").grid(row=2, column=0, sticky="w", padx=8)
        cmb = ttk.Combobox(top, state="readonly", width=14); cmb.grid(row=2, column=1, sticky="w", padx=8)

        def load():
            date_s = de.get() if not HAS_TKCAL else de.get_date().strftime(DAY_FMT)
            try:
                day = datetime.strptime(date_s, DAY_FMT)
            except ValueError:
                messagebox.showerror("Date", "Use YYYY-MM-DD")
                return
            slots = AppointmentService.get_available_slots(target_doc_id, day)
            cmb["values"] = slots
            if slots:
                cmb.current(0)
            else:
                cmb.set("")
                messagebox.showinfo("No Slots", "No availability for that date.")

        ttk.Button(top, text="Find Slots", command=load).grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 6))

        def assign():
            t = (cmb.get() or "").strip()
            if not t:
                messagebox.showwarning("Missing", "Choose a time slot (Find Slots).")
                return
            date_s = de.get() if not HAS_TKCAL else de.get_date().strftime(DAY_FMT)
            try:
                when = datetime.strptime(f"{date_s} {t}", "%Y-%m-%d %H:%M")
            except ValueError:
                messagebox.showerror("Time", "Bad time")
                return

            with SessionLocal() as db2:
                a2 = db2.get(Appointment, ap_id)
                if not a2:
                    top.destroy(); return
                conflict = db2.scalar(
                    select(Appointment.id).where(
                        Appointment.doctor_id == target_doc_id,
                        Appointment.scheduled_for == when,
                        Appointment.id != ap_id,
                        Appointment.status != AppointmentStatus.cancelled,
                    )
                )
                if conflict:
                    messagebox.showerror("Taken", "That time is already booked.")
                    return

                a2.scheduled_for = when
                a2.status = AppointmentStatus.booked
                db2.commit()

            top.destroy()
            self._refresh_requests()
            self._refresh_schedule()

        ttk.Button(top, text="Assign", command=assign).grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

    def _req_decline(self):
        ap_id = self._sel_req_id()
        if not ap_id:
            messagebox.showwarning("No selection", "Select a request.")
            return
        if not messagebox.askyesno("Decline", "Decline this request?"):
            return
        with SessionLocal() as db:
            a = db.get(Appointment, ap_id)
            if a:
                a.status = AppointmentStatus.cancelled
            db.commit()
        self._refresh_requests()

    # ------------------------------------------------------
    # Billing tab (optional models supported)
    # ------------------------------------------------------
    def _build_billing_tab(self):
        wrap = ttk.Frame(self.tab_bill, padding=8)
        wrap.pack(fill="both", expand=True)

        if Invoice is None:
            ttk.Label(wrap, text="Billing models not found in this build.").pack(anchor="w")
            return

        top = ttk.Frame(wrap); top.pack(fill="x")
        ttk.Label(top, text="Patient email").pack(side="left")
        self.bl_email = ttk.Entry(top, width=28); self.bl_email.pack(side="left", padx=6)
        ttk.Button(top, text="Find", command=self._billing_refresh).pack(side="left")

        cols = ("id","date","patient","amount","status")
        self.tv_bill = ttk.Treeview(wrap, columns=cols, show="headings", height=12)
        for c,w in zip(cols,(80,120,260,120,120)):
            self.tv_bill.heading(c, text=c.title()); self.tv_bill.column(c, width=w)
        self.tv_bill.pack(fill="both", expand=True, pady=(8,0))

        btns = ttk.Frame(wrap); btns.pack(fill="x", pady=(6,0))
        ttk.Button(btns, text="Create Invoice…", command=self._invoice_create).pack(side="left")
        ttk.Button(btns, text="Record Payment…", command=self._invoice_payment).pack(side="left", padx=6)
        ttk.Button(btns, text="Refresh", command=self._billing_refresh).pack(side="left", padx=6)

    def _billing_refresh(self):
        if Invoice is None:
            return
        for i in self.tv_bill.get_children():
            self.tv_bill.delete(i)

        qmail = (self.bl_email.get() or "").strip().lower()
        with SessionLocal() as db:
            q = select(Invoice).order_by(Invoice.created_at.desc())
            if qmail:
                q = q.join(Invoice.patient).join(Patient.user).where(User.email == qmail)
            rows = db.scalars(q).all()
            for inv in rows:
                u = db.scalar(select(User).join(Patient).where(Patient.id == inv.patient_id))
                name = (u.full_name or u.email) if u else f"Pt#{inv.patient_id}"
                self.tv_bill.insert(
                    "", "end",
                    values=(
                        inv.id,
                        getattr(inv, "created_at", datetime.utcnow()).strftime(DATE_FMT),
                        name,
                        f"{getattr(inv, 'total_amount', 0.0):.2f}",
                        getattr(inv, "status", "open"),
                    ),
                )

    def _invoice_create(self):
        if Invoice is None:
            return
        top = tk.Toplevel(self); top.title("Create Invoice"); top.grab_set()
        ttk.Label(top, text="Appointment ID").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        ap_in = ttk.Entry(top, width=12); ap_in.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Amount").grid(row=1, column=0, sticky="w", padx=8)
        amt_in = ttk.Entry(top, width=12); amt_in.grid(row=1, column=1, sticky="w", padx=8)

        def do_create():
            try:
                apid = int(ap_in.get().strip()); amt = float(amt_in.get().strip())
            except Exception:
                messagebox.showerror("Invalid", "Enter valid appointment id and amount.")
                return
            with SessionLocal() as db:
                ap = db.get(Appointment, apid)
                if not ap:
                    messagebox.showerror("Appointment", "Not found.")
                    return
                inv = Invoice(patient_id=ap.patient_id, appointment_id=apid, total_amount=amt, status="open")
                db.add(inv); db.commit()
            top.destroy(); self._billing_refresh()
        ttk.Button(top, text="Create", command=do_create).grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(6,8))

    def _invoice_payment(self):
        if Payment is None or Invoice is None:
            return
        top = tk.Toplevel(self); top.title("Record Payment"); top.grab_set()
        ttk.Label(top, text="Invoice ID").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        id_in = ttk.Entry(top, width=12); id_in.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Amount").grid(row=1, column=0, sticky="w", padx=8)
        amt_in = ttk.Entry(top, width=12); amt_in.grid(row=1, column=1, sticky="w", padx=8)

        def do_pay():
            try:
                iid = int(id_in.get().strip()); amt = float(amt_in.get().strip())
            except Exception:
                messagebox.showerror("Invalid", "Enter valid invoice id and amount.")
                return
            with SessionLocal() as db:
                inv = db.get(Invoice, iid)
                if not inv:
                    messagebox.showerror("Invoice", "Not found.")
                    return
                pay = Payment(invoice_id=iid, amount=amt)
                db.add(pay)
                total_paid = (getattr(inv, "paid_amount", 0.0) or 0.0) + amt
                inv.paid_amount = total_paid
                if getattr(inv, "total_amount", 0.0) <= total_paid:
                    inv.status = "paid"
                db.commit()
            top.destroy(); self._billing_refresh()
        ttk.Button(top, text="Save", command=do_pay).grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(6,8))

    # ------------------------------------------------------
    # Patients tab (quick search)
    # ------------------------------------------------------
    def _build_patients_tab(self):
        wrap = ttk.Frame(self.tab_pat, padding=6); wrap.pack(fill="both", expand=True)
        top = ttk.Frame(wrap); top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        self.pt_q = ttk.Entry(top, width=28); self.pt_q.pack(side="left", padx=6)
        ttk.Button(top, text="Go", command=self._patients_refresh).pack(side="left")

        cols = ("id","name","dob","phone","status")
        self.tv_pat = ttk.Treeview(wrap, columns=cols, show="headings", height=14)
        for c,w in zip(cols,(70,220,120,140,120)):
            self.tv_pat.heading(c, text=c.title()); self.tv_pat.column(c, width=w)
        self.tv_pat.pack(fill="both", expand=True, pady=(6,0))

        ttk.Button(wrap, text="Open", command=self._patients_open).pack(pady=(6,0), anchor="w")

    def _patients_refresh(self):
        for i in self.tv_pat.get_children():
            self.tv_pat.delete(i)
        q = (self.pt_q.get() or "").strip().lower()
        with SessionLocal() as db:
            stmt = select(Patient).options(joinedload(Patient.user)).order_by(Patient.id)
            if q:
                stmt = (
                    stmt.join(User, Patient.user_id == User.id)
                    .where(or_(User.full_name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
                )
            rows = db.scalars(stmt).all()
            for p in rows:
                u = p.user
                self.tv_pat.insert(
                    "", "end",
                    values=(
                        p.id,
                        getattr(u, "full_name", "") or getattr(u, "email", ""),
                        getattr(p, "dob", ""),
                        getattr(u, "phone", ""),
                        getattr(p, "status", "active"),
                    ),
                )

    def _patients_open(self):
        sel = self.tv_pat.selection()
        if not sel:
            return
        pid = int(self.tv_pat.item(sel[0], "values")[0])
        with SessionLocal() as db:
            p = db.get(Patient, pid); u = db.get(User, p.user_id) if p else None
        messagebox.showinfo(
            "Patient",
            f"Name: {(u.full_name or u.email) if u else '-'}\n"
            f"DOB: {getattr(p,'dob','-')}\nPhone: {(u.phone or '-') if u else '-'}\n"
            f"Status: {getattr(p,'status','active')}"
        )

    # ------------------------------------------------------
    # Notifications & Support tabs  (UPDATED)
    # ------------------------------------------------------
    def _build_notifications_tab(self):
        wrap = ttk.Frame(self.tab_notif, padding=6)
        wrap.pack(fill="both", expand=True)

        cols = ("id", "time", "title", "who", "read")
        self.tv_nf = ttk.Treeview(wrap, columns=cols, show="headings", height=14)
        heads = {"id":70, "time":160, "title":380, "who":220, "read":80}
        for c in cols:
            self.tv_nf.heading(c, text=c.title())
            self.tv_nf.column(c, width=heads[c])
        self.tv_nf.pack(fill="both", expand=True)

        tools = ttk.Frame(wrap); tools.pack(fill="x", pady=(6, 0))
        ttk.Button(tools, text="Open", command=self._notif_open_selected).pack(side="left")
        ttk.Button(tools, text="Mark as read", command=self._notif_mark_read).pack(side="left", padx=6)
        ttk.Button(tools, text="Mark all as read", command=self._notif_mark_all).pack(side="left", padx=6)
        ttk.Button(tools, text="Refresh", command=self._notif_refresh).pack(side="left", padx=6)

        self.tv_nf.bind("<Double-1>", lambda _e: self._notif_open_selected())

    def _notif_refresh(self):
        for i in self.tv_nf.get_children():
            self.tv_nf.delete(i)
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        unread = 0
        with SessionLocal() as db:
            rows = db.scalars(
                select(Notification)
                .where(Notification.user_id == user.id)
                .order_by(Notification.created_at.desc())
            ).all()
            for n in rows:
                # Resolve “who” from appointment/patient/from_user
                who = "-"
                ap_id = getattr(n, "appointment_id", None)
                if ap_id:
                    ap = db.get(Appointment, ap_id)
                    if ap:
                        p = db.get(Patient, ap.patient_id)
                        if p:
                            u = db.get(User, p.user_id)
                            if u:
                                who = u.full_name or u.email or "-"
                elif getattr(n, "patient_id", None):
                    p = db.get(Patient, n.patient_id)
                    if p:
                        u = db.get(User, p.user_id)
                        if u:
                            who = u.full_name or u.email or "-"
                elif getattr(n, "from_user_id", None):
                    u = db.get(User, n.from_user_id)
                    if u:
                        who = u.full_name or u.email or "-"

                self.tv_nf.insert(
                    "", "end",
                    values=(
                        n.id,
                        n.created_at.strftime(DATE_FMT),
                        getattr(n, "title", "(notification)"),
                        who,
                        "yes" if getattr(n, "read", False) else "",
                    ),
                )
                if not getattr(n, "read", False):
                    unread += 1
        self.nb.tab(self.tab_notif, text=("Notifications" if unread == 0 else f"Notifications ({unread})"))

    def _notif_open_selected(self):
        sel = self.tv_nf.selection()
        if not sel:
            messagebox.showinfo("Open", "Select a notification first.")
            return

        notif_id = int(self.tv_nf.item(sel[0], "values")[0])
        with SessionLocal() as db:
            n = db.get(Notification, notif_id)
            if not n:
                messagebox.showerror("Not found", "Notification not found.")
                return

            appt_id = getattr(n, "appointment_id", None)
            if appt_id:
                # Jump to Schedule and select the appointment
                self.nb.select(self.tab_sched)
                self._refresh_schedule()
                self._select_appt_in_schedule(appt_id)
                return

            pat_id = getattr(n, "patient_id", None)
            if pat_id:
                a = db.scalar(
                    select(Appointment)
                    .where(Appointment.patient_id == pat_id)
                    .order_by(Appointment.scheduled_for.desc())
                )
                if a:
                    self.nb.select(self.tab_sched)
                    self._refresh_schedule()
                    self._select_appt_in_schedule(a.id)
                    return

        # Fallback
        messagebox.showinfo("Notification", "This notification is not linked to a specific appointment.")

    def _notif_mark_read(self):
        sel = self.tv_nf.selection()
        if not sel:
            return
        notif_id = int(self.tv_nf.item(sel[0], "values")[0])
        with SessionLocal() as db:
            n = db.get(Notification, notif_id)
            if n:
                n.read = True
                db.commit()
        self._notif_refresh()

    def _notif_mark_all(self):
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        with SessionLocal() as db:
            rows = db.scalars(select(Notification).where(Notification.user_id == user.id)).all()
            for n in rows:
                n.read = True
            db.commit()
        self._notif_refresh()

    def _build_support_tab(self):
        wrap = ttk.Frame(self.tab_support, padding=6)
        wrap.pack(fill="both", expand=True)
        cols = ("id", "created", "subject", "status")
        self.tv_tk = ttk.Treeview(wrap, columns=cols, show="headings", height=12)
        for c, w in zip(cols, (70, 160, 420, 120)):
            self.tv_tk.heading(c, text=c.title()); self.tv_tk.column(c, width=w)
        self.tv_tk.pack(fill="both", expand=True)

        form = ttk.LabelFrame(wrap, text="Create Ticket", padding=8)
        form.pack(fill="x", pady=(6, 0))
        ttk.Label(form, text="Subject").grid(row=0, column=0, sticky="w")
        self.tk_subj = ttk.Entry(form, width=50); self.tk_subj.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        ttk.Label(form, text="Body").grid(row=2, column=0, sticky="w")
        self.tk_body = tk.Text(form, height=4, width=50); self.tk_body.grid(row=3, column=0, sticky="ew")
        ttk.Button(form, text="Create", command=self._ticket_create).grid(row=3, column=1, sticky="e")

        ttk.Button(wrap, text="Refresh", command=self._support_refresh).pack(pady=(6, 0), anchor="w")

    def _ticket_create(self):
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        subj = self.tk_subj.get().strip()
        body = self.tk_body.get("1.0", "end").strip()
        if not subj or not body:
            messagebox.showwarning("Missing", "Subject and body required.")
            return
        with SessionLocal() as db:
            t = SupportTicket(user_id=user.id, subject=subj, body=body, status=TicketStatus.open)
            db.add(t); db.commit()
        self.tk_subj.delete(0, "end"); self.tk_body.delete("1.0", "end")
        self._support_refresh(); messagebox.showinfo("Created", "Ticket created.")

    def _support_refresh(self):
        for i in self.tv_tk.get_children():
            self.tv_tk.delete(i)
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        with SessionLocal() as db:
            rows = db.scalars(
                select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(SupportTicket.created_at.desc())
            ).all()
            for t in rows:
                self.tv_tk.insert(
                    "", "end",
                    values=(t.id, t.created_at.strftime(DATE_FMT), t.subject, getattr(t.status, "value", t.status)),
                )

    # ------------------------------------------------------
    # Profile & global refresh
    # ------------------------------------------------------
    def _open_profile_dialog(self):
        user = getattr(self.controller, "current_user", None)
        if not user:
            messagebox.showerror("Error", "Not logged in.")
            return
        top = tk.Toplevel(self); top.title("Profile"); top.grab_set()
        ttk.Label(top, text="Full Name").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
        nm = ttk.Entry(top, width=36); nm.insert(0, user.full_name or ""); nm.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Phone").grid(row=1, column=0, sticky="w", padx=8)
        ph = ttk.Entry(top, width=24); ph.insert(0, user.phone or ""); ph.grid(row=1, column=1, sticky="w", padx=8)

        def save():
            with SessionLocal() as db:
                u = db.get(User, user.id)
                if not u:
                    return
                u.full_name = nm.get().strip()
                u.phone = ph.get().strip()
                db.commit()
            self.controller.current_user.full_name = nm.get().strip()
            self.controller.current_user.phone = ph.get().strip()
            top.destroy(); messagebox.showinfo("Saved", "Profile updated.")
        ttk.Button(top, text="Save", command=save).grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(6,8))

    def _refresh_all(self):
        try:
            self._load_reference_data()
        except Exception as e:
            print("reference data load error:", e)
        try:
            self._refresh_staff()
        except Exception as e:
            print("staff refresh error:", e)
        try:
            self._refresh_schedule()
        except Exception as e:
            print("schedule refresh error:", e)
        try:
            self._refresh_requests()
        except Exception as e:
            print("requests refresh error:", e)
        try:
            self._billing_refresh()
        except Exception:
            pass
        try:
            self._patients_refresh()
        except Exception as e:
            print("patients refresh error:", e)
        try:
            self._notif_refresh()
        except Exception:
            pass
        try:
            self._support_refresh()
        except Exception:
            pass
