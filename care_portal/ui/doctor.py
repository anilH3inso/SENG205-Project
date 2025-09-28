# care_portal/ui/doctor.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta, date
from typing import Optional

# Optional date picker
try:
    from tkcalendar import DateEntry, Calendar  # pip install tkcalendar
    HAS_TKCAL = True
except Exception:
    HAS_TKCAL = False
    DateEntry = None  # type: ignore
    Calendar = None   # type: ignore

from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    User,
    Patient,
    Doctor,
    Appointment,
    AppointmentStatus,
    Attendance,
    AttendanceMethod,
    MedicalRecord,
    Prescription,
    DoctorAvailability,
    SupportTicket,
    TicketStatus,
    RecordAuthor,
    Notification,
    # ↓↓↓ disciplinary models used by the UI below
    DisciplinaryRecord,
    DisciplinarySeverity,
    DisciplinaryStatus,
    # staff check-in enums (not used directly but kept for type parity)
    StaffCheckinStatus,
    StaffCheckinMethod,
)
from ..services.appointments import AppointmentService
from .base import BaseFrame
from ..services.checkin import today_checkins


# ------------------------------ Formats ------------------------------
DATE_FMT = "%Y-%m-%d %H:%M"
DAY_FMT  = "%Y-%m-%d"
TIME_FMT = "%H:%M"


# ------------------------------ Small helpers ------------------------------
def _parse_hhmm(s: str) -> tuple[int, int] | None:
    """Return (hour, minute) if s is HH:MM, else None."""
    try:
        t = datetime.strptime(s.strip(), TIME_FMT)
        return t.hour, t.minute
    except Exception:
        return None


def _day_range(dt: datetime) -> tuple[datetime, datetime]:
    """UTC day range [00:00, 24:00) for index-friendly queries."""
    d0 = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    d1 = d0 + timedelta(days=1)
    return d0, d1


def pick_date(parent, initial: date | None = None) -> date | None:
    """Topmost, modal calendar/date prompt (won’t hide behind the app)."""
    if not HAS_TKCAL or Calendar is None:
        from tkinter import simpledialog
        s = simpledialog.askstring("Pick date", "YYYY-MM-DD", parent=parent)
        if not s:
            return None
        try:
            return datetime.strptime(s.strip(), "%Y-%m-%d").date()
        except Exception:
            messagebox.showerror("Invalid", "Use YYYY-MM-DD")
            return None

    top = tk.Toplevel(parent)
    top.title("Select date")
    try:
        top.transient(parent.winfo_toplevel())
    except Exception:
        pass
    try:
        top.lift()
        top.attributes("-topmost", True)
        top.update_idletasks()
        top.grab_set()
    except Exception:
        pass

    frm = ttk.Frame(top, padding=10)
    frm.pack(fill="both", expand=True)

    init = initial or datetime.now().date()
    cal = Calendar(
        frm,
        selectmode="day",
        year=init.year, month=init.month, day=init.day,
        date_pattern="yyyy-mm-dd"
    )
    cal.pack(fill="both", expand=True)

    chosen: dict[str, Optional[date]] = {"d": None}

    def _ok():
        try:
            chosen["d"] = datetime.strptime(cal.get_date(), "%Y-%m-%d").date()
        except Exception:
            chosen["d"] = None
        top.destroy()

    def _cancel():
        chosen["d"] = None
        top.destroy()

    btns = ttk.Frame(frm)
    btns.pack(fill="x", pady=(8, 0))
    ttk.Button(btns, text="Cancel", command=_cancel).pack(side="right", padx=(6, 0))
    ttk.Button(btns, text="OK", command=_ok).pack(side="right")

    top.wait_window()
    return chosen["d"]


# =============================================================================
# Doctor Portal (UI layout fixed; data/logic preserved)
# =============================================================================
class DoctorFrame(BaseFrame):
    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # resolve doctor row for logged-in user
        self.doctor: Doctor | None = None
        self._load_doctor()

        # ----- Top-level layout: left Filters sidebar + right Notebook -----
        root = ttk.Frame(self.body, padding=8)
        root.pack(fill="both", expand=True)

        # Sidebar (stays beside tabs)
        self.sidebar = ttk.LabelFrame(root, text="Filters", padding=8)
        self.sidebar.pack(side="left", fill="y", padx=(0, 8))

        # Show mode (All / By Date)
        ttk.Label(self.sidebar, text="Show").grid(row=0, column=0, sticky="w")
        self.f_mode = ttk.Combobox(
            self.sidebar, state="readonly", values=["All", "By Date"], width=16
        )
        self.f_mode.current(0)  # default = All
        self.f_mode.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        self.f_mode.bind("<<ComboboxSelected>>", lambda _e: self._on_filter_mode_change())

        # Date + Pick (only used when mode = By Date)
        ttk.Label(self.sidebar, text="Date").grid(row=2, column=0, sticky="w")
        self.f_date = ttk.Entry(self.sidebar, width=18)
        self.f_date.insert(0, datetime.now().strftime(DAY_FMT))
        self.f_date.grid(row=3, column=0, sticky="ew", pady=(2, 4))
        self.btn_pick = ttk.Button(self.sidebar, text="Pick…", command=lambda: self._pick_into(self.f_date))
        self.btn_pick.grid(row=3, column=1, padx=(6, 0))

        # Status
        ttk.Label(self.sidebar, text="Status").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.f_status = ttk.Combobox(
            self.sidebar, state="readonly",
            values=["(any)", "booked", "completed", "cancelled", "requested"], width=16
        )
        self.f_status.current(0)
        self.f_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 4))

        # Search
        ttk.Label(self.sidebar, text="Search (patient/reason)").grid(row=6, column=0, columnspan=2, sticky="w")
        self.f_search = ttk.Entry(self.sidebar, width=18)
        self.f_search.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(2, 6))

        ttk.Button(self.sidebar, text="Refresh", command=self._refresh_schedule)\
            .grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 2))

        # Right: Notebook with tabs
        notebook_wrap = ttk.Frame(root)
        notebook_wrap.pack(side="left", fill="both", expand=True)

        self.nb = ttk.Notebook(notebook_wrap)
        self.nb.pack(fill="both", expand=True)

        self.tab_sched    = ttk.Frame(self.nb)
        self.tab_avail    = ttk.Frame(self.nb)
        self.tab_requests = ttk.Frame(self.nb)
        self.tab_notif    = ttk.Frame(self.nb)
        self.tab_support  = ttk.Frame(self.nb)
        self.tab_kpi      = ttk.Frame(self.nb)

        self.nb.add(self.tab_sched, text="Appointments")
        self.nb.add(self.tab_avail, text="Availability")
        self.nb.add(self.tab_requests, text="Requests")
        self.nb.add(self.tab_notif, text="Notifications")
        self.nb.add(self.tab_support, text="Support")
        self.nb.add(self.tab_kpi, text="Today")

        # build tabs (creates self.tree_ap)
        self._build_schedule_tab()
        self._build_availability_tab()
        self._build_requests_tab()
        self._build_notifications_tab()
        self._build_support_tab()
        self._build_kpi_tab()

        # Apply initial mode (now safe because tree_ap exists)
        self._on_filter_mode_change()

        # initial loads
        self._refresh_schedule()
        self._refresh_availability()
        self._refresh_requests()
        self._refresh_notifications()
        self._refresh_support()
        self._refresh_kpis()
        try:
            self.refresh_checkins()
        except Exception:
            pass
    def _on_filter_mode_change(self):
        mode = (self.f_mode.get() or "All").strip()
        is_by_date = (mode == "By Date")
        # Toggle date widgets
        try:
            self.f_date.config(state=("normal" if is_by_date else "disabled"))
            self.btn_pick.config(state=("normal" if is_by_date else "disabled"))
        except Exception:
            pass
        # Only refresh if the table exists (safe on startup)
        if hasattr(self, "tree_ap"):
            self._refresh_schedule()

    # ====================================================
    # Lifecycle
    # ====================================================
    def on_show(self):
        """Called when this frame is shown—refreshes context & data safely."""
        self._load_doctor()
        if not self.doctor:
            try:
                messagebox.showerror(
                    "Not a doctor account",
                    "This dashboard requires role 'doctor'. Ask an admin to assign you the Doctor role."
                )
            except Exception:
                pass
            return
        try:
            self._refresh_schedule()
            self._refresh_availability()
            self._refresh_requests()
            self._refresh_notifications()
            self._refresh_support()
            self._refresh_kpis()
            self.refresh_checkins()
        except Exception as e:
            print("DoctorFrame on_show refresh error:", e)

    def _load_doctor(self):
        user = getattr(self.controller, "current_user", None)
        if not user:
            self.doctor = None
            return

        role_val = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))

        with SessionLocal() as db:
            doc = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
            # Auto-provision if logged-in user is a doctor but profile missing
            if not doc and role_val == "doctor":
                doc = Doctor(user_id=user.id, specialty="General")
                db.add(doc)
                db.commit()
                db.refresh(doc)
            self.doctor = doc

        if not self.doctor and role_val == "doctor":
            print("Warning: user has role=doctor but Doctor profile could not be created.")

    # ---------------- Profile dialog (unchanged logic; minor UX polish) ------------------
    def _open_profile_dialog(self):
        """
        Edit doctor & user fields.

        User:
          - Full Name, Email, Phone

        Doctor:
          - Specialty, License No, Designation/Title, Years Experience,
            Employee ID, Degree, University, Certifications, Work Address
        """
        user = getattr(self.controller, "current_user", None)
        if not user or not self.doctor:
            try:
                messagebox.showerror("Error", "Not logged in as a doctor.")
            except Exception:
                pass
            return

        with SessionLocal() as db:
            d = db.get(Doctor, self.doctor.id)
            u = db.get(User, user.id)
        if not d or not u:
            messagebox.showerror("Error", "Doctor profile not found.")
            return

        top = tk.Toplevel(self)
        top.title("Edit Profile")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        frm = ttk.Frame(top, padding=12)
        frm.pack(fill="both", expand=True)

        r = 0
        # --- User fields ---
        ttk.Label(frm, text="Full Name").grid(row=r, column=0, sticky="w")
        full_in = ttk.Entry(frm, width=40)
        full_in.insert(0, u.full_name or "")
        full_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Email (login)").grid(row=r, column=0, sticky="w")
        email_in = ttk.Entry(frm, width=40)
        email_in.insert(0, u.email or "")
        email_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Phone").grid(row=r, column=0, sticky="w")
        phone_in = ttk.Entry(frm, width=40)
        phone_in.insert(0, u.phone or "")
        phone_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Separator(frm, orient="horizontal").grid(row=r, column=0, columnspan=2, sticky="ew", pady=(8, 8)); r += 1

        # --- Doctor fields ---
        ttk.Label(frm, text="Specialty").grid(row=r, column=0, sticky="w")
        spec_in = ttk.Entry(frm, width=40)
        spec_in.insert(0, d.specialty or "General")
        spec_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="License No").grid(row=r, column=0, sticky="w")
        lic_in = ttk.Entry(frm, width=40)
        lic_in.insert(0, getattr(d, "license_no", "") or "")
        lic_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Designation / Title").grid(row=r, column=0, sticky="w")
        title_in = ttk.Entry(frm, width=40)
        title_in.insert(0, getattr(d, "designation", "") or "")
        title_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Years Experience").grid(row=r, column=0, sticky="w")
        years_in = ttk.Entry(frm, width=12)
        try:
            years_in.insert(0, str(int(getattr(d, "years_exp", 0) or 0)))
        except Exception:
            years_in.insert(0, "0")
        years_in.grid(row=r, column=1, sticky="w"); r += 1

        ttk.Label(frm, text="Employee ID").grid(row=r, column=0, sticky="w")
        empid_in = ttk.Entry(frm, width=40)
        empid_in.insert(0, getattr(d, "employee_id", "") or "")
        empid_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Degree").grid(row=r, column=0, sticky="w")
        degree_in = ttk.Entry(frm, width=40)
        degree_in.insert(0, getattr(d, "degree", "") or "")
        degree_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="University").grid(row=r, column=0, sticky="w")
        univ_in = ttk.Entry(frm, width=40)
        univ_in.insert(0, getattr(d, "university", "") or "")
        univ_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Certifications").grid(row=r, column=0, sticky="w")
        cert_in = ttk.Entry(frm, width=40)
        cert_in.insert(0, getattr(d, "certifications", "") or "")
        cert_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Work Address").grid(row=r, column=0, sticky="w")
        addr_in = ttk.Entry(frm, width=40)
        addr_in.insert(0, getattr(d, "work_address", "") or "")
        addr_in.grid(row=r, column=1, sticky="ew"); r += 1

        frm.columnconfigure(1, weight=1)

        btns = ttk.Frame(top)
        btns.pack(fill="x", pady=(8, 10), padx=10)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right", padx=(6, 0))

        def save():
            # Validate Years Experience
            years_txt = years_in.get().strip()
            try:
                years_val = int(years_txt) if years_txt else 0
                if years_val < 0:
                    raise ValueError()
            except Exception:
                messagebox.showerror("Invalid", "Years Experience must be a non-negative integer.")
                return

            with SessionLocal() as db:
                d2 = db.get(Doctor, d.id)
                u2 = db.get(User, u.id)
                if not d2 or not u2:
                    messagebox.showerror("Error", "Profile not found.")
                    return

                # Update user
                u2.full_name = full_in.get().strip()
                u2.email     = email_in.get().strip()
                u2.phone     = phone_in.get().strip()

                # Update doctor
                d2.specialty     = spec_in.get().strip() or "General"
                d2.license_no    = lic_in.get().strip()
                d2.designation   = title_in.get().strip()
                d2.years_exp     = years_val
                d2.employee_id   = empid_in.get().strip()
                d2.degree        = degree_in.get().strip()
                d2.university    = univ_in.get().strip()
                d2.certifications= cert_in.get().strip()
                d2.work_address  = addr_in.get().strip()

                db.commit()

            # refresh local caches & header
            if hasattr(self.controller, "current_user"):
                self.controller.current_user.full_name = full_in.get().strip()
                self.controller.current_user.email     = email_in.get().strip()
                self.controller.current_user.phone     = phone_in.get().strip()

            self._load_doctor()
            try:
                self.set_user(self.controller.current_user)
            except Exception:
                pass

            try:
                self._refresh_schedule()
                self._refresh_kpis()
                self.refresh_checkins()
            except Exception:
                pass

            messagebox.showinfo("Saved", "Profile updated.")
            top.destroy()

        ttk.Button(btns, text="Save", command=save).pack(side="right")

    # ---------------- Appointments tab ------------------
    def _build_schedule_tab(self):
        wrap = ttk.Frame(self.tab_sched, padding=6)
        wrap.pack(fill="both", expand=True)

        # 3-column grid: Table | (spacing) | Snapshot
        wrap.columnconfigure(0, weight=1)               # table expands
        wrap.columnconfigure(1, minsize=12, weight=0)   # spacer
        wrap.columnconfigure(2, minsize=360, weight=0)  # snapshot fixed width
        wrap.rowconfigure(0, weight=1)                  # main row stretches

        # ---------------- Middle: Table + Buttons ----------------
        middle = ttk.Frame(wrap)
        middle.grid(row=0, column=0, sticky="nsew")
        middle.rowconfigure(0, weight=1)  # table grows
        middle.columnconfigure(0, weight=1)

        col = ("id", "time", "patient", "reason", "status", "checked_in")
        self.tree_ap = ttk.Treeview(middle, columns=col, show="headings", height=16)
        heads = {
            "id": ("Appt ID", 70),
            "time": ("Time", 130),
            "patient": ("Patient", 220),
            "reason": ("Reason", 240),
            "status": ("Status", 100),
            "checked_in": ("Checked-in", 100),
        }
        for c in col:
            title, w = heads[c]
            self.tree_ap.heading(c, text=title)
            self.tree_ap.column(c, width=w)
        self.tree_ap.grid(row=0, column=0, sticky="nsew")

        # Attach scrollbars (pack-free; no bottom clipping)
        vsb = ttk.Scrollbar(middle, orient="vertical", command=self.tree_ap.yview)
        hsb = ttk.Scrollbar(middle, orient="horizontal", command=self.tree_ap.xview)
        self.tree_ap.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Toolbar
        btns = ttk.Frame(middle)
        btns.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for txt, cmd in [
            ("Open Patient", self._open_patient),
            ("Mark Completed", self._mark_completed),
            ("Cancel", self._cancel_appt),
            ("Reschedule", self._resched_appt),
            ("Check-in", self._checkin_appt),
            ("Undo Check-in", self._undo_checkin),
        ]:
            ttk.Button(btns, text=txt, command=cmd).pack(side="left", padx=4)

        # ---------------- Right: Snapshot & Quick Actions ----------------
        right = ttk.LabelFrame(wrap, text="Patient Snapshot & Quick Actions", padding=8)
        right.grid(row=0, column=2, sticky="nsew")

        self.snap_lbl = ttk.Label(right, text="No patient selected", wraplength=320, justify="left")
        self.snap_lbl.pack(anchor="w", pady=(0, 6))

        ttk.Label(right, text="Allergies").pack(anchor="w")
        # editable inputs as requested
        self.snap_allerg = tk.Text(right, height=3, width=42)
        self.snap_allerg.pack(fill="x", pady=(0, 6))

        ttk.Label(right, text="Chronic Conditions").pack(anchor="w")
        self.snap_cond = tk.Text(right, height=3, width=42)
        self.snap_cond.pack(fill="x", pady=(0, 6))

        ttk.Label(right, text="Last Prescriptions / Notes").pack(anchor="w")
        self.snap_recent = tk.Listbox(right, height=6, width=42)
        self.snap_recent.pack(fill="both", expand=False)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(6, 6))

        qa = ttk.LabelFrame(right, text="Quick Clinical Actions", padding=8)
        qa.pack(fill="x")
        for c in range(4):
            qa.grid_columnconfigure(c, weight=1)

        self.sel_appt_lbl = ttk.Label(qa, text="Selected: –")
        self.sel_appt_lbl.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        ttk.Label(qa, text="Visit Note").grid(row=1, column=0, columnspan=4, sticky="w")
        self.note_txt = tk.Text(qa, height=4, width=40)
        self.note_txt.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(2, 4))
        ttk.Button(qa, text="Save Note", command=self._save_note).grid(row=3, column=0, sticky="w", pady=(0, 6))

        ttk.Separator(qa, orient="horizontal").grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 6))

        ttk.Label(qa, text="Prescription").grid(row=5, column=0, columnspan=4, sticky="w")
        ttk.Label(qa, text="Medication").grid(row=6, column=0, sticky="w")
        self.rx_med = ttk.Entry(qa, width=22)
        self.rx_med.grid(row=7, column=0, sticky="w", pady=2)

        ttk.Label(qa, text="Dose").grid(row=6, column=1, sticky="w")
        self.rx_dose = ttk.Entry(qa, width=10)
        self.rx_dose.grid(row=7, column=1, sticky="w", pady=2, padx=(6, 0))

        ttk.Label(qa, text="Frequency").grid(row=8, column=0, sticky="w")
        self.rx_freq = ttk.Entry(qa, width=22)
        self.rx_freq.grid(row=9, column=0, sticky="w", pady=2)

        ttk.Label(qa, text="Duration").grid(row=8, column=1, sticky="w")
        self.rx_dur = ttk.Entry(qa, width=10)
        self.rx_dur.grid(row=9, column=1, sticky="w", pady=2, padx=(6, 0))

        ttk.Label(qa, text="Notes (optional)").grid(row=10, column=0, columnspan=4, sticky="w")
        self.rx_note = ttk.Entry(qa, width=40)
        self.rx_note.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(2, 6))

        ttk.Button(qa, text="Create Rx", command=self._save_rx).grid(row=12, column=0, sticky="w")

        # selection → snapshot/labels
        self._current_patient_id: int | None = None
        self._current_appt_id: int | None = None
        self.tree_ap.bind("<<TreeviewSelect>>", lambda e: self._load_snapshot_from_selection())
        self.tree_ap.bind("<Double-1>", lambda e: self._open_patient())

    # ---------------- Availability tab (logic retained) ------------------
    def _build_availability_tab(self):
        wrap = ttk.Frame(self.tab_avail, padding=6)
        wrap.pack(fill="both", expand=True)

        col = ("id", "day", "start", "end", "slot")
        self.tree_av = ttk.Treeview(wrap, columns=col, show="headings", height=12)
        heads = {
            "id": ("ID", 60),
            "day": ("Date", 120),
            "start": ("Start", 80),
            "end": ("End", 80),
            "slot": ("Slot (min)", 100),
        }
        for c in col:
            title, w = heads[c]
            self.tree_av.heading(c, text=title)
            self.tree_av.column(c, width=w)
        self.tree_av.pack(fill="both", expand=True)

        form = ttk.LabelFrame(wrap, text="Add / Update Rule", padding=8)
        form.pack(fill="x", pady=(6, 0))

        ttk.Label(form, text="Date").grid(row=0, column=0, sticky="w")
        self.av_date = ttk.Entry(form, width=18)
        self.av_date.insert(0, datetime.now().strftime(DAY_FMT))
        self.av_date.grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Button(form, text="Pick…", command=lambda: self._pick_into(self.av_date)).grid(row=1, column=1, sticky="w")

        ttk.Label(form, text="Start (HH:MM)").grid(row=0, column=2, sticky="w")
        self.av_start = ttk.Entry(form, width=10); self.av_start.insert(0, "09:00")
        self.av_start.grid(row=1, column=2, sticky="w", padx=(0, 8))

        ttk.Label(form, text="End (HH:MM)").grid(row=0, column=3, sticky="w")
        self.av_end = ttk.Entry(form, width=10); self.av_end.insert(0, "17:00")
        self.av_end.grid(row=1, column=3, sticky="w", padx=(0, 8))

        ttk.Label(form, text="Slot (min)").grid(row=0, column=4, sticky="w")
        self.av_slot = ttk.Entry(form, width=10); self.av_slot.insert(0, "30")
        self.av_slot.grid(row=1, column=4, sticky="w", padx=(0, 8))

        btns = ttk.Frame(form); btns.grid(row=1, column=5, sticky="e")
        ttk.Button(btns, text="Add / Update", command=self._save_availability).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Delete Selected", command=self._del_availability).pack(side="left")
        ttk.Button(btns, text="Refresh", command=self._refresh_availability).pack(side="left", padx=(6, 0))

        self.tree_av.bind("<<TreeviewSelect>>", lambda e: self._on_select_availability())
        self._refresh_availability()

    # ---------------- Requests tab (logic retained) -----------------
    def _build_requests_tab(self):
        wrap = ttk.Frame(self.tab_requests, padding=6)
        wrap.pack(fill="both", expand=True)

        col = ("id", "requested_for", "patient", "reason")
        self.tree_req = ttk.Treeview(wrap, columns=col, show="headings", height=14)
        heads = {
            "id": ("ID", 70),
            "requested_for": ("Requested For", 160),
            "patient": ("Patient", 220),
            "reason": ("Reason", 360),
        }
        for c in col:
            t, w = heads[c]
            self.tree_req.heading(c, text=t)
            self.tree_req.column(c, width=w)
        self.tree_req.pack(fill="both", expand=True)

        btns = ttk.Frame(wrap); btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Approve (keep time)", command=self._approve_request).pack(side="left")
        ttk.Button(btns, text="Assign Slot…", command=self._assign_slot_request).pack(side="left", padx=6)
        ttk.Button(btns, text="Decline", command=self._decline_request).pack(side="left", padx=6)
        ttk.Button(btns, text="Refresh", command=self._refresh_requests).pack(side="left", padx=6)

    # ---------------- Notifications tab (logic retained) ----------------
    def _build_notifications_tab(self):
        wrap = ttk.Frame(self.tab_notif, padding=6)
        wrap.pack(fill="both", expand=True)

        col = ("id", "time", "title", "who", "read")
        self.tree_nf = ttk.Treeview(wrap, columns=col, show="headings", height=14)
        heads = {
            "id": ("ID", 60),
            "time": ("Time", 160),
            "title": ("Title", 380),
            "who": ("Who", 220),
            "read": ("Read", 80),
        }
        for c in col:
            t, w = heads[c]
            self.tree_nf.heading(c, text=t)
            self.tree_nf.column(c, width=w)
        self.tree_nf.pack(fill="both", expand=True)

        btns = ttk.Frame(wrap); btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Mark as read", command=self._notif_mark_read).pack(side="left")
        ttk.Button(btns, text="Refresh", command=self._refresh_notifications).pack(side="left", padx=6)

    # ---------------- Support tab (logic retained) -----------------------
    def _build_support_tab(self):
        wrap = ttk.Frame(self.tab_support, padding=8)
        wrap.pack(fill="both", expand=True)

        col = ("id", "created", "subject", "status")
        self.tree_tk = ttk.Treeview(wrap, columns=col, show="headings", height=12)
        heads = {
            "id": ("ID", 60),
            "created": ("Created", 160),
            "subject": ("Subject", 360),
            "status": ("Status", 100),
        }
        for c in col:
            t, w = heads[c]
            self.tree_tk.heading(c, text=t)
            self.tree_tk.column(c, width=w)
        self.tree_tk.pack(fill="both", expand=True)

        form = ttk.LabelFrame(wrap, text="Create Ticket", padding=8)
        form.pack(fill="x", pady=(6, 0))
        ttk.Label(form, text="Subject").grid(row=0, column=0, sticky="w")
        self.tk_subj = ttk.Entry(form, width=50)
        self.tk_subj.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        ttk.Label(form, text="Body").grid(row=2, column=0, sticky="w")
        self.tk_body = tk.Text(form, height=4, width=50)
        self.tk_body.grid(row=3, column=0, sticky="ew", pady=(2, 6))
        ttk.Button(form, text="Create", command=self._create_ticket).grid(row=3, column=1, sticky="e")

    # ---------------- KPI tab + Staff Check-ins (logic retained) ----------
    def _build_kpi_tab(self):
        wrap = ttk.Frame(self.tab_kpi, padding=12)
        wrap.pack(fill="both", expand=True)

        # KPI summary
        self.kpi_summary = tk.Text(wrap, height=10, width=90, state="disabled")
        self.kpi_summary.pack(fill="x", expand=False)
        ttk.Button(wrap, text="Refresh KPIs", command=self._refresh_kpis).pack(pady=(6, 12), anchor="w")

        # Today’s Staff Check-ins
        self.checkin_frame = ttk.LabelFrame(wrap, text="Today’s Staff Check-ins", padding=8)
        self.checkin_frame.pack(fill="both", expand=True)

        cols = ("when", "name", "role", "status", "method", "location")
        self.checkin_tv = ttk.Treeview(self.checkin_frame, columns=cols, show="headings", height=8)
        for c in cols:
            self.checkin_tv.heading(c, text=c.title())
            self.checkin_tv.column(c, width=110, anchor="center")
        self.checkin_tv.pack(fill="both", expand=True, padx=6, pady=6)

        btnrow = ttk.Frame(self.checkin_frame)
        btnrow.pack(fill="x")
        ttk.Button(btnrow, text="Refresh", command=self.refresh_checkins).pack(side="left")

    # ====================================================
    # Data ops — Schedule
    # ====================================================
    def _selected_appt_id(self) -> int | None:
        sel = self.tree_ap.selection()
        if not sel:
            return None
        vals = self.tree_ap.item(sel[0], "values")
        try:
            return int(vals[0])
        except Exception:
            return None

    def _refresh_schedule(self):
        # clear table
        for i in self.tree_ap.get_children():
            self.tree_ap.delete(i)
        if not self.doctor:
            return

        # Determine mode (fallback to "By Date" behavior if f_mode isn't present)
        use_by_date = True
        try:
            use_by_date = (self.f_mode.get() == "By Date")
        except Exception:
            use_by_date = True  # backward-compatible default

        day0 = day1 = None
        if use_by_date:
            # date from sidebar (only when By Date)
            date_str = (self.f_date.get() or "").strip()
            try:
                day0 = datetime.strptime(date_str, DAY_FMT).replace(hour=0, minute=0, second=0, microsecond=0)
            except ValueError:
                messagebox.showerror("Invalid date", "Use YYYY-MM-DD")
                return
            day1 = day0 + timedelta(days=1)

        status = self.f_status.get()
        q = (self.f_search.get() or "").strip().lower()

        with SessionLocal() as db:
            # base query: this doctor
            stmt = (
                select(Appointment)
                .options(selectinload(Appointment.patient).selectinload(Patient.user))
                .where(Appointment.doctor_id == self.doctor.id)
                .order_by(Appointment.scheduled_for.asc())
            )
            # add date window only in "By Date" mode
            if use_by_date and day0 and day1:
                stmt = stmt.where(
                    Appointment.scheduled_for >= day0,
                    Appointment.scheduled_for < day1,
                )

            # status filter
            if status and status != "(any)":
                try:
                    stmt = stmt.where(Appointment.status == AppointmentStatus(status))
                except Exception:
                    pass

            appts = db.scalars(stmt).all()

            # check-ins just for the loaded appointments
            ap_ids = [a.id for a in appts]
            checked_ids: set[int] = set()
            if ap_ids:
                checked_ids = set(
                    db.scalars(
                        select(Attendance.appointment_id).where(Attendance.appointment_id.in_(ap_ids))
                    ).all()
                )

            # search filter + populate rows
            for a in appts:
                patient_label = (a.patient.user.full_name or a.patient.user.email) if a.patient and a.patient.user else ""
                if q and (q not in (patient_label or "").lower()) and (q not in (a.reason or "").lower()):
                    continue
                self.tree_ap.insert(
                    "", "end",
                    values=(
                        a.id,
                        a.scheduled_for.strftime(DATE_FMT),
                        patient_label,
                        a.reason or "",
                        getattr(a.status, "value", str(a.status)),
                        "yes" if a.id in checked_ids else "",
                    )
                )


    def _load_snapshot_from_selection(self):
        appt_id = self._selected_appt_id()
        self._current_appt_id = appt_id

        # clear banner & fields when nothing selected
        if not appt_id:
            self._current_patient_id = None
            self.snap_lbl.config(text="No patient selected")
            try:
                self.sel_appt_lbl.config(text="Selected: –")
            except Exception:
                pass
            for txt in (self.snap_allerg, self.snap_cond):
                txt.delete("1.0", "end")
            self.snap_recent.delete(0, "end")
            return

        with SessionLocal() as db:
            a = db.get(Appointment, appt_id)
            if not a:
                return
            p = db.get(Patient, a.patient_id)
            u = db.get(User, p.user_id) if p else None

            self._current_patient_id = p.id if p else None
            patient_label = (u.full_name or u.email) if u else "Unknown"

            label = f"{patient_label} — MRN:{getattr(p, 'mrn', '-') or '-'}  " \
                    f"DOB:{getattr(p, 'dob', '-') or '-'}  Phone:{getattr(u, 'phone', '-') or '-'}"
            self.snap_lbl.config(text=label)

            # “Selected:” banner above quick actions
            try:
                self.sel_appt_lbl.config(
                    text=f"Selected: Appt #{a.id} — {a.scheduled_for:%Y-%m-%d %H:%M} — {patient_label}"
                )
            except Exception:
                pass

            # Allergies / Conditions (editable inputs)
            self.snap_allerg.delete("1.0", "end")
            self.snap_allerg.insert("end", getattr(p, "allergies", "") or "")

            self.snap_cond.delete("1.0", "end")
            self.snap_cond.insert("end", getattr(p, "chronic_conditions", "") or "")

            # recent prescriptions & notes
            self.snap_recent.delete(0, "end")
            rx_rows = db.scalars(
                select(Prescription)
                .where(Prescription.appointment_id == a.id)
                .order_by(Prescription.created_at.desc())
            ).all()
            for r in rx_rows[:5]:
                label = r.text or r.medication or "Prescription"
                self.snap_recent.insert("end", f"Rx {r.created_at:%Y-%m-%d}: {label[:60]}")

            note_rows = db.scalars(
                select(MedicalRecord)
                .where(MedicalRecord.patient_id == p.id)
                .order_by(MedicalRecord.created_at.desc())
            ).all()
            for n in note_rows[:5]:
                self.snap_recent.insert("end", f"Note {n.created_at:%Y-%m-%d}: {n.text[:60]}")

    # Quick actions (Appointments tab) — logic unchanged
    def _open_patient(self):
        """Open a full patient detail dialog for the selected appointment."""
        if not self._current_appt_id:
            self._load_snapshot_from_selection()
        appt_id = self._current_appt_id
        if not appt_id:
            messagebox.showwarning("No selection", "Select an appointment first.")
            return

        with SessionLocal() as db:
            a = db.get(Appointment, appt_id)
            if not a:
                messagebox.showerror("Not found", "Appointment not found.")
                return
            p = db.get(Patient, a.patient_id)
            if not p:
                messagebox.showerror("Not found", "Patient not found.")
                return
            u = db.get(User, p.user_id) if p else None

            top = tk.Toplevel(self)
            top.title(f"Patient — {(u.full_name or u.email) if u else 'Patient'}")
            top.transient(self.winfo_toplevel())
            top.grab_set()

            main = ttk.Frame(top, padding=10)
            main.pack(fill="both", expand=True)

            # Demographics
            demo = ttk.LabelFrame(main, text="Demographics", padding=8)
            demo.pack(fill="x")
            ttk.Label(demo, text=f"Name: {(u.full_name or u.email) if u else '-'}").grid(row=0, column=0, sticky="w", padx=(0, 12))
            ttk.Label(demo, text=f"MRN: {p.mrn or '-'}").grid(row=0, column=1, sticky="w", padx=(0, 12))
            ttk.Label(demo, text=f"DOB: {p.dob or '-'}").grid(row=0, column=2, sticky="w", padx=(0, 12))
            ttk.Label(demo, text=f"Phone: {(u.phone or '-') if u else '-'}").grid(row=0, column=3, sticky="w")

            # Allergies / Conditions
            ac = ttk.Frame(main); ac.pack(fill="x", pady=(8, 0))
            ttk.Label(ac, text="Allergies").grid(row=0, column=0, sticky="w")
            alg = tk.Text(ac, height=3, width=50); alg.insert("1.0", p.allergies or "")
            alg.grid(row=1, column=0, sticky="ew", padx=(0, 8))
            ttk.Label(ac, text="Chronic Conditions").grid(row=0, column=1, sticky="w")
            cond = tk.Text(ac, height=3, width=50); cond.insert("1.0", p.chronic_conditions or "")
            cond.grid(row=1, column=1, sticky="ew")
            ac.columnconfigure(0, weight=1); ac.columnconfigure(1, weight=1)

            # Records & Rx
            lists = ttk.Frame(main); lists.pack(fill="both", expand=True, pady=(8, 0))
            recf = ttk.LabelFrame(lists, text="Medical Records", padding=8)
            rxf  = ttk.LabelFrame(lists, text="Prescriptions (this appointment)", padding=8)
            recf.pack(side="left", fill="both", expand=True, padx=(0, 8))
            rxf.pack(side="left", fill="both", expand=True)

            rec_list = tk.Listbox(recf, height=10)
            rec_list.pack(fill="both", expand=True)
            rows = db.scalars(
                select(MedicalRecord).where(MedicalRecord.patient_id == p.id).order_by(MedicalRecord.created_at.desc())
            ).all()
            for r in rows:
                who = "Dr" if r.author_role == RecordAuthor.doctor else "Pt"
                rec_list.insert("end", f"{r.created_at:%Y-%m-%d} [{who}] {r.text[:80]}")

            rx_list = tk.Listbox(rxf, height=10)
            rx_list.pack(fill="both", expand=True)
            rx_rows = db.scalars(
                select(Prescription).where(Prescription.appointment_id == a.id).order_by(Prescription.created_at.desc())
            ).all()
            for r in rx_rows:
                label = r.text or r.medication or "Prescription"
                rx_list.insert("end", f"{r.created_at:%Y-%m-%d}  {label[:80]}")

            # ---------- Disciplinary Records (unchanged) ----------
            discf = ttk.LabelFrame(main, text="Disciplinary Records", padding=8)
            discf.pack(fill="x", pady=(8, 0))

            inner = ttk.Frame(discf); inner.pack(fill="x")
            disc_list = tk.Listbox(inner, height=7, width=52)
            disc_list.pack(side="left", fill="both", expand=True)
            right = ttk.Frame(inner); right.pack(side="left", padx=(8, 0), fill="x")

            ttk.Label(right, text="Title").grid(row=0, column=0, sticky="w")
            disc_title = ttk.Entry(right, width=36); disc_title.grid(row=1, column=0, sticky="ew", pady=(2, 6))

            ttk.Label(right, text="Severity").grid(row=2, column=0, sticky="w")
            disc_sev = ttk.Combobox(
                right, state="readonly",
                values=[s.value for s in DisciplinarySeverity], width=20
            ); disc_sev.grid(row=3, column=0, sticky="w", pady=(2, 6))

            ttk.Label(right, text="Status").grid(row=4, column=0, sticky="w")
            disc_status = ttk.Combobox(
                right, state="readonly",
                values=[s.value for s in DisciplinaryStatus], width=20
            ); disc_status.grid(row=5, column=0, sticky="w", pady=(2, 6))

            ttk.Label(right, text="Description").grid(row=6, column=0, sticky="w")
            disc_desc = tk.Text(right, height=4, width=36); disc_desc.grid(row=7, column=0, sticky="ew", pady=(2, 6))

            def _disc_load():
                disc_list.delete(0, "end")
                with SessionLocal() as db2:
                    rows2 = db2.query(DisciplinaryRecord)\
                               .filter(DisciplinaryRecord.patient_id == p.id)\
                               .order_by(DisciplinaryRecord.created_at.desc()).all()
                    for r in rows2:
                        disc_list.insert("end", f"{r.id} • {r.created_at:%Y-%m-%d} • {r.severity.value} • {r.title[:50]}")

            def _disc_clear_form():
                disc_title.delete(0, "end")
                disc_sev.set(DisciplinarySeverity.low.value)
                disc_status.set(DisciplinaryStatus.open.value)
                disc_desc.delete("1.0", "end")

            def _disc_on_select(_e=None):
                sel = disc_list.curselection()
                if not sel:
                    return
                try:
                    rec_id = int(disc_list.get(sel[0]).split(" • ", 1)[0])
                except Exception:
                    return
                with SessionLocal() as db2:
                    r = db2.get(DisciplinaryRecord, rec_id)
                    if not r:
                        return
                    disc_title.delete(0, "end"); disc_title.insert(0, r.title or "")
                    disc_sev.set(getattr(r.severity, "value", str(r.severity)) or DisciplinarySeverity.low.value)
                    disc_status.set(getattr(r.status, "value", str(r.status)) or DisciplinaryStatus.open.value)
                    disc_desc.delete("1.0", "end"); disc_desc.insert("end", r.description or "")

            def _disc_save_new():
                t = (disc_title.get() or "").strip()
                if not t:
                    messagebox.showwarning("Missing", "Enter a title.")
                    return
                sev = disc_sev.get() or DisciplinarySeverity.low.value
                st  = disc_status.get() or DisciplinaryStatus.open.value
                desc = disc_desc.get("1.0", "end").strip()
                with SessionLocal() as db2:
                    rec = DisciplinaryRecord(
                        patient_id=p.id,
                        title=t,
                        severity=DisciplinarySeverity(sev),
                        status=DisciplinaryStatus(st),
                        description=desc,
                    )
                    db2.add(rec); db2.commit()
                _disc_load()
                messagebox.showinfo("Saved", "Disciplinary record created.")

            def _disc_update():
                sel = disc_list.curselection()
                if not sel:
                    messagebox.showwarning("No selection", "Pick a record from the list.")
                    return
                try:
                    rec_id = int(disc_list.get(sel[0]).split(" • ", 1)[0])
                except Exception:
                    messagebox.showerror("Error", "Could not parse selection.")
                    return
                t = (disc_title.get() or "").strip()
                if not t:
                    messagebox.showwarning("Missing", "Enter a title.")
                    return
                sev = disc_sev.get() or DisciplinarySeverity.low.value
                st  = disc_status.get() or DisciplinaryStatus.open.value
                desc = disc_desc.get("1.0", "end").strip()
                with SessionLocal() as db2:
                    r = db2.get(DisciplinaryRecord, rec_id)
                    if not r:
                        messagebox.showerror("Not found", "Record not found.")
                        return
                    r.title = t
                    r.severity = DisciplinarySeverity(sev)
                    r.status = DisciplinaryStatus(st)
                    r.description = desc
                    db2.commit()
                _disc_load()
                messagebox.showinfo("Updated", "Disciplinary record updated.")

            def _disc_delete():
                sel = disc_list.curselection()
                if not sel:
                    messagebox.showwarning("No selection", "Pick a record from the list.")
                    return
                try:
                    rec_id = int(disc_list.get(sel[0]).split(" • ", 1)[0])
                except Exception:
                    messagebox.showerror("Error", "Could not parse selection.")
                    return
                if not messagebox.askyesno("Delete", "Delete selected record?"):
                    return
                with SessionLocal() as db2:
                    r = db2.get(DisciplinaryRecord, rec_id)
                    if r:
                        db2.delete(r); db2.commit()
                _disc_clear_form()
                _disc_load()

            disc_list.bind("<<ListboxSelect>>", _disc_on_select)

            btnrow = ttk.Frame(discf); btnrow.pack(fill="x")
            ttk.Button(btnrow, text="New", command=_disc_clear_form).pack(side="left")
            ttk.Button(btnrow, text="Save New", command=_disc_save_new).pack(side="left", padx=6)
            ttk.Button(btnrow, text="Update Selected", command=_disc_update).pack(side="left")
            ttk.Button(btnrow, text="Delete Selected", command=_disc_delete).pack(side="left", padx=6)

            # init defaults + load list
            _disc_clear_form()
            _disc_load()

            # ---------- Add note / Rx inline ----------
            qa = ttk.LabelFrame(main, text="Add Note / Prescription", padding=8)
            qa.pack(fill="x", pady=(8, 0))
            ttk.Label(qa, text="Note").grid(row=0, column=0, sticky="w")
            note_t = tk.Text(qa, height=3, width=70); note_t.grid(row=1, column=0, columnspan=3, sticky="ew")

            ttk.Label(qa, text="Medication").grid(row=2, column=0, sticky="w", pady=(6, 0))
            med_e = ttk.Entry(qa, width=24); med_e.grid(row=3, column=0, sticky="w")
            ttk.Label(qa, text="Dose").grid(row=2, column=1, sticky="w", pady=(6, 0))
            dose_e = ttk.Entry(qa, width=10); dose_e.grid(row=3, column=1, sticky="w", padx=(6, 0))
            ttk.Label(qa, text="Frequency").grid(row=2, column=2, sticky="w", pady=(6, 0))
            freq_e = ttk.Entry(qa, width=20); freq_e.grid(row=3, column=2, sticky="w")
            ttk.Label(qa, text="Duration").grid(row=2, column=3, sticky="w", pady=(6, 0))
            dur_e = ttk.Entry(qa, width=10);  dur_e.grid(row=3, column=3, sticky="w", padx=(6, 0))
            ttk.Label(qa, text="Notes").grid(row=4, column=0, sticky="w", pady=(6, 0))
            note_e = ttk.Entry(qa, width=60); note_e.grid(row=5, column=0, columnspan=3, sticky="ew")

            def save_note_local():
                text = note_t.get("1.0", "end").strip()
                if not text:
                    messagebox.showwarning("Missing", "Enter a note.")
                    return
                with SessionLocal() as db2:
                    rec = MedicalRecord(
                        patient_id=p.id,
                        author_user_id=self.controller.current_user.id,
                        author_role=RecordAuthor.doctor,
                        text=text,
                    )
                    db2.add(rec); db2.commit()
                note_t.delete("1.0", "end")
                messagebox.showinfo("Saved", "Visit note added.")
                top.destroy()
                self._load_snapshot_from_selection()

            def save_rx_local():
                med = med_e.get().strip()
                if not med:
                    messagebox.showwarning("Missing", "Enter Medication.")
                    return
                dose = dose_e.get().strip()
                freq = freq_e.get().strip()
                dur  = dur_e.get().strip()
                note = note_e.get().strip()
                text = f"Medication: {med}; Dose: {dose}; Frequency: {freq}; Duration: {dur}; Notes: {note}"
                with SessionLocal() as db2:
                    rx = Prescription(appointment_id=a.id, text=text)
                    db2.add(rx); db2.commit()
                for e in (med_e, dose_e, freq_e, dur_e, note_e):
                    e.delete(0, "end")
                messagebox.showinfo("Saved", "Prescription created.")
                top.destroy()
                self._load_snapshot_from_selection()

            btns = ttk.Frame(main); btns.pack(fill="x", pady=(8, 0))
            ttk.Button(btns, text="Save Note", command=save_note_local).pack(side="left")
            ttk.Button(btns, text="Create Rx", command=save_rx_local).pack(side="left", padx=6)
            ttk.Button(btns, text="Close", command=top.destroy).pack(side="right")

    def _mark_completed(self):
        appt_id = self._selected_appt_id()
        if not appt_id:
            return
        with SessionLocal() as db:
            a = db.get(Appointment, appt_id)
            if a:
                a.status = AppointmentStatus.completed
                db.commit()
        self._refresh_schedule()

    def _cancel_appt(self):
        appt_id = self._selected_appt_id()
        if not appt_id:
            return
        if not messagebox.askyesno("Cancel", "Cancel selected appointment?"):
            return
        with SessionLocal() as db:
            a = db.get(Appointment, appt_id)
            if a:
                a.status = AppointmentStatus.cancelled
                db.commit()
        self._refresh_schedule()

    def _resched_appt(self):
        """Reschedule dialog with date picker and available time slots."""
        appt_id = self._selected_appt_id()
        if not appt_id:
            return

        with SessionLocal() as db:
            ap = db.get(Appointment, appt_id)
            if not ap:
                return
            current_dt = ap.scheduled_for

        top = tk.Toplevel(self)
        top.title(f"Reschedule #{appt_id}")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        ttk.Label(top, text=f"Current: {current_dt:%Y-%m-%d %H:%M}").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))

        ttk.Label(top, text="New Date").grid(row=1, column=0, sticky="w", padx=8)
        # Use topmost calendar popup
        de = ttk.Entry(top, width=16)
        de.insert(0, current_dt.strftime(DAY_FMT))
        de.grid(row=1, column=1, sticky="w", padx=8, pady=2)
        ttk.Button(top, text="Pick…", command=lambda: self._pick_into(de)).grid(row=1, column=2, sticky="w")

        ttk.Label(top, text="Available Time").grid(row=2, column=0, sticky="w", padx=8)
        time_cmb = ttk.Combobox(top, state="readonly", width=14, values=[])
        time_cmb.grid(row=2, column=1, sticky="w", padx=8, pady=2)

        def load_slots():
            date_str = de.get()
            try:
                day = datetime.strptime(date_str, DAY_FMT)
            except ValueError:
                messagebox.showerror("Invalid", "Use YYYY-MM-DD")
                return
            slots = AppointmentService.get_available_slots(self.doctor.id, day)
            # allow keeping same slot if same day
            cur_s = current_dt.strftime("%H:%M")
            if day.date() == current_dt.date() and cur_s not in slots:
                slots.append(cur_s)
                slots.sort()
            time_cmb["values"] = slots
            if slots:
                time_cmb.current(0)
            else:
                time_cmb.set("")
                messagebox.showinfo("No Slots", "No available times for that date.")

        ttk.Button(top, text="Find Slots", command=load_slots).grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 6))

        def do_resched():
            date_str = de.get()
            t = time_cmb.get().strip()
            if not t:
                messagebox.showwarning("Missing", "Choose a time slot (Find Slots).")
                return
            try:
                new_when = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M")
            except ValueError:
                messagebox.showerror("Invalid", "Bad time format.")
                return
            if new_when < datetime.now():
                messagebox.showwarning("Past time", "Please pick a future time.")
                return
            with SessionLocal() as db:
                a = db.get(Appointment, appt_id)
                if not a:
                    top.destroy(); return
                if a.scheduled_for == new_when:
                    top.destroy(); return
                conflict = db.scalar(
                    select(Appointment.id).where(
                        Appointment.doctor_id == a.doctor_id,
                        Appointment.scheduled_for == new_when,
                        Appointment.id != appt_id,
                        Appointment.status != AppointmentStatus.cancelled,
                    )
                )
                if conflict:
                    messagebox.showerror("Taken", "That time is already booked.")
                    return
                a.scheduled_for = new_when
                db.commit()
            top.destroy()
            self._refresh_schedule()

        ttk.Button(top, text="Save", command=do_resched).grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))

    def _checkin_appt(self):
        appt_id = self._selected_appt_id()
        if not appt_id:
            return
        with SessionLocal() as db:
            att = Attendance(appointment_id=appt_id, checkin_method=AttendanceMethod.web)
            db.add(att); db.commit()
        self._refresh_schedule()

    def _undo_checkin(self):
        appt_id = self._selected_appt_id()
        if not appt_id:
            return
        with SessionLocal() as db:
            db.execute(delete(Attendance).where(Attendance.appointment_id == appt_id))
            db.commit()
        self._refresh_schedule()

    def _save_note(self):
        if not self._current_patient_id:
            self._load_snapshot_from_selection()
        if not self._current_patient_id:
            messagebox.showwarning("No patient", "Select an appointment first.")
            return
        text = self.note_txt.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Missing", "Enter a note.")
            return
        with SessionLocal() as db:
            rec = MedicalRecord(
                patient_id=self._current_patient_id,
                author_user_id=self.controller.current_user.id,
                author_role=RecordAuthor.doctor,
                text=text,
            )
            db.add(rec); db.commit()
        self.note_txt.delete("1.0", "end")
        messagebox.showinfo("Saved", "Visit note added.")
        self._load_snapshot_from_selection()

    def _save_rx(self):
        if not self._current_appt_id:
            self._load_snapshot_from_selection()
        appt_id = self._current_appt_id
        if not appt_id:
            messagebox.showwarning("No selection", "Select an appointment first.")
            return
        med = self.rx_med.get().strip()
        if not med:
            messagebox.showwarning("Missing", "Enter Medication.")
            return
        dose = self.rx_dose.get().strip()
        freq = self.rx_freq.get().strip()
        dur  = self.rx_dur.get().strip()
        note = self.rx_note.get().strip()
        text = f"Medication: {med}; Dose: {dose}; Frequency: {freq}; Duration: {dur}; Notes: {note}"
        with SessionLocal() as db:
            rx = Prescription(appointment_id=appt_id, text=text)
            db.add(rx); db.commit()
        for e in (self.rx_med, self.rx_dose, self.rx_freq, self.rx_dur, self.rx_note):
            e.delete(0, "end")
        messagebox.showinfo("Saved", "Prescription created.")
        self._load_snapshot_from_selection()

    # ====================================================
    # Data ops — Availability
    # ====================================================
    def _refresh_availability(self):
        for i in self.tree_av.get_children():
            self.tree_av.delete(i)
        if not self.doctor:
            return
        with SessionLocal() as db:
            rows = db.scalars(
                select(DoctorAvailability)
                .where(DoctorAvailability.doctor_id == self.doctor.id)
                .order_by(DoctorAvailability.day.asc())
            ).all()
            for r in rows:
                self.tree_av.insert("", "end", values=(r.id, r.day.strftime(DAY_FMT), r.start_time, r.end_time, r.slot_minutes))

    def _on_select_availability(self):
        """When a row is selected, prefill the form for easy editing."""
        sel = self.tree_av.selection()
        if not sel:
            return
        _, day, start, end, slot = self.tree_av.item(sel[0], "values")
        # date
        self.av_date.delete(0, "end")
        self.av_date.insert(0, day)
        # times & slot
        self.av_start.delete(0, "end"); self.av_start.insert(0, start)
        self.av_end.delete(0, "end");   self.av_end.insert(0, end)
        self.av_slot.delete(0, "end");  self.av_slot.insert(0, str(slot))

    def _save_availability(self):
        if not self.doctor:
            messagebox.showerror("No doctor", "This dashboard requires a doctor account.")
            return
        # read date
        date_s = self.av_date.get()
        try:
            day = datetime.strptime(date_s.strip(), DAY_FMT)
        except Exception:
            messagebox.showerror("Invalid date", "Use YYYY-MM-DD")
            return

        # read times
        start_s = (self.av_start.get() or "").strip()
        end_s   = (self.av_end.get() or "").strip()
        slot_s  = (self.av_slot.get() or "").strip() or "30"

        hp = _parse_hhmm(start_s); ep = _parse_hhmm(end_s)
        if not hp:
            messagebox.showerror("Invalid time", "Start time must be HH:MM (e.g., 09:00)")
            return
        if not ep:
            messagebox.showerror("Invalid time", "End time must be HH:MM (e.g., 17:00)")
            return
        try:
            slot_i = int(slot_s)
            if slot_i <= 0:
                raise ValueError()
        except Exception:
            messagebox.showerror("Invalid slot", "Slot minutes must be a positive integer")
            return

        sh, sm = hp; eh, em = ep
        start_dt = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_dt   = day.replace(hour=eh, minute=em, second=0, microsecond=0)
        if not (start_dt < end_dt):
            messagebox.showerror("Invalid range", "Start time must be before End time")
            return

        # upsert per-day rule (index-friendly)
        day0, day1 = _day_range(day)
        with SessionLocal() as db:
            existing = db.scalar(
                select(DoctorAvailability)
                .where(
                    DoctorAvailability.doctor_id == self.doctor.id,
                    DoctorAvailability.day >= day0,
                    DoctorAvailability.day < day1,
                )
                .order_by(DoctorAvailability.id.desc())
            )
            if existing:
                existing.start_time   = start_s
                existing.end_time     = end_s
                existing.slot_minutes = slot_i
                existing.day          = day0
                action = "updated"
            else:
                db.add(
                    DoctorAvailability(
                        doctor_id=self.doctor.id,
                        day=day0,
                        start_time=start_s,
                        end_time=end_s,
                        slot_minutes=slot_i,
                    )
                )
                action = "added"
            db.commit()
        self._refresh_availability()
        messagebox.showinfo("Saved", f"Availability {action} for {day.strftime(DAY_FMT)}: {start_s}-{end_s} ({slot_i} min)")

    def _del_availability(self):
        sel = self.tree_av.selection()
        if not sel:
            messagebox.showwarning("No selection", "Choose a rule to delete.")
            return
        av_id = int(self.tree_av.item(sel[0], "values")[0])
        if not messagebox.askyesno("Delete", "Delete selected availability rule?"):
            return
        with SessionLocal() as db:
            av = db.get(DoctorAvailability, av_id)
            if av:
                db.delete(av); db.commit()
        self._refresh_availability()

    # ====================================================
    # Data ops — Requests
    # ====================================================
    def _selected_request_id(self) -> int | None:
        sel = self.tree_req.selection()
        if not sel:
            return None
        return int(self.tree_req.item(sel[0], "values")[0])

    def _refresh_requests(self):
        for i in self.tree_req.get_children():
            self.tree_req.delete(i)
        if not self.doctor:
            return
        with SessionLocal() as db:
            stmt = (
                select(Appointment)
                .options(selectinload(Appointment.patient).selectinload(Patient.user))
                .where(Appointment.doctor_id == self.doctor.id)
                .order_by(Appointment.scheduled_for.asc())
            )
            appts = db.scalars(stmt).all()
            for a in appts:
                s_val = getattr(a.status, "value", str(a.status)).lower()
                if s_val != "requested":
                    continue
                patient_label = a.patient.user.full_name or a.patient.user.email
                self.tree_req.insert(
                    "", "end",
                    values=(a.id, a.scheduled_for.strftime(DATE_FMT), patient_label, a.reason or "")
                )

    def _approve_request(self):
        appt_id = self._selected_request_id()
        if not appt_id:
            messagebox.showwarning("No selection", "Select a request.")
            return
        with SessionLocal() as db:
            ap = db.get(Appointment, appt_id)
            if not ap:
                return
            # ensure requested time is still free
            conflict = db.scalar(
                select(Appointment.id).where(
                    Appointment.doctor_id == ap.doctor_id,
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
        messagebox.showinfo("Approved", "Request approved and booked.")
        self._refresh_requests()
        self._refresh_schedule()

    def _assign_slot_request(self):
        appt_id = self._selected_request_id()
        if not appt_id:
            messagebox.showwarning("No selection", "Select a request.")
            return

        with SessionLocal() as db:
            ap = db.get(Appointment, appt_id)
            if not ap:
                return
            current_dt = ap.scheduled_for
            p = db.get(Patient, ap.patient_id)
            u = db.get(User, p.user_id) if p else None
            patient_label = (u.full_name or u.email) if u else f"Patient#{ap.patient_id}"

        top = tk.Toplevel(self)
        top.title(f"Assign Slot for Request #{appt_id}")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        ttk.Label(top, text=f"Patient: {patient_label}").grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(top, text=f"Requested: {current_dt:%Y-%m-%d %H:%M}").grid(row=1, column=0, columnspan=3, sticky="w", padx=8, pady=(0, 6))

        ttk.Label(top, text="Date").grid(row=2, column=0, sticky="w", padx=8)
        de = ttk.Entry(top, width=16)
        de.insert(0, current_dt.strftime(DAY_FMT))
        de.grid(row=2, column=1, sticky="w", padx=8, pady=2)
        ttk.Button(top, text="Pick…", command=lambda: self._pick_into(de)).grid(row=2, column=2, sticky="w")

        ttk.Label(top, text="Available Time").grid(row=3, column=0, sticky="w", padx=8)
        time_cmb = ttk.Combobox(top, state="readonly", width=14, values=[])
        time_cmb.grid(row=3, column=1, sticky="w", padx=8, pady=2)

        def load_slots():
            date_str = de.get()
            try:
                day = datetime.strptime(date_str, DAY_FMT)
            except ValueError:
                messagebox.showerror("Invalid", "Use YYYY-MM-DD")
                return
            slots = AppointmentService.get_available_slots(self.doctor.id, day)
            time_cmb["values"] = slots
            if slots:
                time_cmb.current(0)
            else:
                time_cmb.set("")
                messagebox.showinfo("No Slots", "No available times for that date.")

        ttk.Button(top, text="Find Slots", command=load_slots).grid(row=4, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 6))

        def do_assign():
            date_str = de.get()
            t = time_cmb.get().strip()
            if not t:
                messagebox.showwarning("Missing", "Choose a time slot (Find Slots).")
                return
            try:
                new_when = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M")
            except ValueError:
                messagebox.showerror("Invalid", "Bad time format.")
                return
            if new_when < datetime.now():
                messagebox.showwarning("Past time", "Please pick a future time.")
                return
            with SessionLocal() as db:
                a = db.get(Appointment, appt_id)
                if not a:
                    top.destroy(); return
                conflict = db.scalar(
                    select(Appointment.id).where(
                        Appointment.doctor_id == a.doctor_id,
                        Appointment.scheduled_for == new_when,
                        Appointment.id != appt_id,
                        Appointment.status != AppointmentStatus.cancelled,
                    )
                )
                if conflict:
                    messagebox.showerror("Taken", "That time is already booked.")
                    return
                a.scheduled_for = new_when
                a.status = AppointmentStatus.booked
                db.commit()
            top.destroy()
            self._refresh_requests()
            self._refresh_schedule()

        ttk.Button(top, text="Assign", command=do_assign).grid(row=5, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))

    def _decline_request(self):
        appt_id = self._selected_request_id()
        if not appt_id:
            messagebox.showwarning("No selection", "Select a request.")
            return
        if not messagebox.askyesno("Decline", "Decline this appointment request?"):
            return
        with SessionLocal() as db:
            a = db.get(Appointment, appt_id)
            if a:
                a.status = AppointmentStatus.cancelled
                db.commit()
        self._refresh_requests()

    # ====================================================
    # Data ops — Notifications
    # ====================================================
    def _refresh_notifications(self):
        for i in self.tree_nf.get_children():
            self.tree_nf.delete(i)
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

                self.tree_nf.insert(
                    "", "end",
                    values=(n.id, n.created_at.strftime(DATE_FMT), getattr(n, "title", "(notification)"), who, "yes" if getattr(n, "read", False) else "")
                )
                if not getattr(n, "read", False):
                    unread += 1
        # badge
        title = "Notifications" if unread == 0 else f"Notifications ({unread})"
        idx = self.nb.index(self.tab_notif)
        self.nb.tab(idx, text=title)

    def _notif_mark_read(self):
        sel = self.tree_nf.selection()
        if not sel:
            return
        notif_id = int(self.tree_nf.item(sel[0], "values")[0])
        with SessionLocal() as db:
            n = db.get(Notification, notif_id)
            if n:
                n.read = True
                db.commit()
        self._refresh_notifications()

    # ====================================================
    # Data ops — Support
    # ====================================================
    def _refresh_support(self):
        for i in self.tree_tk.get_children():
            self.tree_tk.delete(i)
        user = getattr(self.controller, "current_user", None)
        if not user:
            return
        with SessionLocal() as db:
            rows = db.scalars(
                select(SupportTicket)
                .where(SupportTicket.user_id == user.id)
                .order_by(SupportTicket.created_at.desc())
            ).all()
            for t in rows:
                self.tree_tk.insert("", "end", values=(t.id, t.created_at.strftime(DATE_FMT), t.subject, t.status.value))

    def _create_ticket(self):
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
        self._refresh_support()
        messagebox.showinfo("Created", "Support ticket created.")

    # ====================================================
    # Data ops — KPIs
    # ====================================================
    def _refresh_kpis(self):
        if not self.doctor:
            return
        day0, day1 = _day_range(datetime.now())

        with SessionLocal() as db:
            appts = db.scalars(
                select(Appointment)
                .where(
                    Appointment.doctor_id == self.doctor.id,
                    Appointment.scheduled_for >= day0,
                    Appointment.scheduled_for < day1,
                )
                .order_by(Appointment.scheduled_for.asc())
            ).all()
            total = len(appts)
            completed = sum(1 for a in appts if getattr(a.status, "value", a.status) == "completed")
            cancelled = sum(1 for a in appts if getattr(a.status, "value", a.status) == "cancelled")

            ap_ids = [a.id for a in appts]
            checked_in = 0
            if ap_ids:
                rows = db.execute(
                    select(func.count(func.distinct(Attendance.appointment_id)))
                    .where(Attendance.appointment_id.in_(ap_ids))
                ).all()
                checked_in = rows[0][0] if rows else 0

            # utilisation (booked/total slots)
            av = db.scalar(
                select(DoctorAvailability)
                .where(
                    DoctorAvailability.doctor_id == self.doctor.id,
                    DoctorAvailability.day >= day0,
                    DoctorAvailability.day < day1,
                )
                .order_by(DoctorAvailability.id.desc())
            )
            start_h, start_m, end_h, end_m, slot_min = 9, 0, 17, 0, 30
            if av:
                try:
                    start_h, start_m = map(int, av.start_time.split(":"))
                    end_h, end_m = map(int, av.end_time.split(":"))
                    slot_min = av.slot_minutes or 30
                except Exception:
                    pass
            total_slots = 0
            t = day0.replace(hour=start_h, minute=start_m)
            end = day0.replace(hour=end_h, minute=end_m)
            while t < end:
                total_slots += 1
                t += timedelta(minutes=slot_min)
            booked = sum(1 for a in appts if getattr(a.status, "value", a.status) != "cancelled")
            util = f"{booked}/{total_slots}" if total_slots else "n/a"

            next_ap = next((a for a in appts if getattr(a.status, "value", a.status) != "cancelled"), None)
            next_label = ""
            if next_ap:
                p = db.get(Patient, next_ap.patient_id)
                u = db.get(User, p.user_id) if p else None
                next_label = f"{next_ap.scheduled_for:%H:%M} — {(u.full_name or u.email) if u else 'Patient'}"

        text = (
            f"Doctor: {self.controller.current_user.full_name or self.controller.current_user.email}\n"
            f"Date: {day0:%Y-%m-%d}\n\n"
            f"Total bookings: {total}\n"
            f"Checked-in: {checked_in}\n"
            f"Completed: {completed}\n"
            f"Cancelled: {cancelled}\n"
            f"Utilisation (booked/slots): {util}\n"
            f"Next patient: {next_label or '-'}\n"
        )
        self.kpi_summary.config(state="normal")
        self.kpi_summary.delete("1.0", "end")
        self.kpi_summary.insert("end", text)
        self.kpi_summary.config(state="disabled")

    # ====================================================
    # Staff Check-ins — helper
    # ====================================================
    def refresh_checkins(self):
        """Populate the 'Today’s Staff Check-ins' table."""
        if not hasattr(self, "checkin_tv"):
            return
        # clear table
        for i in self.checkin_tv.get_children():
            self.checkin_tv.delete(i)

        # fetch today’s check-ins
        rows = []
        try:
            rows = today_checkins()  # expects objects with user, role, status, method, location, ts
        except Exception as e:
            print("today_checkins() failed:", e)
            rows = []

        for r in rows:
            who = getattr(r, "user", None)
            who_label = getattr(who, "full_name", None) or getattr(who, "email", "Unknown")
            ts = ""
            try:
                ts = r.ts.strftime("%H:%M") if getattr(r, "ts", None) else ""
            except Exception:
                pass
            role = getattr(r, "role", "") or ""
            status = getattr(getattr(r, "status", None), "value", getattr(r, "status", "")) or ""
            method = getattr(getattr(r, "method", None), "value", getattr(r, "method", "")) or ""
            location = getattr(r, "location", "") or ""
            self.checkin_tv.insert("", "end", values=(ts, who_label, role, status, method, location))

    # ====================================================
    # UI helpers — topmost calendar picker
    # ====================================================
    def _pick_into(self, entry: tk.Entry):
        """Open a topmost calendar and put the chosen date into the given Entry."""
        # get current value as initial date if valid
        init: Optional[date] = None
        try:
            init = datetime.strptime(entry.get().strip(), DAY_FMT).date()
        except Exception:
            init = None

        d = pick_date(self, init)
        if d:
            entry.delete(0, "end")
            entry.insert(0, d.strftime(DAY_FMT))
