# care_portal/ui/finance.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import (
    Billing, BillingStatus, PaymentMethod,
    Payment, PaymentStatus,
    Appointment, Patient
)
from .base import BaseFrame
from ..services.checkin import today_checkins
from ..models import StaffCheckinStatus, StaffCheckinMethod

class FinanceFrame(BaseFrame):
    title = "Finance"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # Toolbar
        bar = ttk.Frame(self.body); bar.pack(fill="x", pady=(6, 6))
        ttk.Button(bar, text="Refresh", command=self.refresh_data).pack(side="left")
        ttk.Button(bar, text="Mark Paid", command=self.mark_paid).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Refund", command=self.refund).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Cancel", command=self.cancel).pack(side="left", padx=(8, 0))

        # Billing table
        ttk.Label(self.body, text="Invoices").pack(anchor="w")
        self.tree_b = ttk.Treeview(
            self.body,
            columns=("id", "created", "appointment", "patient", "description", "amount", "status", "method"),
            show="headings", height=12
        )
        for c, txt, w in [
            ("id", "Id", 70),
            ("created", "Created", 140),
            ("appointment", "Appt", 90),
            ("patient", "Patient", 200),
            ("description", "Description", 260),
            ("amount", "Amount", 100),
            ("status", "Status", 110),
            ("method", "Method", 100),
        ]:
            self.tree_b.heading(c, text=txt)
            self.tree_b.column(c, width=w, anchor="w", stretch=(c in {"patient", "description"}))
        wrap_b = self.attach_tree_scrollbars(self.body, self.tree_b)
        wrap_b.pack(fill="both", expand=True, pady=(2, 8))
        self.style_treeview(self.tree_b)

        # Payments table
        ttk.Label(self.body, text="Payments").pack(anchor="w")
        self.tree_p = ttk.Treeview(
            self.body,
            columns=("id", "created", "patient", "appointment", "amount", "method", "status", "notes"),
            show="headings", height=10
        )
        for c, txt, w in [
            ("id", "Id", 70),
            ("created", "Created", 140),
            ("patient", "Patient", 200),
            ("appointment", "Appt", 90),
            ("amount", "Amount", 100),
            ("method", "Method", 100),
            ("status", "Status", 110),
            ("notes", "Notes", 280),
        ]:
            self.tree_p.heading(c, text=txt)
            self.tree_p.column(c, width=w, anchor="w", stretch=(c in {"patient", "notes"}))
        wrap_p = self.attach_tree_scrollbars(self.body, self.tree_p)
        wrap_p.pack(fill="both", expand=True)
        self.style_treeview(self.tree_p)

        self.lbl_status = ttk.Label(self.body, text="")
        self.lbl_status.pack(anchor="w", pady=(4, 0))

    # ---------- hooks ----------
    def on_show(self):
        self.refresh_data()

    # ---------- helpers ----------
    @staticmethod
    def _fmt_dt(dt):
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _selected_billing_id(self):
        sel = self.tree_b.selection()
        if not sel:
            return None
        try:
            return int(self.tree_b.item(sel[0], "values")[0])
        except Exception:
            return None

    # ---------- data ops ----------
    def refresh_data(self):
        self.tree_b.delete(*self.tree_b.get_children())
        self.tree_p.delete(*self.tree_p.get_children())
        try:
            with SessionLocal() as db:
                # invoices
                bills = db.scalars(select(Billing).order_by(Billing.created_at.desc())).all()
                bi = 0
                for b in bills:
                    appt = db.get(Appointment, b.appointment_id) if b.appointment_id else None
                    pat_name = ""
                    if appt and appt.patient and appt.patient.user:
                        pat_name = appt.patient.user.full_name
                    self.tree_b.insert("", "end", values=(
                        b.id,
                        self._fmt_dt(b.created_at),
                        appt.id if appt else "",
                        pat_name,
                        b.description or "",
                        f"{float(b.amount):.2f}",
                        getattr(b.status, "value", b.status),
                        getattr(b.payment_method, "value", b.payment_method) if b.payment_method else "",
                    ))
                    bi += 1

                # payments
                pays = db.scalars(select(Payment).order_by(Payment.created_at.desc())).all()
                pi = 0
                for p in pays:
                    appt = db.get(Appointment, p.appointment_id) if p.appointment_id else None
                    pat = db.get(Patient, p.patient_id) if p.patient_id else None
                    pat_name = pat.user.full_name if pat and pat.user else ""
                    self.tree_p.insert("", "end", values=(
                        p.id,
                        self._fmt_dt(p.created_at),
                        pat_name,
                        appt.id if appt else "",
                        f"{float(p.amount):.2f}",
                        p.method or "",
                        getattr(p.status, "value", p.status),
                        (p.notes or "")[:200],
                    ))
                    pi += 1

                self.lbl_status.config(text=f"{bi} invoice(s), {pi} payment(s).")
        except SQLAlchemyError as e:
            messagebox.showerror("Finance", f"Load failed: {e}")

    def mark_paid(self):
        bid = self._selected_billing_id()
        if not bid:
            messagebox.showinfo("Mark Paid", "Select an invoice first.")
            return
        with SessionLocal() as db:
            try:
                b = db.get(Billing, bid)
                if not b:
                    return
                b.status = BillingStatus.paid
                b.payment_method = PaymentMethod.online
                b.paid_at = datetime.utcnow()
                # Also record a payment entry
                p = Payment(
                    appointment_id=b.appointment_id,
                    patient_id=b.appointment.patient_id if b.appointment else None,
                    amount=b.amount,
                    method=b.payment_method.value if b.payment_method else "Online",
                    status=PaymentStatus.paid,
                    notes=f"Invoice {b.id} marked paid",
                )
                db.add(p)
                db.commit()
                self.refresh_data()
            except SQLAlchemyError as e:
                db.rollback()
                messagebox.showerror("Mark Paid", str(e))

    def refund(self):
        bid = self._selected_billing_id()
        if not bid:
            messagebox.showinfo("Refund", "Select an invoice first.")
            return
        with SessionLocal() as db:
            try:
                b = db.get(Billing, bid)
                if not b:
                    return
                b.status = BillingStatus.refunded
                db.add(Payment(
                    appointment_id=b.appointment_id,
                    patient_id=b.appointment.patient_id if b.appointment else None,
                    amount=-abs(float(b.amount)),
                    method=(b.payment_method.value if b.payment_method else "Online"),
                    status=PaymentStatus.paid,
                    notes=f"Refund for invoice {b.id}",
                ))
                db.commit()
                self.refresh_data()
            except SQLAlchemyError as e:
                db.rollback()
                messagebox.showerror("Refund", str(e))

    def cancel(self):
        bid = self._selected_billing_id()
        if not bid:
            messagebox.showinfo("Cancel", "Select an invoice first.")
            return
        with SessionLocal() as db:
            try:
                b = db.get(Billing, bid)
                if not b:
                    return
                b.status = BillingStatus.cancelled
                db.commit()
                self.refresh_data()
            except SQLAlchemyError as e:
                db.rollback()
                messagebox.showerror("Cancel", str(e))
# In __init__:
self.checkin_frame = ttk.LabelFrame(self, text="Today’s Staff Check-ins")
self.checkin_frame.pack(fill="x", padx=8, pady=8)

cols = ("when","name","role","status","method","location")
self.checkin_tv = ttk.Treeview(self.checkin_frame, columns=cols, show="headings", height=6)
for c in cols:
    self.checkin_tv.heading(c, text=c.title())
    self.checkin_tv.column(c, width=110, anchor="center")
self.checkin_tv.pack(fill="x", padx=6, pady=6)

ttk.Button(self.checkin_frame, text="Refresh", command=self.refresh_checkins).pack(pady=(0,6))

# Add method:
def refresh_checkins(self):
    for i in self.checkin_tv.get_children():
        self.checkin_tv.delete(i)
    rows = today_checkins()
    for r in rows:
        who = getattr(r.user, "full_name", None) or getattr(r.user, "email", "Unknown")
        ts = r.ts.strftime("%H:%M")
        self.checkin_tv.insert("", "end", values=(ts, who, r.role, r.status.value, r.method.value, r.location or ""))