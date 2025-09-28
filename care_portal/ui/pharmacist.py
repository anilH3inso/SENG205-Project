from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import Prescription, Patient, Doctor, StaffCheckinStatus, StaffCheckinMethod
from .base import BaseFrame

# Optional: staff check-ins service
try:
    from ..services.checkin import today_checkins
except Exception:
    today_checkins = None  # type: ignore


class PharmacistFrame(BaseFrame):
    title = "Pharmacist"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # Top actions
        toolbar = ttk.Frame(self.body)
        toolbar.pack(fill="x", pady=(6, 6))

        self.var_only_open = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Show only not-dispensed",
                        variable=self.var_only_open, command=self.refresh_data).pack(side="left")

        ttk.Button(toolbar, text="Refresh", command=self.refresh_data).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Mark as Dispensed", command=self.mark_dispensed).pack(side="left", padx=(8, 0))
        ttk.Button(toolbar, text="Print Label", command=self.print_label).pack(side="left", padx=(8, 0))

        search_wrap = ttk.Frame(toolbar)
        search_wrap.pack(side="right")
        ttk.Label(search_wrap, text="Search:").pack(side="left", padx=(0, 4))
        self.ent_search = ttk.Entry(search_wrap, width=28)
        self.ent_search.pack(side="left")
        self.ent_search.bind("<Return>", lambda _e: self.refresh_data())

        # Table
        cols = ("id", "issued_at", "patient", "doctor", "items", "notes", "dispensed")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings", height=14)
        for c, txt, w in [
            ("id", "Id", 70),
            ("issued_at", "Issued_At", 150),
            ("patient", "Patient", 180),
            ("doctor", "Doctor", 180),
            ("items", "Items", 280),
            ("notes", "Notes", 220),
            ("dispensed", "Dispensed", 100),
        ]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w", stretch=(c in {"patient", "doctor", "items", "notes"}))

        wrap = self.attach_tree_scrollbars(self.body, self.tree)
        wrap.pack(fill="both", expand=True)

        # --- Today’s Staff Check-ins (compact)
        self.checkin_frame = ttk.LabelFrame(self.body, text="Today’s Staff Check-ins")
        self.checkin_frame.pack(fill="x", padx=8, pady=8)

        cols2 = ("when", "name", "role", "status", "method", "location")
        self.checkin_tv = ttk.Treeview(self.checkin_frame, columns=cols2, show="headings", height=6)
        for c in cols2:
            self.checkin_tv.heading(c, text=c.title())
            self.checkin_tv.column(c, width=110, anchor="center")
        self.checkin_tv.pack(fill="x", padx=6, pady=6)
        ttk.Button(self.checkin_frame, text="Refresh", command=self.refresh_checkins).pack(pady=(0, 6))

        self.style_treeview(self.tree)
        self.status_lbl = ttk.Label(self.body, text="0 prescription(s) listed")
        self.status_lbl.pack(anchor="w", pady=(4, 0))

    # ------------ lifecycle hooks ------------
    def on_show(self):
        self.refresh_data()
        self.refresh_checkins()

    def set_user(self, user):
        super().set_user(user)
        # no-op

    # ------------ helpers ------------
    @staticmethod
    def _fmt_dt(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def refresh_data(self):
        try:
            self.tree.delete(*self.tree.get_children())
            q = self.ent_search.get().strip().lower()
            only_open = self.var_only_open.get()

            with SessionLocal() as db:
                stmt = (
                    select(Prescription)
                    .options(
                        selectinload(Prescription.patient).selectinload(Patient.user),
                        selectinload(Prescription.doctor).selectinload(Doctor.user),
                    )
                    .order_by(Prescription.created_at.desc())
                )
                if only_open:
                    stmt = stmt.where(Prescription.is_dispensed.is_(False))
                rows = db.scalars(stmt).all()

                total = 0
                for p in rows:
                    # Lightweight filtering by patient/doctor/medication text
                    pat = p.patient.user.full_name if p.patient and p.patient.user else ""
                    doc = p.doctor.user.full_name if p.doctor and p.doctor.user else ""
                    items = f"{(p.medication or '').strip()} {(p.dosage or '').strip()}".strip()
                    text_blob = " ".join([pat, doc, p.title or "", p.summary or "", items]).lower()
                    if q and q not in text_blob:
                        continue

                    issued = getattr(p, "dispensed_at", None) or getattr(p, "created_at", None)
                    disp = "Yes" if p.is_dispensed else "No"
                    notes = (p.instructions or p.summary or "")[:120]
                    self.tree.insert("", "end", values=(
                        p.id,
                        self._fmt_dt(issued),
                        pat,
                        doc,
                        items,
                        notes,
                        disp,
                    ))
                    total += 1

                self.status_lbl.config(text=f"{total} prescription(s) listed")
        except SQLAlchemyError as e:
            messagebox.showerror("Prescriptions", f"Could not load prescriptions: {e}")
        except Exception as e:
            messagebox.showerror("Prescriptions", f"Could not load prescriptions: {e}")

    def _selected_ids(self):
        ids = []
        for iid in self.tree.selection():
            try:
                vals = self.tree.item(iid, "values")
                ids.append(int(vals[0]))
            except Exception:
                pass
        return ids

    def mark_dispensed(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Mark as Dispensed", "Select one or more prescriptions first.")
            return
        with SessionLocal() as db:
            try:
                changed = 0
                now = datetime.utcnow()
                for pid in ids:
                    p = db.get(Prescription, pid)
                    if not p:
                        continue
                    if not p.is_dispensed:
                        p.is_dispensed = True
                        p.dispensed_at = now
                        changed += 1
                db.commit()
                messagebox.showinfo("Dispensed", f"Updated {changed} prescription(s).")
            except SQLAlchemyError as e:
                db.rollback()
                messagebox.showerror("Error", str(e))
        self.refresh_data()

    def print_label(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Print Label", "Select a prescription first.")
            return
        # Simple label preview
        with SessionLocal() as db:
            p = db.get(Prescription, ids[0])
            if not p:
                return
            pat = p.patient.user.full_name if p.patient and p.patient.user else "Patient"
            doc = p.doctor.user.full_name if p.doctor and p.doctor.user else "Doctor"
            msg = (
                f"Rx #{p.id}\n"
                f"Patient: {pat}\n"
                f"Doctor: {doc}\n"
                f"Medication: {p.medication or '-'}\n"
                f"Dosage: {p.dosage or '-'}\n"
                f"Instructions: {(p.instructions or p.summary or '')[:220]}"
            )
        messagebox.showinfo("Label", msg)

    # --- Staff check-ins (compact list)
    def refresh_checkins(self):
        for i in self.checkin_tv.get_children():
            self.checkin_tv.delete(i)
        rows = []
        try:
            if today_checkins:
                rows = today_checkins()
        except Exception:
            rows = []
        for r in rows:
            who = getattr(r.user, "full_name", None) or getattr(r.user, "email", "Unknown")
            ts = r.ts.strftime("%H:%M")
            role = getattr(getattr(r.user, "role", None), "value", getattr(r.user, "role", "-"))
            self.checkin_tv.insert(
                "", "end",
                values=(ts, who, role, getattr(r.status, "value", r.status), getattr(r.method, "value", r.method), r.location or "")
            )
