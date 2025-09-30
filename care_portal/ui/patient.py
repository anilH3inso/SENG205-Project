# care_portal/ui/patient.py
from __future__ import annotations

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from datetime import datetime, timedelta, date
from typing import Optional, List, Tuple, Any

# ---- Calendar picker (tkcalendar) ----
try:
    from tkcalendar import DateEntry, Calendar  # pip install tkcalendar
    HAS_TKCAL = True
except Exception:
    HAS_TKCAL = False

from sqlalchemy import select, join
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    User,
    Role,
    Patient,
    Appointment,
    AppointmentStatus,
    MedicalRecord,
    RecordAuthor,
    Billing,
    BillingStatus,
    PaymentMethod,
    Doctor,
    Notification,
    Prescription,
)

# Try importing disciplinary models if present (UI hides if missing)
try:
    from ..models import DisciplinaryRecord, DisciplinaryStatus, DisciplinarySeverity  # type: ignore
    HAS_DISCIPLINARY = True
except Exception:
    HAS_DISCIPLINARY = False
from ..services.notifications import notify_receptionists_about_request

from ..services.appointments import AppointmentService
from .base import BaseFrame

# ------------------------------ UI CONFIG ------------------------------
DATE_FMT = "%Y-%m-%d %H:%M"
DAY_FMT = "%Y-%m-%d"

LIGHT_GREEN = "#c9f7d4"
LIGHT_RED = "#ffd9d9"

# ---- Optional PDF export ----
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    HAS_PDF = True
except Exception:
    HAS_PDF = False


# =============================================================================
# Patient Portal
# =============================================================================
class PatientFrame(BaseFrame):
    """
    Patient dashboard:
      - Booking & Appointments
      - Treatment History (Doctor notes + Prescription summaries)
      - Medical Records (self notes) + Refresh button
      - Billing
      - Prescriptions
      - Notifications
      - Disciplinary (if enabled)
    """

    title = "Patient Portal"

    # ------------------------------ INIT ------------------------------
    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # Build inside scrollable body
        root = ttk.Frame(self.body, padding=8)
        root.pack(fill="both", expand=True)

        # Top toolbar (right side)
        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(0, 4))
        ttk.Button(toolbar, text="Profile", command=self._open_profile_dialog).pack(side="right")

        # ---------------- Left: Booking Form ----------------
        left = ttk.LabelFrame(root, text="Book an Appointment", padding=10)
        left.pack(side="left", fill="y", padx=(0, 8))

        # Specialty + search
        filt = ttk.Frame(left)
        filt.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        filt.columnconfigure(1, weight=1)

        ttk.Label(filt, text="Specialty").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.spec_cmb = ttk.Combobox(filt, state="readonly", width=16, values=["All specialties"])
        self.spec_cmb.current(0)
        self.spec_cmb.grid(row=0, column=1, sticky="ew")

        ttk.Label(filt, text="Search").grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.search_in = ttk.Entry(filt)
        self.search_in.grid(row=1, column=1, sticky="ew")

        # Caches
        self._current_slots: list[str] = []
        self._available_dates: set[str] = set()
        self._date_window_days = 90  # booking horizon

        # Doctor picker
        ttk.Label(left, text="Doctor").grid(row=2, column=0, sticky="w")
        self.doctor_cmb = ttk.Combobox(left, state="readonly", width=32)
        self.doctor_cmb.grid(row=3, column=0, sticky="ew", pady=(2, 8))

        # Date picker (Calendar preferred so colour is visible)
        ttk.Label(left, text="Date").grid(row=4, column=0, sticky="w")
        if HAS_TKCAL:
            self.cal = Calendar(left, selectmode="day", date_pattern="yyyy-mm-dd")
            self.cal.grid(row=5, column=0, sticky="ew", pady=(2, 8))
            self.cal.bind("<<CalendarSelected>>", lambda _e: self.refresh_slots())
        else:
            self.cal = None
            self.date_in = ttk.Entry(left, width=32)
            self.date_in.insert(0, datetime.now().strftime(DAY_FMT))
            self.date_in.grid(row=5, column=0, sticky="ew", pady=(2, 8))

        # Time slots
        ttk.Label(left, text="Time (available)").grid(row=6, column=0, sticky="w")
        self.time_cmb = ttk.Combobox(left, state="disabled", width=32, values=[])
        self.time_cmb.grid(row=7, column=0, sticky="ew", pady=(2, 8))

        # Reason
        ttk.Label(left, text="Reason").grid(row=8, column=0, sticky="w")
        self.reason_in = ttk.Entry(left, width=32)
        self.reason_in.grid(row=9, column=0, sticky="ew", pady=(2, 8))
        self.reason_in.bind("<Return>", lambda _e: self.book())  # Enter to book

        # Actions
        ttk.Button(left, text="Find Slots", command=self.find_slots).grid(row=10, column=0, sticky="ew")
        ttk.Button(left, text="Book", command=self.book).grid(row=11, column=0, sticky="ew", pady=(2, 6))

        # Request area
        req = ttk.LabelFrame(left, text="No slots? Request a time", padding=8)
        req.grid(row=12, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(req, text="Date").grid(row=0, column=0, sticky="w")
        if HAS_TKCAL:
            self.req_date_in = DateEntry(req, width=16, date_pattern="yyyy-mm-dd")
        else:
            self.req_date_in = ttk.Entry(req, width=18)
            self.req_date_in.insert(0, datetime.now().strftime(DAY_FMT))
        self.req_date_in.grid(row=1, column=0, sticky="w", pady=(2, 6))
        ttk.Label(req, text="Desired Time (HH:MM)").grid(row=2, column=0, sticky="w")
        self.req_time_in = ttk.Entry(req, width=18)
        self.req_time_in.grid(row=3, column=0, sticky="w", pady=(2, 6))
        self.req_time_in.bind("<Return>", lambda _e: self.request_booking())
        ttk.Button(req, text="Request", command=self.request_booking).grid(row=4, column=0, sticky="ew")

        # ---------------- Right: Tabs ----------------
        self.nb = ttk.Notebook(root)
        self.nb.pack(side="left", fill="both", expand=True)

        self.tab_appt = ttk.Frame(self.nb)
        self.tab_hist = ttk.Frame(self.nb)     # Treatment History (Records + Prescriptions)
        self.tab_med  = ttk.Frame(self.nb)
        self.tab_bill = ttk.Frame(self.nb)
        self.tab_rx   = ttk.Frame(self.nb)
        self.tab_notif = ttk.Frame(self.nb)
        self.tab_disc  = ttk.Frame(self.nb)     # Disciplinary (optional)

        self.nb.add(self.tab_appt, text="My Appointments")
        self.nb.add(self.tab_hist, text="Treatment History")
        self.nb.add(self.tab_med,  text="Medical Records")
        self.nb.add(self.tab_bill, text="Billing")
        self.nb.add(self.tab_rx,   text="Prescriptions")
        self.nb.add(self.tab_notif, text="Notifications")
        self.nb.add(self.tab_disc,  text="Disciplinary")

        # ---- Appointments tab ----
        ap_cols = ("id", "when", "doctor", "reason", "status")
        self.tree_ap = ttk.Treeview(self.tab_appt, columns=ap_cols, show="headings", height=12)
        for c, w in (("id", 60), ("when", 150), ("doctor", 240), ("reason", 240), ("status", 100)):
            self.tree_ap.heading(c, text=c.title())
            self.tree_ap.column(c, width=w)
        self.tree_ap.pack(fill="both", expand=True, padx=6, pady=6)

        ap_btns = ttk.Frame(self.tab_appt)
        ap_btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(ap_btns, text="Cancel Selected", command=self.cancel_selected).pack(side="left")
        ttk.Button(ap_btns, text="Request Reschedule", command=self.request_reschedule_selected).pack(side="left", padx=6)
        ttk.Button(ap_btns, text="Refresh", command=self.refresh_appointments).pack(side="left", padx=6)

        # ---- Treatment History ----
        # Unified view: Medical Records + Prescriptions (summaries)
        th_cols = ("id", "type", "date", "author", "role", "summary")
        self.tree_hist = ttk.Treeview(self.tab_hist, columns=th_cols, show="headings", height=12)
        for c, title, w in [
            ("id", "ID", 60),
            ("type", "Type", 90),
            ("date", "Date", 150),
            ("author", "Author", 220),
            ("role", "Role", 110),
            ("summary", "Summary", 420),
        ]:
            self.tree_hist.heading(c, text=title)
            self.tree_hist.column(c, width=w)
        self.tree_hist.pack(fill="both", expand=True, padx=6, pady=6)

        th_btns = ttk.Frame(self.tab_hist)
        th_btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(th_btns, text="View", command=self.view_selected_record).pack(side="left")
        ttk.Button(th_btns, text="Download", command=self.download_selected_record).pack(side="left", padx=6)
        ttk.Button(th_btns, text="Refresh", command=self.refresh_treatment_history).pack(side="left", padx=6)

        # ---- Medical Records tab (self add) ----
        info = ttk.LabelFrame(self.tab_med, text="Profile Snapshot", padding=8)
        info.pack(fill="x", padx=6, pady=6)
        ttk.Label(info, text="Allergies").grid(row=0, column=0, sticky="nw")
        self.allergies_txt = tk.Text(info, height=3, width=70, state="disabled")
        self.allergies_txt.grid(row=0, column=1, sticky="ew", padx=6)

        controls = ttk.LabelFrame(self.tab_med, text="Add Medical Record", padding=8)
        controls.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(controls, text="Record / Note").grid(row=0, column=0, sticky="w")
        self.rec_txt = tk.Text(controls, height=4, width=60)
        self.rec_txt.grid(row=1, column=0, sticky="ew")
        ttk.Button(controls, text="Add Record", command=self.add_med_record).grid(row=1, column=1, padx=8)

        # NEW: explicit Refresh button so users can reload immediately after adding
        tt = ttk.Frame(self.tab_med)
        tt.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(tt, text="Refresh Medical Records", command=self.refresh_treatment_history).pack(side="left")
        ttk.Button(tt, text="Refresh Allergies", command=self.load_allergies).pack(side="left", padx=6)

        # ---- Billing tab ----
        bill_cols = ("id", "desc", "amount", "status", "paid_at", "method")
        self.tree_bill = ttk.Treeview(self.tab_bill, columns=bill_cols, show="headings", height=10)
        headings = {
            "id": ("ID", 60),
            "desc": ("Description", 240),
            "amount": ("Amount", 100),
            "status": ("Status", 100),
            "paid_at": ("Paid At", 160),
            "method": ("Method", 120),
        }
        for c, (title, w) in headings.items():
            self.tree_bill.heading(c, text=title)
            self.tree_bill.column(c, width=w)
        self.tree_bill.pack(fill="both", expand=True, padx=6, pady=6)

        payf = ttk.Frame(self.tab_bill)
        payf.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(payf, text="Mark Selected as Paid", command=self.mark_bill_paid).pack(side="left")

        # ---- Prescriptions tab ----
        rx_cols = ("id", "date", "doctor", "summary")
        self.tree_rx = ttk.Treeview(self.tab_rx, columns=rx_cols, show="headings", height=10)
        for c, title, w in [
            ("id", "ID", 60),
            ("date", "Date", 150),
            ("doctor", "Doctor", 220),
            ("summary", "Summary", 380),
        ]:
            self.tree_rx.heading(c, text=title)
            self.tree_rx.column(c, width=w)
        self.tree_rx.pack(fill="both", expand=True, padx=6, pady=6)

        rx_btns = ttk.Frame(self.tab_rx)
        rx_btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(rx_btns, text="Refresh", command=self.refresh_prescriptions).pack(side="left")
        ttk.Button(rx_btns, text="Download Selected", command=self.download_prescription_selected).pack(side="left", padx=6)

        # ---- Notifications tab ----
        nf_cols = ("id", "time", "title", "read")
        self.tree_nf = ttk.Treeview(self.tab_notif, columns=nf_cols, show="headings", height=10)
        for c, title, w in [
            ("id", "ID", 60),
            ("time", "Time", 160),
            ("title", "Title", 420),
            ("read", "Read", 80),
        ]:
            self.tree_nf.heading(c, text=title)
            self.tree_nf.column(c, width=w)
        self.tree_nf.pack(fill="both", expand=True, padx=6, pady=6)
        nf_btns = ttk.Frame(self.tab_notif)
        nf_btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(nf_btns, text="Mark as read", command=self._notif_mark_read).pack(side="left")
        ttk.Button(nf_btns, text="Refresh", command=self.refresh_notifications).pack(side="left", padx=6)

        # ---- Disciplinary tab ----
        ds_cols = ("id", "date", "severity", "status", "title")
        self.tree_disc = ttk.Treeview(self.tab_disc, columns=ds_cols, show="headings", height=10)
        for c, title, w in [
            ("id", "ID", 60),
            ("date", "Date", 150),
            ("severity", "Severity", 110),
            ("status", "Status", 110),
            ("title", "Title", 460),
        ]:
            self.tree_disc.heading(c, text=title)
            self.tree_disc.column(c, width=w)
        self.tree_disc.pack(fill="both", expand=True, padx=6, pady=6)

        ds_btns = ttk.Frame(self.tab_disc)
        ds_btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(ds_btns, text="View", command=self.view_selected_disciplinary).pack(side="left")
        ttk.Button(ds_btns, text="Acknowledge", command=self.ack_selected_disciplinary).pack(side="left", padx=6)
        ttk.Button(ds_btns, text="Refresh", command=self.refresh_disciplinary).pack(side="left", padx=6)

        # Staff-only management buttons
        if self._can_manage_disciplinary() and HAS_DISCIPLINARY:
            ttk.Button(ds_btns, text="New", command=self.new_disciplinary).pack(side="left", padx=(12, 6))
            ttk.Button(ds_btns, text="Edit", command=self.edit_selected_disciplinary).pack(side="left")

        # Hide the disciplinary tab if models not present
        if not HAS_DISCIPLINARY:
            self.nb.hide(self.tab_disc)

        # Header-level Notifications button (quick jump)
        try:
            header = self.user_lbl.master
            self.btn_notif = ttk.Button(
                header,
                text="Notifications",
                command=lambda: (self.nb.select(self.tab_notif), self.refresh_notifications()),
            )
            self.btn_notif.pack(side="right", padx=(6, 0))
        except Exception:
            self.btn_notif = None

        # ---------------- Data caches ----------------
        self.patient: Optional[Patient] = None
        self._all_doctors: list[Doctor] = []
        self.doctors: dict[str, int] = {}
        self.doctor_labels: dict[int, str] = {}

        # Initial loads
        self.load_patient()
        self.refresh_doctors()
        self.refresh_appointments()
        self.refresh_treatment_history()
        self.refresh_billing()
        self.refresh_prescriptions()
        self.load_allergies()
        self.refresh_notifications()
        self.refresh_disciplinary()

        # Wire filters
        self.spec_cmb.bind("<<ComboboxSelected>>", lambda _e: self.apply_doctor_filters())
        self.search_in.bind("<KeyRelease>", lambda _e: self.apply_doctor_filters())
        self.doctor_cmb.bind("<<ComboboxSelected>>", lambda _e: self._on_doctor_change())

    # ---------------- Role helpers ----------------
    def _current_role(self) -> str:
        user = getattr(self.controller, "current_user", None)
        role = getattr(user, "role", None)
        return getattr(role, "value", role) if role else ""

    def _can_manage_disciplinary(self) -> bool:
        return self._current_role() in {"doctor", "admin"}

    # ---------------- Safe user switching ----------------
    def set_user(self, user):
        super().set_user(user)
        if not hasattr(self, "tree_ap"):
            self._pending_user = user
            return
        self.load_patient()
        self.refresh_doctors()
        self.refresh_appointments()
        self.refresh_treatment_history()
        self.refresh_billing()
        self.refresh_prescriptions()
        self.load_allergies()
        self.refresh_notifications()
        self.refresh_disciplinary()

    # inside class PatientFrame ...

    def load_allergies(self):
        """Reload the patient's allergies text into the snapshot box."""
        if not getattr(self, "patient", None):
            return
        try:
            with SessionLocal() as db:
                p = db.get(Patient, self.patient.id)
                text = p.allergies if p and p.allergies else ""
        except Exception:
            text = ""
        # Update the UI text box
        try:
            self.allergies_txt.config(state="normal")
            self.allergies_txt.delete("1.0", "end")
            self.allergies_txt.insert("end", text)
            self.allergies_txt.config(state="disabled")
        except Exception:
            pass

    # ---------------- Profile dialog ----------------
    def _open_profile_dialog(self):
        """Open a modal to edit patient & user fields."""
        if not self.patient or not getattr(self.controller, "current_user", None):
            messagebox.showerror("Error", "Not logged in as patient.")
            return

        with SessionLocal() as db:
            p = db.get(Patient, self.patient.id)
            u = db.get(User, p.user_id) if p else None
        if not p or not u:
            messagebox.showerror("Error", "Patient profile not found.")
            return

        top = tk.Toplevel(self)
        top.title("Edit Profile")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        frm = ttk.Frame(top, padding=10)
        frm.pack(fill="both", expand=True)

        # --- User fields ---
        r = 0
        ttk.Label(frm, text="Full Name").grid(row=r, column=0, sticky="w")
        full_in = ttk.Entry(frm, width=40); full_in.insert(0, u.full_name or ""); full_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Email (login)").grid(row=r, column=0, sticky="w")
        email_in = ttk.Entry(frm, width=40); email_in.insert(0, u.email or ""); email_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Phone").grid(row=r, column=0, sticky="w")
        phone_in = ttk.Entry(frm, width=40); phone_in.insert(0, u.phone or ""); phone_in.grid(row=r, column=1, sticky="ew"); r += 1

        # --- Patient fields ---
        ttk.Label(frm, text="DOB (YYYY-MM-DD)").grid(row=r, column=0, sticky="w")
        dob_in = ttk.Entry(frm, width=40); dob_in.insert(0, (p.dob.isoformat() if p.dob else "")); dob_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Gender").grid(row=r, column=0, sticky="w")
        gender_in = ttk.Entry(frm, width=40); gender_in.insert(0, p.gender or ""); gender_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Address").grid(row=r, column=0, sticky="w")
        addr_in = ttk.Entry(frm, width=40); addr_in.insert(0, p.address or ""); addr_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Insurance No.").grid(row=r, column=0, sticky="w")
        ins_in = ttk.Entry(frm, width=40); ins_in.insert(0, p.insurance_no or ""); ins_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Emergency Contact Name").grid(row=r, column=0, sticky="w")
        em_name_in = ttk.Entry(frm, width=40); em_name_in.insert(0, p.emergency_contact_name or ""); em_name_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Emergency Contact Phone").grid(row=r, column=0, sticky="w")
        em_phone_in = ttk.Entry(frm, width=40); em_phone_in.insert(0, p.emergency_contact_phone or ""); em_phone_in.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Allergies").grid(row=r, column=0, sticky="nw")
        alg_txt = tk.Text(frm, height=3, width=40); alg_txt.insert("1.0", p.allergies or ""); alg_txt.grid(row=r, column=1, sticky="ew"); r += 1

        ttk.Label(frm, text="Chronic Conditions").grid(row=r, column=0, sticky="nw")
        cond_txt = tk.Text(frm, height=3, width=40); cond_txt.insert(0.0, p.chronic_conditions or ""); cond_txt.grid(row=r, column=1, sticky="ew"); r += 1

        frm.columnconfigure(1, weight=1)

        btns = ttk.Frame(top); btns.pack(fill="x", pady=(6, 8), padx=10)
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right", padx=(6, 0))

        def save():
            # Validate DOB
            dob_val = dob_in.get().strip()
            dob_parsed = None
            if dob_val:
                try:
                    dob_parsed = datetime.strptime(dob_val, DAY_FMT).date()
                except ValueError:
                    messagebox.showerror("Invalid DOB", "Use format YYYY-MM-DD.")
                    return

            with SessionLocal() as db:
                p2 = db.get(Patient, p.id); u2 = db.get(User, u.id)
                if not p2 or not u2:
                    messagebox.showerror("Error", "Profile not found.")
                    return

                # Update user
                u2.full_name = full_in.get().strip()
                u2.email = email_in.get().strip()
                u2.phone = phone_in.get().strip()

                # Update patient
                p2.dob = dob_parsed
                p2.gender = gender_in.get().strip()
                p2.address = addr_in.get().strip()
                p2.insurance_no = ins_in.get().strip()
                p2.emergency_contact_name = em_name_in.get().strip()
                p2.emergency_contact_phone = em_phone_in.get().strip()
                p2.allergies = alg_txt.get("1.0", "end").strip()
                p2.chronic_conditions = cond_txt.get("1.0", "end").strip()

                db.commit()

            # refresh local caches & header
            self.load_patient(auto_create=False)
            if hasattr(self.controller, "current_user"):
                self.controller.current_user.full_name = full_in.get().strip()
                self.controller.current_user.email = email_in.get().strip()
                self.controller.current_user.phone = phone_in.get().strip()
                self.set_user(self.controller.current_user)

            self.load_allergies()
            messagebox.showinfo("Saved", "Profile updated.")
            top.destroy()

        ttk.Button(btns, text="Save", command=save).pack(side="right")

    # ---------------- Utility ----------------
    def _get_selected_date_str(self) -> str:
        """Return selected date as YYYY-MM-DD."""
        if HAS_TKCAL and getattr(self, "cal", None) is not None:
            d = self.cal.get_date()
            return d if isinstance(d, str) else d.strftime(DAY_FMT)
        return self.date_in.get()

    # ---------------- Data loading ----------------
    def load_patient(self, auto_create: bool = True) -> None:
        u = getattr(self.controller, "current_user", None)
        if not u:
            self.patient = None
            return

        role_val = getattr(getattr(u, "role", None), "value", getattr(u, "role", None))
        with SessionLocal() as db:
            pat = db.scalar(select(Patient).options(selectinload(Patient.user)).where(Patient.user_id == u.id))
            if not pat and auto_create and role_val == "patient":
                pat = Patient(user_id=u.id)
                db.add(pat); db.commit(); db.refresh(pat)
                pat = db.scalar(select(Patient).options(selectinload(Patient.user)).where(Patient.id == pat.id))
            self.patient = pat

    # ---------- Doctor list + filters ----------
    def refresh_doctors(self):
        try:
            docs = AppointmentService.list_doctors()
        except Exception as e:
            print("[PatientFrame] list_doctors error:", e)
            docs = []

        self._all_doctors = docs

        # Fill specialty list
        specs = sorted({(getattr(d, "specialty", None) or "General") for d in docs})
        values = ["All specialties"] + specs
        self.spec_cmb["values"] = values
        cur = self.spec_cmb.get() or "All specialties"
        self.spec_cmb.set(cur if cur in values else "All specialties")

        self.apply_doctor_filters()

    def _resolve_doctor_label(self, doctor_id: int | None) -> str:
        if not doctor_id:
            return "Unassigned"

        label = f"Doctor {doctor_id}"
        try:
            with SessionLocal() as db:
                # 1) Try as Doctor.id first
                d = db.get(Doctor, doctor_id)

                # 2) If not found, treat it as User.id -> Doctor
                if not d:
                    d = db.scalar(select(Doctor).where(Doctor.user_id == doctor_id))

                if d:
                    u = db.get(User, d.user_id) if d.user_id else None
                    # Prefer full_name, then email; fallback to Doctor <id>
                    base = (u.full_name or u.email) if u else f"Doctor {d.id}"
                    spec = getattr(d, "specialty", None) or "General"
                    label = f"Dr. {base} ({spec})"

                    # Refresh caches (for both keys) but don't *read* from them next time
                    try:
                        self.doctor_labels[d.id] = label
                        self.doctor_labels[doctor_id] = label
                    except Exception:
                        pass
        except Exception:
            # keep the simple fallback label
            pass

        return label


    def apply_doctor_filters(self):
        spec = self.spec_cmb.get()
        q = (self.search_in.get() or "").strip().lower()

        # Invalidate any old labels so name edits show up immediately
        self.doctor_labels.clear()

        filtered = []
        for d in self._all_doctors:
            d_spec = (getattr(d, "specialty", None) or "General")

            # Try to get the doctor's display base (full_name -> email) even if d.user is detached
            base = ""
            try:
                # Works if AppointmentService.list_doctors() eager-loaded User
                base = (d.user.full_name or d.user.email or "").strip()
            except Exception:
                base = ""

            if not base:
                # Fallback: fetch the linked user by id (handles detached/lazy relationship)
                try:
                    with SessionLocal() as db:
                        u = db.get(User, getattr(d, "user_id", None))
                        if u:
                            base = (u.full_name or u.email or "").strip()
                except Exception:
                    base = ""

            # Filtering
            name_lc = base.lower()
            if spec and spec != "All specialties" and d_spec != spec:
                continue
            if q and (q not in name_lc and q not in d_spec.lower()):
                continue

            filtered.append((d, base, d_spec))

        self.doctors.clear()
        self.doctor_labels.clear()

        labels = []
        for d, base, d_spec in filtered:
            if not base:
                # Last-resort fallback
                base = f"{getattr(d, 'id', '?')}"
            disp = f"Dr. {base} ({d_spec})"
            labels.append(disp)
            self.doctors[disp] = d.id
            self.doctor_labels[d.id] = disp  # prime cache for resolver

        self.doctor_cmb["values"] = labels
        cur = self.doctor_cmb.get()
        self.doctor_cmb.set(cur if cur in labels else (labels[0] if labels else ""))

        self._set_time_slots([])
        if self.doctor_cmb.get():
            self._recompute_available_dates()
            self._maybe_jump_to_next_available_date()
            self.refresh_slots()


    def _on_doctor_change(self):
        self._set_time_slots([])
        self._recompute_available_dates()
        self._maybe_jump_to_next_available_date()
        self.refresh_slots()

    # ---------- Available date horizon + colouring ----------
    def _recompute_available_dates(self):
        label = self.doctor_cmb.get()
        if not label:
            self._available_dates.clear()
            if HAS_TKCAL and self.cal is not None:
                self.cal.calevent_remove("all")
            return

        doctor_id = self.doctors.get(label)
        if not doctor_id:
            self._available_dates.clear()
            if HAS_TKCAL and self.cal is not None:
                self.cal.calevent_remove("all")
            return

        today_dt = datetime.now()
        end_dt = today_dt + timedelta(days=self._date_window_days)
        try:
            avail_list = AppointmentService.get_available_dates(doctor_id, today_dt, end_dt)
        except Exception as e:
            print("[PatientFrame] get_available_dates error:", e)
            avail_list = []

        self._available_dates = set(avail_list)

        # Colour the calendar: green for available; red otherwise
        if HAS_TKCAL and self.cal is not None:
            self.cal.calevent_remove("all")
            try:
                self.cal.tag_config("avail", background=LIGHT_GREEN)
                self.cal.tag_config("blocked", background=LIGHT_RED)
            except Exception:
                pass

            cur_month_first = date.today().replace(day=1)
            all_days = [cur_month_first + timedelta(days=i) for i in range(370)]
            avail_dates = {datetime.strptime(s, DAY_FMT).date() for s in self._available_dates}

            for d in all_days:
                tag = "avail" if d in avail_dates else "blocked"
                self.cal.calevent_create(d, "", tag)

            try:
                self.cal.update_idletasks()
            except Exception:
                pass

    def _maybe_jump_to_next_available_date(self):
        """If selected day isn't available for the current doctor, jump to the closest available date."""
        if not self._available_dates:
            return

        if HAS_TKCAL and self.cal is not None:
            sel = self.cal.get_date()
            sel_str = sel if isinstance(sel, str) else sel.strftime(DAY_FMT)
        else:
            sel_str = (self.date_in.get() or "").strip()

        if sel_str in self._available_dates:
            return

        today_str = date.today().strftime(DAY_FMT)
        future = sorted(d for d in self._available_dates if d >= today_str)
        pick = future[0] if future else sorted(self._available_dates)[0]
        try:
            if HAS_TKCAL and self.cal is not None:
                self.cal.selection_set(datetime.strptime(pick, DAY_FMT).date())
            else:
                self.date_in.delete(0, "end"); self.date_in.insert(0, pick)
        except Exception:
            pass

    # ---------- Slots ----------
    def _set_time_slots(self, slots: list[str]) -> None:
        self._current_slots = list(slots)
        if slots:
            self.time_cmb.config(state="readonly", values=slots)
            cur = self.time_cmb.get()
            self.time_cmb.set(cur if cur in slots else slots[0])
        else:
            self.time_cmb.config(state="disabled", values=[])
            self.time_cmb.set("")

    def refresh_slots(self):
        label = self.doctor_cmb.get()
        if not label:
            self._set_time_slots([]); return
        doctor_id = self.doctors.get(label)
        if not doctor_id:
            self._set_time_slots([]); return

        date_str = self._get_selected_date_str()
        if self._available_dates and date_str not in self._available_dates:
            self._set_time_slots([]); return

        try:
            day = datetime.strptime(date_str, DAY_FMT)
        except ValueError:
            messagebox.showerror("Invalid date", "Use YYYY-MM-DD")
            self._set_time_slots([]); return

        try:
            slots = AppointmentService.get_available_slots(doctor_id, day)
        except Exception as e:
            print("[PatientFrame] get_available_slots error:", e)
            slots = []

        self._set_time_slots(slots)

    def find_slots(self):
        label = self.doctor_cmb.get()
        if not label:
            messagebox.showwarning("Pick a doctor", "Select a doctor first.")
            return
        doctor_id = self.doctors.get(label)
        if not doctor_id:
            messagebox.showwarning("Pick a doctor", "Select a valid doctor.")
            return

        self._recompute_available_dates()
        self._maybe_jump_to_next_available_date()
        self.refresh_slots()

    # ---------- Appointments tab actions ----------
    def _selected_appt_id(self) -> int | None:
        sel = self.tree_ap.selection()
        if not sel:
            return None
        vals = self.tree_ap.item(sel[0], "values")
        return int(vals[0])

    def cancel_selected(self):
        appt_id = self._selected_appt_id()
        if not appt_id:
            messagebox.showwarning("No selection", "Select an appointment to cancel.")
            return
        if not messagebox.askyesno("Cancel Appointment", "Are you sure you want to cancel this appointment?"):
            return

        with SessionLocal() as db:
            ap = db.get(Appointment, appt_id)
            if not ap or (self.patient and ap.patient_id != self.patient.id):
                messagebox.showerror("Error", "Invalid appointment.")
                return
            doc_id = ap.doctor_id

        try:
            AppointmentService.cancel(appt_id)
        except Exception as e:
            messagebox.showerror("Cancel error", str(e))
            return

        # notify doctor + all receptionists
        self._notify_doc_and_reception(
            doc_id, title="Appointment cancelled", body=f"Patient cancelled appointment #{appt_id}."
        )

        self.refresh_appointments()
        self.refresh_notifications()
        messagebox.showinfo("Cancelled", "Your appointment has been cancelled.")

    def request_reschedule_selected(self):
        appt_id = self._selected_appt_id()
        if not appt_id:
            messagebox.showwarning("No selection", "Select an appointment to reschedule.")
            return

        with SessionLocal() as db:
            ap = db.get(Appointment, appt_id)
            if not ap or (self.patient and ap.patient_id != self.patient.id):
                messagebox.showerror("Error", "Invalid appointment.")
                return
            doc_id = ap.doctor_id

        # Dialog
        top = tk.Toplevel(self)
        top.title("Request Reschedule")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        ttk.Label(top, text=f"Current: {ap.scheduled_for:%Y-%m-%d %H:%M}").grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(top, text="New Date").grid(row=1, column=0, sticky="w", padx=8)
        if HAS_TKCAL:
            de = DateEntry(top, width=16, date_pattern="yyyy-mm-dd")
        else:
            de = ttk.Entry(top, width=16); de.insert(0, date.today().strftime(DAY_FMT))
        de.grid(row=1, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(top, text="New Time (HH:MM)").grid(row=2, column=0, sticky="w", padx=8)
        te = ttk.Entry(top, width=10); te.grid(row=2, column=1, sticky="w", padx=8, pady=2)
        te.bind("<Return>", lambda _e: do_req())

        def do_req():
            d_str = de.get() if not HAS_TKCAL else de.get_date().strftime(DAY_FMT)
            t_str = te.get().strip()
            try:
                new_when = datetime.strptime(f"{d_str} {t_str}", "%Y-%m-%d %H:%M")
            except Exception:
                messagebox.showerror("Invalid", "Use date YYYY-MM-DD and time HH:MM.")
                return
            if new_when <= datetime.now():
                messagebox.showwarning("Future only", "Please choose a future date/time.")
                return

            # If the slot is free, offer instant reschedule
            try:
                free_slots = set(AppointmentService.get_available_slots(doc_id, new_when))
                if t_str in free_slots and messagebox.askyesno(
                    "Slot Available", f"{t_str} is available on {d_str}. Book it now?"
                ):
                    try:
                        AppointmentService.reschedule(appt_id, new_when)
                    except Exception as e:
                        messagebox.showerror("Reschedule error", str(e))
                        return
                    self._notify_doc_and_reception(
                        doc_id, title="Appointment rescheduled",
                        body=f"Patient rescheduled appointment #{appt_id} to {new_when:%Y-%m-%d %H:%M}."
                    )
                    top.destroy()
                    self.refresh_appointments()
                    self.refresh_notifications()
                    messagebox.showinfo("Rescheduled", "Your appointment has been rescheduled.")
                    return
            except Exception:
                pass

            # Otherwise create a formal request (unassigned allowed)
            try:
                if hasattr(AppointmentService, "create_request"):
                    req_appt = AppointmentService.create_request(
                        patient_id=self.patient.id,
                        doctor_id=doc_id,  # service may store 0 if None
                        when=new_when,
                        reason=self.reason_in.get().strip(),
                    )
                    try:
                        notify_receptionists_about_request(req_appt)
                    except Exception:
                        self._notify_doc_and_reception(
                            doc_id,
                            title="Reschedule request",
                            body=f"Patient requests to move appointment #{appt_id} to {new_when:%Y-%m-%d %H:%M}.",
                        )
                else:
                    self._notify_doc_and_reception(
                        doc_id,
                        title="Reschedule request",
                        body=f"Patient requests to move appointment #{appt_id} to {new_when:%Y-%m-%d %H:%M}.",
                    )

            except Exception as e:
                messagebox.showerror("Request error", f"Unexpected error: {e}")
                return


            top.destroy()
            self.refresh_notifications()
            messagebox.showinfo("Requested", "Reschedule request sent. You’ll be notified when it’s accepted.")

        ttk.Button(top, text="Send Request", command=do_req).grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 8))

    def _notify_doc_and_reception(self, doctor_id: int, title: str, body: str):
        """Notify the doctor user + all receptionists."""
        with SessionLocal() as db:
            doc_user_id = db.scalar(select(User.id).join(Doctor, Doctor.user_id == User.id).where(Doctor.id == doctor_id))
            recp_ids = [u.id for u in db.scalars(select(User).where(User.role == Role.receptionist)).all()]
            targets = set([doc_user_id] if doc_user_id else []).union(recp_ids)
            for uid in targets:
                db.add(Notification(user_id=uid, title=title, body=body))
            db.commit()

    def refresh_appointments(self):
        if not hasattr(self, "tree_ap") or not getattr(self, "patient", None):
            return
        for iid in self.tree_ap.get_children():
            self.tree_ap.delete(iid)

        try:
            with SessionLocal() as db:
                appts = db.scalars(
                    select(Appointment)
                    .where(Appointment.patient_id == self.patient.id)
                    .order_by(Appointment.scheduled_for.desc())
                ).all()

            for a in appts:
                when = getattr(a, "scheduled_for", None) or getattr(a, "datetime", None)
                when_str = when.strftime(DATE_FMT) if when else ""

                # Robust label regardless of whether a.doctor_id is Doctor.id or User.id
                doc_label = self._resolve_doctor_label(getattr(a, "doctor_id", None)) if getattr(a, "doctor_id", None) else "Unassigned"

                reason = getattr(a, "reason", "") or ""
                status_obj = getattr(a, "status", "")
                status_val = getattr(status_obj, "value", status_obj) or ""

                self.tree_ap.insert("", "end", values=(a.id, when_str, doc_label, reason, status_val))
        except Exception as e:
            print(f"[UI] Appointments error: {e}")


    # ---------- Treatment History (Records + Prescriptions) ----------
    def refresh_treatment_history(self):
        """Unified table with both Medical Records and Prescriptions (as summaries)."""
        for i in self.tree_hist.get_children():
            self.tree_hist.delete(i)
        if not self.patient:
            return

        merged: List[Tuple[str, Any]] = []  # (kind, obj)

        with SessionLocal() as db:
            # Medical Records
            try:
                q = select(MedicalRecord).where(MedicalRecord.patient_id == self.patient.id)
                order_col = getattr(MedicalRecord, "created_at", getattr(MedicalRecord, "id", None))
                if order_col is not None:
                    q = q.order_by(order_col.desc())
                recs = db.scalars(q).all()
                merged.extend([("record", r) for r in recs])
            except Exception as e:
                print("[PatientFrame] history records error:", e)

            # Prescriptions
            try:
                pq = select(Prescription).where(Prescription.patient_id == self.patient.id)
                pq = pq.order_by(getattr(Prescription, "created_at", Prescription.id).desc())
                rx_list = db.scalars(pq).all()
                merged.extend([("rx", r) for r in rx_list])
            except Exception as e:
                print("[PatientFrame] history rx error:", e)

            # Sort merged by created_at desc if possible
            def _dt(obj):
                t = getattr(obj, "created_at", None)
                return t or datetime.min
            merged.sort(key=lambda kv: _dt(kv[1]), reverse=True)

            # Small cache for names
            user_cache: dict[int, str] = {}

            def _user_name(uid: int | None) -> str:
                if not uid:
                    return "-"
                if uid in user_cache:
                    return user_cache[uid]
                u = db.get(User, uid)
                name = (u.full_name or u.email) if u else f"User#{uid}"
                user_cache[uid] = name
                return name

            # Populate rows
            for kind, obj in merged:
                if kind == "record":
                    r = obj
                    rid = getattr(r, "id", "")
                    dt = ""
                    if hasattr(r, "created_at") and r.created_at:
                        try: dt = r.created_at.strftime(DATE_FMT)
                        except Exception: dt = str(r.created_at)[:16]
                    role_val = getattr(getattr(r, "author_role", None), "value", getattr(r, "author_role", "")) or ""
                    author = _user_name(getattr(r, "author_user_id", None))
                    summary_candidates = [
                        getattr(r, "title", None),
                        getattr(r, "summary", None),
                        getattr(r, "text", None),
                        getattr(r, "content", None),
                        getattr(r, "note", None),
                    ]
                    summary = next((s for s in summary_candidates if isinstance(s, str) and s.strip()), "")
                    summary = (summary.replace("\n", " ").strip() or f"Record #{rid}")[:120]
                    self.tree_hist.insert("", "end", values=(rid, "Record", dt, author, role_val, summary))

                elif kind == "rx":
                    rx = obj
                    rid = getattr(rx, "id", "")
                    dt = ""
                    if hasattr(rx, "created_at") and rx.created_at:
                        try: dt = rx.created_at.strftime(DATE_FMT)
                        except Exception: dt = str(rx.created_at)[:16]
                    doc_label = self._resolve_doctor_label(getattr(rx, "doctor_id", None)) if getattr(rx, "doctor_id", None) else "-"
                    # Show a compact summary
                    summary_candidates = [
                        getattr(rx, "title", None),
                        getattr(rx, "summary", None),
                        getattr(rx, "text", None),
                        getattr(rx, "medication", None),
                        getattr(rx, "instructions", None),
                        getattr(rx, "dosage", None),
                    ]
                    summary = next((s for s in summary_candidates if isinstance(s, str) and s.strip()), "")
                    if not summary:
                        summary = "Prescription issued."
                    summary = summary.replace("\n", " ").strip()[:120]
                    self.tree_hist.insert("", "end", values=(rid, "Rx", dt, doc_label, "doctor", summary))

    def _selected_hist_row(self):
        sel = self.tree_hist.selection()
        if not sel:
            return None
        return self.tree_hist.item(sel[0], "values")  # (id, type, date, author, role, summary)

    def view_selected_record(self):
        row = self._selected_hist_row()
        if not row:
            messagebox.showwarning("No selection", "Select an item to view.")
            return
        rid, kind = row[0], row[1]
        if kind == "Record":
            self._view_medical_record(int(rid))
        else:
            self._view_prescription(int(rid))

    def download_selected_record(self):
        row = self._selected_hist_row()
        if not row:
            messagebox.showwarning("No selection", "Select an item to download.")
            return
        rid, kind = row[0], row[1]
        if kind == "Record":
            self._download_medical_record(int(rid))
        else:
            self.download_prescription_selected_by_id(int(rid))

    # ----- Medical Record helpers (view/download) -----
    def _view_medical_record(self, rid: int):
        with SessionLocal() as db:
            r = db.get(MedicalRecord, rid)
            if not r or getattr(r, "patient_id", None) != getattr(self.patient, "id", None):
                messagebox.showerror("Error", "Invalid record.")
                return
            dt = ""
            if hasattr(r, "created_at") and r.created_at:
                try: dt = r.created_at.strftime(DATE_FMT)
                except Exception: dt = str(r.created_at)[:16]
            role_val = getattr(getattr(r, "author_role", None), "value", getattr(r, "author_role", "")) or ""
            author_user_id = getattr(r, "author_user_id", None)
            author_name = "-"
            if author_user_id:
                u = db.get(User, author_user_id)
                if u:
                    author_name = u.full_name or u.email
            body_candidates = [
                getattr(r, "text", None),
                getattr(r, "content", None),
                getattr(r, "note", None),
                getattr(r, "summary", None),
            ]
            body = next((s for s in body_candidates if isinstance(s, str) and s.strip()), "")

        top = tk.Toplevel(self)
        top.title(f"Record #{rid}")
        top.transient(self.winfo_toplevel()); top.grab_set()
        frm = ttk.Frame(top, padding=10); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=f"Date: {dt}").pack(anchor="w")
        ttk.Label(frm, text=f"Author: {author_name}").pack(anchor="w")
        ttk.Label(frm, text=f"Role: {role_val}").pack(anchor="w")
        txt = scrolledtext.ScrolledText(frm, height=16, wrap="word")
        txt.pack(fill="both", expand=True, pady=(6, 0))
        txt.insert("end", body or "(no text)")
        txt.config(state="disabled")
        ttk.Button(frm, text="Close", command=top.destroy).pack(anchor="e", pady=(6, 0))

    def _download_medical_record(self, rid: int):
        if not self.patient:
            messagebox.showerror("Error", "Not logged in as patient.")
            return
        with SessionLocal() as db:
            r = db.get(MedicalRecord, rid)
            if not r or getattr(r, "patient_id", None) != self.patient.id:
                messagebox.showerror("Error", "Invalid record.")
                return
            created_at = ""
            if hasattr(r, "created_at") and r.created_at:
                try: created_at = r.created_at.strftime(DATE_FMT)
                except Exception: created_at = str(r.created_at)[:16]
            role_val = getattr(getattr(r, "author_role", None), "value", getattr(r, "author_role", "")) or ""
            author_user_id = getattr(r, "author_user_id", None)
            author_name = "-"
            if author_user_id:
                u = db.get(User, author_user_id)
                if u:
                    author_name = u.full_name or u.email
            summary = getattr(r, "title", None) or getattr(r, "summary", None) or ""
            body = getattr(r, "text", None) or getattr(r, "content", None) or getattr(r, "note", None) or ""

        filetypes = [("PDF file", "*.pdf")] if HAS_PDF else [("Text file", "*.txt")]
        defaultext = ".pdf" if HAS_PDF else ".txt"
        fname = filedialog.asksaveasfilename(
            title="Save Medical Record",
            defaultextension=defaultext,
            filetypes=filetypes,
            initialfile=f"record_{rid}{defaultext}",
        )
        if not fname:
            return

        lines = [
            "MEDICAL RECORD",
            "--------------",
            f"ID: {rid}",
            f"Date: {created_at}",
            f"Patient ID: {getattr(self.patient, 'id', '')}",
            f"Author: {author_name}",
            f"Author Role: {role_val}",
            "",
            f"Summary: {summary or ''}",
            "",
            "Body:",
            (body or "").strip(),
            "",
            "—— End ——",
        ]
        self._write_pdf_or_txt(fname, lines, title="Medical Record")

    # ---------- Billing ----------
    def refresh_billing(self):
        for i in self.tree_bill.get_children():
            self.tree_bill.delete(i)
        if not self.patient:
            return

        with SessionLocal() as db:
            j = join(Billing, Appointment, Billing.appointment_id == Appointment.id)
            rows = db.execute(
                select(
                    Billing.id,
                    Billing.description,
                    Billing.amount,
                    Billing.status,
                    Billing.paid_at,
                    Billing.payment_method,
                )
                .select_from(j)
                .where(Appointment.patient_id == self.patient.id)
                .order_by(Billing.created_at.desc())
            ).all()

        for (bid, desc, amt, status, paid_at, method) in rows:
            status_val = getattr(status, "value", str(status)) if status else ""
            method_val = getattr(method, "value", str(method)) if method else ""
            self.tree_bill.insert(
                "",
                "end",
                values=(
                    bid,
                    desc or "",
                    f"{(amt or 0):.2f}",
                    status_val,
                    paid_at.strftime(DATE_FMT) if paid_at else "",
                    method_val,
                ),
            )

    def mark_bill_paid(self):
        sel = self.tree_bill.selection()
        if not sel:
            messagebox.showwarning("No selection", "Choose a bill to mark as paid.")
            return
        item = self.tree_bill.item(sel[0], "values")
        bill_id = int(item[0])

        top = tk.Toplevel(self)
        top.title("Payment")
        top.transient(self.winfo_toplevel())
        top.grab_set()

        ttk.Label(top, text="Payment Method").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        pm = ttk.Combobox(top, state="readonly", values=[m.value for m in PaymentMethod], width=24)
        pm.grid(row=1, column=0, padx=8, sticky="ew"); pm.current(0)

        ttk.Label(top, text="Transaction ID (optional)").grid(row=2, column=0, padx=8, pady=(6, 0), sticky="w")
        tx = ttk.Entry(top, width=30); tx.grid(row=3, column=0, padx=8, pady=(2, 8), sticky="ew")

        def do_pay():
            method_str = pm.get(); txn = tx.get().strip() or None
            with SessionLocal() as db:
                b = db.get(Billing, bill_id)
                if b and b.status != BillingStatus.paid:
                    b.status = BillingStatus.paid
                    try:
                        b.payment_method = PaymentMethod(method_str)
                    except Exception:
                        pass
                    b.paid_at = datetime.now()
                    b.transaction_id = txn
                    db.commit()
            top.destroy()
            self.refresh_billing()
            messagebox.showinfo("Paid", "Bill marked as paid.")

        ttk.Button(top, text="Confirm", command=do_pay).grid(row=4, column=0, padx=8, pady=(0, 8), sticky="ew")

    # ---------- Prescriptions ----------
    def _selected_rx_id(self) -> int | None:
        sel = self.tree_rx.selection()
        if not sel:
            return None
        vals = self.tree_rx.item(sel[0], "values")
        try:
            return int(vals[0])
        except Exception:
            return None

    def refresh_prescriptions(self):
        """Load prescriptions for the current patient."""
        for i in self.tree_rx.get_children():
            self.tree_rx.delete(i)
        if not self.patient:
            return

        with SessionLocal() as db:
            try:
                q = select(Prescription).where(Prescription.patient_id == self.patient.id)
                q = q.order_by(getattr(Prescription, "created_at", Prescription.id).desc())
                rx_list = db.scalars(q).all()
            except Exception as e:
                print("[PatientFrame] refresh_prescriptions error:", e)
                rx_list = []

        for rx in rx_list:
            dt = ""
            if hasattr(rx, "created_at") and rx.created_at:
                try:
                    dt = rx.created_at.strftime(DATE_FMT)
                except Exception:
                    dt = str(rx.created_at)[:16]
            doc_label = self._resolve_doctor_label(getattr(rx, "doctor_id", None)) if getattr(rx, "doctor_id", None) else "-"
            summary_candidates = [
                getattr(rx, "title", None),
                getattr(rx, "summary", None),
                getattr(rx, "notes", None),
                getattr(rx, "text", None),
                getattr(rx, "medication", None),
                getattr(rx, "instructions", None),
            ]
            summary = next((s for s in summary_candidates if isinstance(s, str) and s.strip()), "")
            if not summary:
                summary = f"Prescription #{getattr(rx, 'id', '')}"
            summary = (summary.strip().replace("\n", " "))[:120]
            self.tree_rx.insert("", "end", values=(rx.id, dt, doc_label, summary))

    def _view_prescription(self, rx_id: int):
        with SessionLocal() as db:
            rx = db.get(Prescription, rx_id)
            if not rx or getattr(rx, "patient_id", None) != getattr(self.patient, "id", None):
                messagebox.showerror("Error", "Invalid prescription.")
                return

            created_at = ""
            if hasattr(rx, "created_at") and rx.created_at:
                try:
                    created_at = rx.created_at.strftime(DATE_FMT)
                except Exception:
                    created_at = str(rx.created_at)[:16]
            meds = getattr(rx, "medication", None) or getattr(rx, "title", None) or ""
            dosage = getattr(rx, "dosage", None) or ""
            instructions = getattr(rx, "instructions", None) or getattr(rx, "notes", None) or getattr(rx, "text", None) or ""
            repeats = getattr(rx, "repeats", None)
            doctor_id = getattr(rx, "doctor_id", None)
            doctor_name = self._resolve_doctor_label(doctor_id) if doctor_id else "-"

        top = tk.Toplevel(self)
        top.title(f"Prescription #{rx_id}")
        top.transient(self.winfo_toplevel()); top.grab_set()
        frm = ttk.Frame(top, padding=10); frm.pack(fill="both", expand=True)
        for line in (f"Date: {created_at}", f"Doctor: {doctor_name}", f"Medication: {meds}", f"Dosage: {dosage}", f"Repeats: {repeats or ''}"):
            ttk.Label(frm, text=line).pack(anchor="w")
        stxt = scrolledtext.ScrolledText(frm, height=12, wrap="word")
        stxt.pack(fill="both", expand=True, pady=(6, 0))
        stxt.insert("end", f"Instructions:\n{instructions or '(none)'}")
        stxt.config(state="disabled")
        ttk.Button(frm, text="Close", command=top.destroy).pack(anchor="e", pady=(6, 0))

    def download_prescription_selected(self):
        rx_id = self._selected_rx_id()
        if not rx_id:
            messagebox.showwarning("No selection", "Select a prescription to download.")
            return
        self.download_prescription_selected_by_id(rx_id)

    def download_prescription_selected_by_id(self, rx_id: int):
        if not self.patient:
            messagebox.showerror("Error", "Not logged in as patient.")
            return

        with SessionLocal() as db:
            rx = db.get(Prescription, rx_id)
            if not rx or getattr(rx, "patient_id", None) != self.patient.id:
                messagebox.showerror("Error", "Invalid prescription.")
                return

            created_at = ""
            if hasattr(rx, "created_at") and rx.created_at:
                try:
                    created_at = rx.created_at.strftime(DATE_FMT)
                except Exception:
                    created_at = str(rx.created_at)[:16]
            meds = getattr(rx, "medication", None) or getattr(rx, "title", None) or ""
            dosage = getattr(rx, "dosage", None) or ""
            instructions = getattr(rx, "instructions", None) or getattr(rx, "notes", None) or getattr(rx, "text", None) or ""
            repeats = getattr(rx, "repeats", None)
            doctor_id = getattr(rx, "doctor_id", None)
            doctor_name = self._resolve_doctor_label(doctor_id) if doctor_id else "-"
            patient_name = ""
            try:
                patient_name = self.controller.current_user.full_name or self.controller.current_user.email
            except Exception:
                pass

        filetypes = [("PDF file", "*.pdf")] if HAS_PDF else [("Text file", "*.txt")]
        defaultext = ".pdf" if HAS_PDF else ".txt"
        fname = filedialog.asksaveasfilename(
            title="Save Prescription",
            defaultextension=defaultext,
            filetypes=filetypes,
            initialfile=f"prescription_{rx_id}{defaultext}",
        )
        if not fname:
            return

        lines = [
            "PRESCRIPTION",
            "------------",
            f"ID: {rx_id}",
            f"Date: {created_at}",
            f"Patient: {patient_name}",
            f"Doctor: {doctor_name}",
            "",
            f"Medication: {meds}",
            f"Dosage: {dosage}",
            f"Repeats: {repeats if repeats is not None else ''}",
            "",
            "Instructions:",
            instructions.strip() if isinstance(instructions, str) else "",
            "",
            "—— End ——",
        ]
        self._write_pdf_or_txt(fname, lines, title="Prescription")

    # ---------- Notifications ----------
    def refresh_notifications(self):
        for i in self.tree_nf.get_children():
            self.tree_nf.delete(i)
        u = getattr(self.controller, "current_user", None)
        if not u:
            return
        with SessionLocal() as db:
            rows = db.scalars(
                select(Notification)
                .where(Notification.user_id == u.id)
                .order_by(Notification.created_at.desc())
            ).all()
        unread = 0
        for n in rows:
            self.tree_nf.insert(
                "", "end",
                values=(n.id, n.created_at.strftime(DATE_FMT), n.title, "yes" if n.read else "")
            )
            if not n.read:
                unread += 1

        if getattr(self, "btn_notif", None):
            self.btn_notif.config(text=f"Notifications ({unread})" if unread else "Notifications")

    def _notif_mark_read(self):
        sel = self.tree_nf.selection()
        if not sel:
            return
        notif_id = int(self.tree_nf.item(sel[0], "values")[0])
        with SessionLocal() as db:
            n = db.get(Notification, notif_id)
            if n:
                n.read = True; db.commit()
        self.refresh_notifications()

    # ---------------- Medical Records (add note) ----------------
    def add_med_record(self):
        if not self.patient or not self.controller.current_user:
            return
        text = self.rec_txt.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Missing", "Enter a record/note.")
            return

        user = self.controller.current_user
        try:
            role_val = getattr(user, "role", "patient")
            role_val = getattr(role_val, "value", role_val)
            author_role = RecordAuthor.doctor if role_val == "doctor" else RecordAuthor.patient
        except Exception:
            author_role = RecordAuthor.patient

        with SessionLocal() as db:
            rec = MedicalRecord(
                patient_id=self.patient.id,
                author_user_id=user.id,
                author_role=author_role,
                text=text,
            )
            db.add(rec); db.commit()

        self.rec_txt.delete("1.0", "end")
        # Immediate refresh so the new note appears in Treatment History
        self.refresh_treatment_history()
        messagebox.showinfo("Saved", "Medical record added.")

    # ---------------- Booking Actions ----------------
    def book(self):
        if not self.controller.current_user or not self.patient:
            messagebox.showerror("Error", "Not logged in as patient.")
            return

        label = self.doctor_cmb.get()
        if not label:
            messagebox.showwarning("Missing", "Select a doctor.")
            return
        doctor_id = self.doctors.get(label)
        if not doctor_id:
            messagebox.showwarning("Missing", "Select a valid doctor.")
            return

        date_str = self._get_selected_date_str()
        time_str = self.time_cmb.get().strip()
        if not time_str:
            messagebox.showwarning("Missing", "Choose a time slot (click 'Find Slots').")
            return
        if time_str not in set(self._current_slots):
            messagebox.showerror("Invalid selection", "Please pick a time from the available list.")
            self.refresh_slots()
            return

        try:
            day_dt = datetime.strptime(date_str, DAY_FMT)
            current_slots = set(AppointmentService.get_available_slots(doctor_id, day_dt))
            if time_str not in current_slots:
                messagebox.showerror(
                    "No longer available",
                    "That time was just taken. Please choose another available slot.",
                )
                self._set_time_slots(sorted(current_slots))
                return
        except Exception:
            pass

        try:
            when = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Invalid time", "Use HH:MM")
            return

        try:
            ap = AppointmentService.book_at_slot(
                self.patient.id, doctor_id, when, time_str, self.reason_in.get().strip()
            )
        except ValueError as e:
            messagebox.showerror("Booking error", str(e)); return
        except Exception as e:
            messagebox.showerror("Booking error", f"Unexpected error: {e}"); return

        messagebox.showinfo("Booked", f"Appointment #{ap.id} at {ap.scheduled_for.strftime(DATE_FMT)}")
        self.refresh_appointments()
        self._recompute_available_dates()
        self.refresh_slots()

    def request_booking(self):
        if not self.controller.current_user or not self.patient:
            messagebox.showerror("Error", "Not logged in as patient.")
            return

        label = self.doctor_cmb.get()
        if not label:
            messagebox.showwarning("Missing", "Select a doctor.")
            return
        doctor_id = self.doctors.get(label)
        if not doctor_id:
            messagebox.showwarning("Missing", "Select a valid doctor.")
            return

        req_date_str = (
            self.req_date_in.get_date().strftime(DAY_FMT)
            if HAS_TKCAL and hasattr(self.req_date_in, "get_date")
            else self.req_date_in.get().strip()
        )
        time_str = (self.req_time_in.get() or "").strip()
        if not time_str:
            messagebox.showwarning("Missing", "Enter a desired time (HH:MM).")
            return

        try:
            when = datetime.strptime(f"{req_date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Invalid time", "Use format HH:MM, e.g. 14:30")
            return

        if when <= datetime.now():
            messagebox.showwarning("Choose future time", "Please select a time in the future.")
            return

        # If free, offer instant booking
        try:
            free_slots = set(AppointmentService.get_available_slots(doctor_id, when))
            if time_str in free_slots and messagebox.askyesno(
                "Slot Available", f"{time_str} is available on {req_date_str}. Book it now?"
            ):
                try:
                    ap = AppointmentService.book(self.patient.id, doctor_id, when, self.reason_in.get().strip())
                except Exception as e:
                    messagebox.showerror("Booking error", f"Unexpected error: {e}")
                    return
                messagebox.showinfo("Booked", f"Appointment #{ap.id} at {when:%Y-%m-%d %H:%M}")
                self.refresh_appointments()
                self._recompute_available_dates()
                self.refresh_slots()
                return
        except Exception:
            pass
        # Otherwise submit a formal request
        try:
            if hasattr(AppointmentService, "create_request"):
                req_appt = AppointmentService.create_request(
                    patient_id=self.patient.id,
                    doctor_id=doctor_id,
                    when=when,
                    reason=self.reason_in.get().strip(),
                )
                try:
                    # notify all receptionists about this request
                    notify_receptionists_about_request(req_appt)
                except Exception as _e:
                    # soft-fail: fall back to local notifier so UI isn't blocked
                    self._notify_doc_and_reception(
                        doctor_id,
                        title="New booking request",
                        body=f"Patient requests {when:%Y-%m-%d %H:%M} with you.",
                    )
            else:
                self._notify_doc_and_reception(
                    doctor_id,
                    title="New booking request",
                    body=f"Patient requests {when:%Y-%m-%d %H:%M} with you.",
                )

        except ValueError as e:
            messagebox.showerror("Request error", str(e)); return
        except Exception as e:
            messagebox.showerror("Request error", f"Unexpected error: {e}"); return

        messagebox.showinfo(
            "Requested",
            "Requested successfully.\n\nDoctor and reception have been notified. "
            "You’ll be notified here when it’s accepted.",
        )
        self.req_time_in.delete(0, "end")
        self.refresh_notifications()
        self.refresh_appointments()

    # ---------- Disciplinary ----------
    def _selected_disc_id(self) -> int | None:
        sel = self.tree_disc.selection()
        if not sel:
            return None
        vals = self.tree_disc.item(sel[0], "values")
        try:
            return int(vals[0])
        except Exception:
            return None

    def refresh_disciplinary(self):
        if not HAS_DISCIPLINARY:
            return
        for i in self.tree_disc.get_children():
            self.tree_disc.delete(i)
        if not self.patient:
            return
        with SessionLocal() as db:
            q = select(DisciplinaryRecord).where(DisciplinaryRecord.patient_id == self.patient.id)
            order_col = getattr(DisciplinaryRecord, "created_at", getattr(DisciplinaryRecord, "id", None))
            if order_col is not None:
                q = q.order_by(order_col.desc())
            rows = db.scalars(q).all()
        for d in rows:
            dt = ""
            if hasattr(d, "created_at") and d.created_at:
                try: dt = d.created_at.strftime(DATE_FMT)
                except Exception: dt = str(d.created_at)[:16]
            sev = getattr(getattr(d, "severity", None), "value", getattr(d, "severity", "")) or ""
            st  = getattr(getattr(d, "status", None), "value", getattr(d, "status", "")) or ""
            title = getattr(d, "title", "") or f"Disciplinary #{getattr(d,'id','')}"
            self.tree_disc.insert("", "end", values=(getattr(d,"id",""), dt, sev, st, title))

    def view_selected_disciplinary(self):
        if not HAS_DISCIPLINARY:
            messagebox.showinfo("Unavailable", "Disciplinary module is not enabled.")
            return
        did = self._selected_disc_id()
        if not did:
            messagebox.showwarning("No selection", "Select a disciplinary entry to view.")
            return
        with SessionLocal() as db:
            d = db.get(DisciplinaryRecord, did)
            if not d or getattr(d,"patient_id",None) != getattr(self.patient,"id",None):
                messagebox.showerror("Error", "Invalid entry.")
                return
            dt = ""
            if hasattr(d, "created_at") and d.created_at:
                try: dt = d.created_at.strftime(DATE_FMT)
                except Exception: dt = str(d.created_at)[:16]
            sev = getattr(getattr(d, "severity", None), "value", getattr(d, "severity", "")) or ""
            st  = getattr(getattr(d, "status", None), "value", getattr(d, "status", "")) or ""
            title = getattr(d, "title", "") or f"Disciplinary #{getattr(d,'id','')}"
            desc = getattr(d, "description", "") or ""
        top = tk.Toplevel(self)
        top.title(title); top.transient(self.winfo_toplevel()); top.grab_set()
        frm = ttk.Frame(top, padding=10); frm.pack(fill="both", expand=True)
        for line in (f"Date: {dt}", f"Severity: {sev}", f"Status: {st}"):
            ttk.Label(frm, text=line).pack(anchor="w")
        stxt = scrolledtext.ScrolledText(frm, height=12, wrap="word"); stxt.pack(fill="both", expand=True, pady=(6,0))
        stxt.insert("end", desc or "(no description)"); stxt.config(state="disabled")
        ttk.Button(frm, text="Close", command=top.destroy).pack(anchor="e", pady=(6,0))

    def ack_selected_disciplinary(self):
        if not HAS_DISCIPLINARY:
            messagebox.showinfo("Unavailable", "Disciplinary module is not enabled.")
            return
        did = self._selected_disc_id()
        if not did:
            messagebox.showwarning("No selection", "Select an entry to acknowledge.")
            return
        with SessionLocal() as db:
            d = db.get(DisciplinaryRecord, did)
            if not d or getattr(d,"patient_id",None) != getattr(self.patient,"id",None):
                messagebox.showerror("Error", "Invalid entry.")
                return
            try:
                if d.status != DisciplinaryStatus.acknowledged:
                    d.status = DisciplinaryStatus.acknowledged
                    d.acknowledged_at = datetime.now()
                    db.commit()
            except Exception as e:
                messagebox.showerror("Error", f"Unable to update: {e}")
                return
        self.refresh_disciplinary()
        messagebox.showinfo("Acknowledged", "Entry has been acknowledged.")

    # ---------- Staff-only editor ----------
    def new_disciplinary(self):
        if not HAS_DISCIPLINARY or not self._can_manage_disciplinary():
            messagebox.showinfo("Unavailable", "You don't have permission to create disciplinary entries.")
            return
        self._disc_open_editor(record_id=None)

    def edit_selected_disciplinary(self):
        if not HAS_DISCIPLINARY or not self._can_manage_disciplinary():
            messagebox.showinfo("Unavailable", "You don't have permission to edit disciplinary entries.")
            return
        did = self._selected_disc_id()
        if not did:
            messagebox.showwarning("No selection", "Select an entry to edit.")
            return
        self._disc_open_editor(record_id=did)

    def _disc_open_editor(self, record_id: int | None):
        if not self.patient:
            messagebox.showerror("Error", "No patient context.")
            return

        rec = None
        if record_id:
            with SessionLocal() as db:
                rec = db.get(DisciplinaryRecord, record_id)
                if not rec or getattr(rec, "patient_id", None) != getattr(self.patient, "id", None):
                    messagebox.showerror("Error", "Invalid disciplinary record.")
                    return

        top = tk.Toplevel(self)
        top.title("Disciplinary Editor")
        top.transient(self.winfo_toplevel()); top.grab_set()

        frm = ttk.Frame(top, padding=10)
        frm.pack(fill="both", expand=True)

        r = 0
        ttk.Label(frm, text="Title").grid(row=r, column=0, sticky="w"); r += 1
        title_in = ttk.Entry(frm, width=60); title_in.grid(row=r-1, column=1, sticky="ew")

        ttk.Label(frm, text="Severity").grid(row=r, column=0, sticky="w"); r += 1
        sev_in = ttk.Combobox(frm, state="readonly", width=20,
                              values=[s.value for s in DisciplinarySeverity])
        sev_in.grid(row=r-1, column=1, sticky="w")

        ttk.Label(frm, text="Status").grid(row=r, column=0, sticky="w"); r += 1
        st_in = ttk.Combobox(frm, state="readonly", width=20,
                             values=[s.value for s in DisciplinaryStatus])
        st_in.grid(row=r-1, column=1, sticky="w")

        ttk.Label(frm, text="Description").grid(row=r, column=0, sticky="nw"); r += 1
        desc_in = tk.Text(frm, height=8, width=60); desc_in.grid(row=r-1, column=1, sticky="ew")

        frm.columnconfigure(1, weight=1)

        if rec:
            title_in.insert(0, getattr(rec, "title", "") or "")
            sev_in.set(getattr(getattr(rec, "severity", None), "value", "low"))
            st_in.set(getattr(getattr(rec, "status", None), "value", "open"))
            desc_in.insert("1.0", getattr(rec, "description", "") or "")
        else:
            sev_in.set("low")
            st_in.set("open")

        btns = ttk.Frame(top); btns.pack(fill="x", padx=10, pady=(8, 10))
        ttk.Button(btns, text="Cancel", command=top.destroy).pack(side="right", padx=(6, 0))

        def save():
            title = title_in.get().strip()
            severity = sev_in.get().strip()
            status = st_in.get().strip()
            desc = desc_in.get("1.0", "end").strip()

            if not title:
                messagebox.showwarning("Missing", "Title is required.")
                return
            if not severity or not status:
                messagebox.showwarning("Missing", "Pick severity and status.")
                return

            try:
                sev_enum = DisciplinarySeverity(severity)
                st_enum = DisciplinaryStatus(status)
            except Exception:
                messagebox.showerror("Invalid", "Invalid severity or status.")
                return

            with SessionLocal() as db:
                if rec:
                    db_rec = db.get(DisciplinaryRecord, rec.id)
                    if not db_rec:
                        messagebox.showerror("Error", "Record not found.")
                        return
                    db_rec.title = title
                    db_rec.description = desc
                    db_rec.severity = sev_enum
                    db_rec.status = st_enum
                else:
                    db_rec = DisciplinaryRecord(
                        patient_id=self.patient.id,
                        title=title,
                        description=desc,
                        severity=sev_enum,
                        status=st_enum,
                    )
                    db.add(db_rec)
                db.commit()

            top.destroy()
            self.refresh_disciplinary()
            messagebox.showinfo("Saved", "Disciplinary record saved.")

        ttk.Button(btns, text="Save", command=save).pack(side="right")

    # ---------- PDF/TXT writer helper ----------
    def _write_pdf_or_txt(self, fname: str, lines: list[str], title: str):
        if HAS_PDF and fname.lower().endswith(".pdf"):
            try:
                c = canvas.Canvas(fname, pagesize=A4)
                width, height = A4
                x_left = 40
                y = height - 50
                c.setFont("Helvetica-Bold", 16)
                c.drawString(x_left, y, title); y -= 24
                c.setFont("Helvetica", 10)
                for line in lines:
                    l = line
                    while len(l) > 110:
                        chunk, l = l[:110], l[110:]
                        if y < 60:
                            c.showPage(); y = height - 50; c.setFont("Helvetica", 10)
                        c.drawString(x_left, y, chunk); y -= 14
                    if y < 60:
                        c.showPage(); y = height - 50; c.setFont("Helvetica", 10)
                    c.drawString(x_left, y, l); y -= 14
                c.showPage(); c.save()
                messagebox.showinfo("Saved", f"Saved as PDF:\n{fname}")
                return
            except Exception as e:
                print("[PatientFrame] PDF write failed, falling back to TXT:", e)

        # TXT fallback or explicit .txt
        try:
            with open(fname, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            messagebox.showinfo("Saved", f"Saved:\n{fname}")
        except Exception as e:
            messagebox.showerror("Save error", f"Could not save file:\n{e}")


# ---------------- Module exports ----------------
__all__ = ["PatientFrame"]

