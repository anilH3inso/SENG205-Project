# care_portal/ui/doctor.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta

# Optional date picker
try:
    from tkcalendar import DateEntry  # pip install tkcalendar
    HAS_TKCAL = True
except Exception:
    HAS_TKCAL = False

from sqlalchemy import select, func, delete
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    User,
    Role,
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
    Billing,
    BillingStatus,
    PaymentMethod,
    RecordAuthor,
)
from ..services.appointments import AppointmentService
from .base import BaseFrame

DATE_FMT = "%Y-%m-%d %H:%M"
DAY_FMT = "%Y-%m-%d"
TIME_FMT = "%H:%M"


def _parse_hhmm(s: str) -> tuple[int, int] | None:
    """Return (hour, minute) if s is HH:MM, else None."""
    try:
        t = datetime.strptime(s.strip(), TIME_FMT)
        return t.hour, t.minute
    except Exception:
        return None


class DoctorFrame(BaseFrame):
    title = "Doctor Dashboard"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # resolve doctor row for logged-in user
        self.doctor: Doctor | None = None
        self._load_doctor()

        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True)

        self.tab_sched = ttk.Frame(self.nb)
        self.tab_avail = ttk.Frame(self.nb)
        self.tab_support = ttk.Frame(self.nb)
        self.tab_kpi = ttk.Frame(self.nb)

        self.nb.add(self.tab_sched, text="Appointments")
        self.nb.add(self.tab_avail, text="Availability")
        self.nb.add(self.tab_support, text="Support")
        self.nb.add(self.tab_kpi, text="Today")

        # build tabs
        self._build_schedule_tab()
        self._build_availability_tab()
        self._build_support_tab()
        self._build_kpi_tab()

        # initial loads
        self._refresh_schedule()
        self._refresh_support()
        self._refresh_kpis()

    # ====================================================
    # Setup helpers
    # ====================================================
    

    # inside care_portal/ui/doctor.py  (methods of DoctorFrame)

    def on_show(self):
        # Re-evaluate the logged-in user and ensure a Doctor profile exists.
        self._load_doctor()
        # If still no doctor profile, bail early (prevents other methods from breaking)
        if not self.doctor:
            return
        # Refresh UI with the now-known doctor context
        try:
            self._refresh_schedule()
            self._refresh_availability()
            self._refresh_support()
            self._refresh_kpis()
        except Exception as e:
            print("DoctorFrame on_show refresh error:", e)

    def _load_doctor(self):
        user = self.controller.current_user
        if not user:
            self.doctor = None
            return

        role_val = getattr(user.role, "value", user.role)  # works for Enum or str

        from sqlalchemy import select
        from ..db import SessionLocal
        from ..models import Doctor

        with SessionLocal() as db:
            doc = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
            # Auto-provision if the logged-in user is a doctor but profile missing
            if not doc and role_val == "doctor":
                from ..models import Doctor as DoctorModel
                doc = DoctorModel(user_id=user.id, specialty="General")
                db.add(doc)
                db.commit()
                db.refresh(doc)
            self.doctor = doc

        if not self.doctor and role_val == "doctor":
            # Something odd (e.g., DB write failed). Log but avoid crashing.
            print("Warning: user has role=doctor but Doctor profile could not be created.")


    # ---------------- Appointments tab ------------------
    def _build_schedule_tab(self):
        wrap = ttk.Frame(self.tab_sched)
        wrap.pack(fill="both", expand=True)

        # Left: filters
        left = ttk.LabelFrame(wrap, text="Filters", padding=8)
        left.pack(side="left", fill="y", padx=(0, 8))

        ttk.Label(left, text="Date").grid(row=0, column=0, sticky="w")
        if HAS_TKCAL:
            self.f_date = DateEntry(left, width=18, date_pattern="yyyy-mm-dd")
        else:
            self.f_date = ttk.Entry(left, width=20)
            self.f_date.insert(0, datetime.now().strftime(DAY_FMT))
        self.f_date.grid(row=1, column=0, sticky="ew", pady=(2, 6))

        ttk.Label(left, text="Status").grid(row=2, column=0, sticky="w")
        self.f_status = ttk.Combobox(
            left, state="readonly",
            values=["(any)", "booked", "completed", "cancelled"]
        )
        self.f_status.current(0)
        self.f_status.grid(row=3, column=0, sticky="ew", pady=(2, 6))

        ttk.Label(left, text="Search (patient/reason)").grid(row=4, column=0, sticky="w")
        self.f_search = ttk.Entry(left, width=20)
        self.f_search.grid(row=5, column=0, sticky="ew", pady=(2, 6))

        ttk.Button(left, text="Refresh", command=self._refresh_schedule).grid(
            row=6, column=0, sticky="ew", pady=(8, 2)
        )

        ttk.Separator(wrap, orient="vertical").pack(side="left", fill="y")

        # Middle: schedule
        middle = ttk.Frame(wrap)
        middle.pack(side="left", fill="both", expand=True, padx=(8, 8))

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
        self.tree_ap.pack(fill="both", expand=True)

        btns = ttk.Frame(middle)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Open Patient", command=self._open_patient).pack(side="left")
        ttk.Button(btns, text="Add Note", command=self._add_note_for_selected).pack(side="left", padx=6)
        ttk.Button(btns, text="Write Prescription", command=self._write_rx_for_selected).pack(side="left", padx=6)
        ttk.Button(btns, text="Mark Completed", command=self._mark_completed).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=self._cancel_appt).pack(side="left", padx=6)
        ttk.Button(btns, text="Reschedule", command=self._resched_appt).pack(side="left", padx=6)
        ttk.Button(btns, text="Check-in", command=self._checkin_appt).pack(side="left", padx=6)
        ttk.Button(btns, text="Undo Check-in", command=self._undo_checkin).pack(side="left", padx=6)

        # Right: snapshot & quick actions
        right = ttk.LabelFrame(wrap, text="Patient Snapshot & Quick Actions", padding=8)
        right.pack(side="left", fill="y")

        self.snap_lbl = ttk.Label(right, text="No patient selected")
        self.snap_lbl.pack(anchor="w")

        ttk.Label(right, text="Allergies").pack(anchor="w")
        self.snap_allerg = tk.Text(right, height=3, width=42, state="disabled")
        self.snap_allerg.pack(fill="x", pady=(0, 6))

        ttk.Label(right, text="Chronic Conditions").pack(anchor="w")
        self.snap_cond = tk.Text(right, height=3, width=42, state="disabled")
        self.snap_cond.pack(fill="x", pady=(0, 6))

        ttk.Label(right, text="Last Prescriptions / Notes").pack(anchor="w")
        self.snap_recent = tk.Listbox(right, height=6, width=42)
        self.snap_recent.pack(fill="both", expand=True)

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=(6, 6))

        qa = ttk.LabelFrame(right, text="Quick Clinical Actions", padding=8)
        qa.pack(fill="x")

        ttk.Label(qa, text="Visit Note").grid(row=0, column=0, sticky="w")
        self.note_txt = tk.Text(qa, height=4, width=40)
        self.note_txt.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))

        ttk.Label(qa, text="Prescription").grid(row=2, column=0, sticky="w")
        self.rx_med = ttk.Entry(qa, width=22); self.rx_med.grid(row=3, column=0, sticky="w", pady=2)
        self.rx_dose = ttk.Entry(qa, width=8);  self.rx_dose.grid(row=3, column=1, sticky="w", pady=2, padx=(6, 0))
        self.rx_freq = ttk.Entry(qa, width=22); self.rx_freq.grid(row=4, column=0, sticky="w", pady=2)
        self.rx_dur = ttk.Entry(qa, width=8);  self.rx_dur.grid(row=4, column=1, sticky="w", pady=2, padx=(6, 0))
        self.rx_note = ttk.Entry(qa, width=40); self.rx_note.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 6))

        rx_btns = ttk.Frame(qa)
        rx_btns.grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Button(rx_btns, text="Save Note", command=self._save_note).pack(side="left")
        ttk.Button(rx_btns, text="Create Rx", command=self._save_rx).pack(side="left", padx=6)

        # track selection → snapshot
        self._current_patient_id: int | None = None
        self._current_appt_id: int | None = None
        self.tree_ap.bind("<<TreeviewSelect>>", lambda e: self._load_snapshot_from_selection())

    # ---------------- Availability tab ------------------
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
        if HAS_TKCAL:
            self.av_date = DateEntry(form, width=18, date_pattern="yyyy-mm-dd")
        else:
            self.av_date = ttk.Entry(form, width=20)
            self.av_date.insert(0, datetime.now().strftime(DAY_FMT))
        self.av_date.grid(row=1, column=0, sticky="w", padx=(0, 8))

        ttk.Label(form, text="Start (HH:MM)").grid(row=0, column=1, sticky="w")
        self.av_start = ttk.Entry(form, width=10); self.av_start.insert(0, "09:00")
        self.av_start.grid(row=1, column=1, sticky="w", padx=(0, 8))

        ttk.Label(form, text="End (HH:MM)").grid(row=0, column=2, sticky="w")
        self.av_end = ttk.Entry(form, width=10); self.av_end.insert(0, "17:00")
        self.av_end.grid(row=1, column=2, sticky="w", padx=(0, 8))

        ttk.Label(form, text="Slot (min)").grid(row=0, column=3, sticky="w")
        self.av_slot = ttk.Entry(form, width=10); self.av_slot.insert(0, "30")
        self.av_slot.grid(row=1, column=3, sticky="w", padx=(0, 8))

        btns = ttk.Frame(form); btns.grid(row=1, column=4, sticky="e")
        ttk.Button(btns, text="Add / Update", command=self._save_availability).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Delete Selected", command=self._del_availability).pack(side="left")
        ttk.Button(btns, text="Refresh", command=self._refresh_availability).pack(side="left", padx=(6, 0))

        # When you click a row, load it into the form
        self.tree_av.bind("<<TreeviewSelect>>", lambda e: self._on_select_availability())

        self._refresh_availability()

    # ---------------- Support tab -----------------------
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

    # ---------------- KPI tab ---------------------------
    def _build_kpi_tab(self):
        wrap = ttk.Frame(self.tab_kpi, padding=12)
        wrap.pack(fill="both", expand=True)

        self.kpi_summary = tk.Text(wrap, height=12, width=90, state="disabled")
        self.kpi_summary.pack(fill="both", expand=True)
        ttk.Button(wrap, text="Refresh KPIs", command=self._refresh_kpis).pack(pady=(6, 0))

    # ====================================================
    # Data ops — Schedule
    # ====================================================
    def _selected_appt_id(self) -> int | None:
        sel = self.tree_ap.selection()
        if not sel:
            return None
        vals = self.tree_ap.item(sel[0], "values")
        return int(vals[0])

    def _refresh_schedule(self):
        for i in self.tree_ap.get_children():
            self.tree_ap.delete(i)
        if not self.doctor:
            return

        date_str = self.f_date.get() if not HAS_TKCAL else self.f_date.get_date().strftime(DAY_FMT)
        try:
            day0 = datetime.strptime(date_str, DAY_FMT).replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            messagebox.showerror("Invalid date", "Use YYYY-MM-DD")
            return
        day1 = day0 + timedelta(days=1)

        status = self.f_status.get()
        q = self.f_search.get().strip().lower()

        with SessionLocal() as db:
            stmt = (
                select(Appointment)
                .options(selectinload(Appointment.patient).selectinload(Patient.user))
                .where(
                    Appointment.doctor_id == self.doctor.id,
                    Appointment.scheduled_for >= day0,
                    Appointment.scheduled_for < day1,
                )
                .order_by(Appointment.scheduled_for.asc())
            )
            if status and status != "(any)":
                stmt = stmt.where(Appointment.status == AppointmentStatus(status))

            appts = db.scalars(stmt).all()

            # prefetch attendance (any check-in record)
            ap_ids = [a.id for a in appts]
            att_map = {}
            if ap_ids:
                rows = db.execute(
                    select(Attendance.appointment_id, func.count(Attendance.id))
                    .where(Attendance.appointment_id.in_(ap_ids))
                    .group_by(Attendance.appointment_id)
                ).all()
                att_map = {aid: cnt for (aid, cnt) in rows}

            for a in appts:
                patient_label = a.patient.user.full_name or a.patient.user.email
                if q and (q not in (patient_label or "").lower()) and (q not in (a.reason or "").lower()):
                    continue
                self.tree_ap.insert(
                    "", "end",
                    values=(
                        a.id,
                        a.scheduled_for.strftime(DATE_FMT),
                        patient_label,
                        a.reason or "",
                        a.status.value,
                        "yes" if att_map.get(a.id) else "",
                    )
                )

    def _load_snapshot_from_selection(self):
        appt_id = self._selected_appt_id()
        self._current_appt_id = appt_id
        if not appt_id:
            self._current_patient_id = None
            self.snap_lbl.config(text="No patient selected")
            for txt in (self.snap_allerg, self.snap_cond):
                txt.config(state="normal"); txt.delete("1.0", "end"); txt.config(state="disabled")
            self.snap_recent.delete(0, "end")
            return

        with SessionLocal() as db:
            a = db.get(Appointment, appt_id)
            if not a:
                return
            p = db.get(Patient, a.patient_id)
            u = db.get(User, p.user_id) if p else None

            self._current_patient_id = p.id if p else None

            label = f"{u.full_name or u.email} — MRN:{p.mrn or '-'}  DOB:{p.dob or '-'}  Phone:{u.phone or '-'}"
            self.snap_lbl.config(text=label)

            self.snap_allerg.config(state="normal")
            self.snap_allerg.delete("1.0", "end")
            self.snap_allerg.insert("end", p.allergies or "")
            self.snap_allerg.config(state="disabled")

            self.snap_cond.config(state="normal")
            self.snap_cond.delete("1.0", "end")
            self.snap_cond.insert("end", p.chronic_conditions or "")
            self.snap_cond.config(state="disabled")

            # recent prescriptions & notes
            self.snap_recent.delete(0, "end")
            rx_rows = db.scalars(
                select(Prescription)
                .where(Prescription.appointment_id == a.id)
                .order_by(Prescription.created_at.desc())
            ).all()
            for r in rx_rows[:5]:
                self.snap_recent.insert("end", f"Rx {r.created_at:%Y-%m-%d}: {r.text[:60]}")

            note_rows = db.scalars(
                select(MedicalRecord)
                .where(MedicalRecord.patient_id == p.id)
                .order_by(MedicalRecord.created_at.desc())
            ).all()
            for n in note_rows[:5]:
                self.snap_recent.insert("end", f"Note {n.created_at:%Y-%m-%d}: {n.text[:60]}")

    # Quick actions
    def _open_patient(self):
        self._load_snapshot_from_selection()

    def _add_note_for_selected(self):
        if not self._current_patient_id:
            messagebox.showwarning("No selection", "Select an appointment first.")
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

    def _write_rx_for_selected(self):
        appt_id = self._current_appt_id
        if not appt_id:
            messagebox.showwarning("No selection", "Select an appointment first.")
            return
        med = self.rx_med.get().strip()
        dose = self.rx_dose.get().strip()
        freq = self.rx_freq.get().strip()
        dur = self.rx_dur.get().strip()
        note = self.rx_note.get().strip()
        if not med:
            messagebox.showwarning("Missing", "Enter Medication.")
            return
        text = f"Medication: {med}; Dose: {dose}; Frequency: {freq}; Duration: {dur}; Notes: {note}"
        with SessionLocal() as db:
            rx = Prescription(appointment_id=appt_id, text=text)
            db.add(rx); db.commit()
        for e in (self.rx_med, self.rx_dose, self.rx_freq, self.rx_dur, self.rx_note):
            e.delete(0, "end")
        messagebox.showinfo("Saved", "Prescription created.")
        self._load_snapshot_from_selection()

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
        if HAS_TKCAL:
            de = DateEntry(top, width=16, date_pattern="yyyy-mm-dd")
            de.set_date(current_dt.date())
        else:
            de = ttk.Entry(top, width=16)
            de.insert(0, current_dt.strftime(DAY_FMT))
        de.grid(row=1, column=1, sticky="w", padx=8, pady=2)

        ttk.Label(top, text="Available Time").grid(row=2, column=0, sticky="w", padx=8)
        time_cmb = ttk.Combobox(top, state="readonly", width=14, values=[])
        time_cmb.grid(row=2, column=1, sticky="w", padx=8, pady=2)

        def load_slots():
            date_str = de.get() if not HAS_TKCAL else de.get_date().strftime(DAY_FMT)
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

        ttk.Button(top, text="Find Slots", command=load_slots).grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(2, 6))

        def do_resched():
            date_str = de.get() if not HAS_TKCAL else de.get_date().strftime(DAY_FMT)
            t = time_cmb.get().strip()
            if not t:
                messagebox.showwarning("Missing", "Choose a time slot (Find Slots).")
                return
            try:
                new_when = datetime.strptime(f"{date_str} {t}", "%Y-%m-%d %H:%M")
            except ValueError:
                messagebox.showerror("Invalid", "Bad time format.")
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

        ttk.Button(top, text="Save", command=do_resched).grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))

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
        dur = self.rx_dur.get().strip()
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
        vid, day, start, end, slot = self.tree_av.item(sel[0], "values")
        # date
        if HAS_TKCAL:
            try:
                self.av_date.set_date(datetime.strptime(day, DAY_FMT).date())
            except Exception:
                pass
        else:
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
        date_s = self.av_date.get() if not HAS_TKCAL else self.av_date.get_date().strftime(DAY_FMT)
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

        # upsert per-day rule
        with SessionLocal() as db:
            existing = db.scalar(
                select(DoctorAvailability)
                .where(
                    DoctorAvailability.doctor_id == self.doctor.id,
                    func.date(DoctorAvailability.day) == func.date(day),
                )
                .order_by(DoctorAvailability.id.desc())
            )
            if existing:
                existing.start_time   = start_s
                existing.end_time     = end_s
                existing.slot_minutes = slot_i
                existing.day          = day.replace(hour=0, minute=0, second=0, microsecond=0)
                action = "updated"
            else:
                db.add(
                    DoctorAvailability(
                        doctor_id=self.doctor.id,
                        day=day.replace(hour=0, minute=0, second=0, microsecond=0),
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
    # Data ops — Support
    # ====================================================
    def _refresh_support(self):
        for i in self.tree_tk.get_children():
            self.tree_tk.delete(i)
        user = self.controller.current_user
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
        user = self.controller.current_user
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
        day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        day1 = day0 + timedelta(days=1)

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
            completed = sum(1 for a in appts if a.status == AppointmentStatus.completed)
            cancelled = sum(1 for a in appts if a.status == AppointmentStatus.cancelled)

            ap_ids = [a.id for a in appts]
            checked_in = 0
            if ap_ids:
                rows = db.execute(
                    select(func.count(Attendance.id))
                    .where(Attendance.appointment_id.in_(ap_ids))
                ).all()
                checked_in = sum(r[0] for r in rows)

            # utilisation (booked/total slots)
            av = db.scalar(
                select(DoctorAvailability)
                .where(
                    DoctorAvailability.doctor_id == self.doctor.id,
                    func.date(DoctorAvailability.day) == func.date(day0),
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
            booked = sum(1 for a in appts if a.status != AppointmentStatus.cancelled)
            util = f"{booked}/{total_slots}" if total_slots else "n/a"

            next_ap = next((a for a in appts if a.status != AppointmentStatus.cancelled), None)
            next_label = ""
            if next_ap:
                p = db.get(Patient, next_ap.patient_id)
                u = db.get(User, p.user_id) if p else None
                next_label = f"{next_ap.scheduled_for:%H:%M} — {u.full_name or u.email}"

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
