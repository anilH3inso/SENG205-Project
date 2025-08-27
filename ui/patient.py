# care_portal/ui/patient.py
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import scrolledtext
from datetime import datetime

# Calendar picker (optional dependency)
try:
    from tkcalendar import DateEntry  # pip install tkcalendar
    HAS_TKCAL = True
except Exception:
    HAS_TKCAL = False

import requests
from sqlalchemy import select, join
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    User,
    Patient,
    Doctor,
    Appointment,
    AppointmentStatus,
    MedicalRecord,
    RecordAuthor,
    Billing,
    BillingStatus,
    PaymentMethod,
)
from ..services.appointments import AppointmentService
from .base import BaseFrame

DATE_FMT = "%Y-%m-%d %H:%M"
SLOT_HINT = "…thinking…"

# ---- AI API config ----
API_URL = "http://127.0.0.1:8000/ai/chat"  # run: python -m care_portal.api.app_bot
API_TIMEOUT = 15  # seconds


class PatientFrame(BaseFrame):
    title = "Patient Portal"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # ---------------- Left: booking form ----------------
        left = ttk.LabelFrame(root, text="Book an Appointment", padding=10)
        left.pack(side="left", fill="y", padx=(0, 8))

        ttk.Label(left, text="Doctor").grid(row=0, column=0, sticky="w")
        self.doctor_cmb = ttk.Combobox(left, state="readonly", width=32)
        self.doctor_cmb.grid(row=1, column=0, sticky="ew", pady=(2, 8))

        ttk.Label(left, text="Date").grid(row=2, column=0, sticky="w")
        if HAS_TKCAL:
            self.date_in = DateEntry(left, width=30, date_pattern="yyyy-mm-dd")
        else:
            self.date_in = ttk.Entry(left, width=32)
            self.date_in.insert(0, datetime.now().strftime("%Y-%m-%d"))
        self.date_in.grid(row=3, column=0, sticky="ew", pady=(2, 8))

        ttk.Label(left, text="Time (available)").grid(row=4, column=0, sticky="w")
        self.time_cmb = ttk.Combobox(left, state="readonly", width=32, values=[])
        self.time_cmb.grid(row=5, column=0, sticky="ew", pady=(2, 8))

        ttk.Label(left, text="Reason").grid(row=6, column=0, sticky="w")
        self.reason_in = ttk.Entry(left, width=32)
        self.reason_in.grid(row=7, column=0, sticky="ew", pady=(2, 8))

        ttk.Button(left, text="Find Slots", command=self.refresh_slots).grid(
            row=8, column=0, sticky="ew", pady=(6, 2)
        )
        ttk.Button(left, text="Book", command=self.book).grid(
            row=9, column=0, sticky="ew", pady=(2, 2)
        )

        # ---------------- Right: tabs ----------------
        self.nb = ttk.Notebook(root)
        self.nb.pack(side="left", fill="both", expand=True)

        self.tab_appt = ttk.Frame(self.nb)
        self.tab_med = ttk.Frame(self.nb)
        self.tab_bill = ttk.Frame(self.nb)
        self.tab_ai = ttk.Frame(self.nb)

        self.nb.add(self.tab_appt, text="My Appointments")
        self.nb.add(self.tab_med, text="Medical Records")
        self.nb.add(self.tab_bill, text="Billing")
        self.nb.add(self.tab_ai, text="AI Help")

        # ---- Appointments tab ----
        ap_cols = ("id", "when", "doctor", "reason", "status")
        self.tree_ap = ttk.Treeview(
            self.tab_appt, columns=ap_cols, show="headings", height=12
        )
        for c, w in (("id", 60), ("when", 150), ("doctor", 220), ("reason", 240), ("status", 100)):
            self.tree_ap.heading(c, text=c.title())
            self.tree_ap.column(c, width=w)
        self.tree_ap.pack(fill="both", expand=True, padx=6, pady=6)

        # ---- Medical Records tab ----
        info = ttk.LabelFrame(self.tab_med, text="Profile Snapshot", padding=8)
        info.pack(fill="x", padx=6, pady=(6, 6))

        ttk.Label(info, text="Allergies").grid(row=0, column=0, sticky="nw")
        self.allergies_txt = tk.Text(info, height=3, width=70)
        self.allergies_txt.grid(row=0, column=1, sticky="ew", padx=6)
        self.allergies_txt.config(state="disabled")

        controls = ttk.LabelFrame(self.tab_med, text="Add Medical Record", padding=8)
        controls.pack(fill="x", padx=6, pady=(0, 6))

        ttk.Label(controls, text="Record / Note").grid(row=0, column=0, sticky="w")
        self.rec_txt = tk.Text(controls, height=4, width=60)
        self.rec_txt.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        ttk.Button(controls, text="Add Record", command=self.add_med_record).grid(
            row=1, column=1, padx=8
        )

        edit_all = ttk.Frame(self.tab_med)
        edit_all.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(edit_all, text="Allergies (editable)").pack(side="left")
        ttk.Button(edit_all, text="Edit & Save", command=self.edit_allergies).pack(
            side="left", padx=8
        )

        # ---- Billing tab ----
        bill_cols = ("id", "desc", "amount", "status", "paid_at", "method")
        self.tree_bill = ttk.Treeview(
            self.tab_bill, columns=bill_cols, show="headings", height=10
        )
        heads = {
            "id": ("ID", 60),
            "desc": ("Description", 240),
            "amount": ("Amount", 100),
            "status": ("Status", 100),
            "paid_at": ("Paid At", 160),
            "method": ("Method", 120),
        }
        for c in bill_cols:
            title, w = heads[c]
            self.tree_bill.heading(c, text=title)
            self.tree_bill.column(c, width=w)
        self.tree_bill.pack(fill="both", expand=True, padx=6, pady=6)

        payf = ttk.Frame(self.tab_bill)
        payf.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(payf, text="Mark Selected as Paid", command=self.mark_bill_paid).pack(
            side="left"
        )

        # ---- AI Help tab ----
        ai_wrap = ttk.Frame(self.tab_ai)
        ai_wrap.pack(fill="both", expand=True, padx=8, pady=8)

        self.chat_box = scrolledtext.ScrolledText(
            ai_wrap, height=18, wrap="word", state="disabled"
        )
        self.chat_box.pack(fill="both", expand=True)

        ai_bottom = ttk.Frame(ai_wrap)
        ai_bottom.pack(fill="x", pady=(8, 0))

        self.ai_entry = ttk.Entry(ai_bottom)
        self.ai_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(ai_bottom, text="Send", command=self._ai_send).pack(side="left")

        ttk.Label(
            ai_wrap,
            text="Tip: Ask about booking, availability, prescriptions, billing, or how to use the portal. "
                 "AI assistant may make mistakes. For medical issues, contact your clinician."
        ).pack(anchor="w", pady=(6, 0))

        # Data caches
        self.patient = None
        self.doctors = {}        # label -> id
        self.doctor_labels = {}  # id -> label

        # Initial loads
        self.load_patient()
        self.refresh_doctors()
        self.refresh_appointments()
        self.refresh_billing()
        self.load_allergies()

        # AI Adapter + greet
        self.ai_adapter = AIAdapter()
        self._ai_greet()

    # ---------------- Data loading ----------------
    def load_patient(self):
        """Bind self.patient to the logged-in user's patient row."""
        user = self.controller.current_user
        if not user:
            return
        with SessionLocal() as db:
            self.patient = db.scalar(select(Patient).where(Patient.user_id == user.id))

    def refresh_doctors(self):
        """Populate doctor combo with labels and remember ids."""
        self.doctors.clear()
        self.doctor_labels.clear()
        docs = AppointmentService.list_doctors()
        labels = []
        for d in docs:
            label = f"Dr. {d.user.full_name or d.user.email} ({d.specialty})"
            self.doctors[label] = d.id
            self.doctor_labels[d.id] = label
            labels.append(label)
        self.doctor_cmb["values"] = labels
        if labels:
            self.doctor_cmb.current(0)

    def refresh_slots(self):
        label = self.doctor_cmb.get()
        if not label:
            messagebox.showwarning("Missing", "Select a doctor.")
            return
        doctor_id = self.doctors.get(label)
        if not doctor_id:
            return

        date_str = (
            self.date_in.get()
            if not HAS_TKCAL
            else self.date_in.get_date().strftime("%Y-%m-%d")
        )
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            messagebox.showerror("Invalid date", "Use YYYY-MM-DD")
            return

        slots = AppointmentService.get_available_slots(doctor_id, day)
        self.time_cmb["values"] = slots
        if slots:
            self.time_cmb.current(0)
        else:
            self.time_cmb.set("")
            messagebox.showinfo("No Slots", "No available times for the selected date.")

    def refresh_appointments(self):
        """List this patient's appointments."""
        for i in self.tree_ap.get_children():
            self.tree_ap.delete(i)
        if not self.patient:
            return

        with SessionLocal() as db:
            appts = db.scalars(
                select(Appointment)
                .where(Appointment.patient_id == self.patient.id)
                .order_by(Appointment.scheduled_for.desc())
            ).all()
            for a in appts:
                doc_label = self.doctor_labels.get(a.doctor_id, f"Doctor#{a.doctor_id}")
                when = a.scheduled_for.strftime(DATE_FMT)
                self.tree_ap.insert(
                    "", "end",
                    values=(a.id, when, doc_label, a.reason or "", a.status.value)
                )

    def load_allergies(self):
        """Load allergies text into read-only box."""
        if not self.patient:
            return
        with SessionLocal() as db:
            p = db.get(Patient, self.patient.id)
            text = p.allergies if p and p.allergies else ""
        self.allergies_txt.config(state="normal")
        self.allergies_txt.delete("1.0", "end")
        self.allergies_txt.insert("end", text)
        self.allergies_txt.config(state="disabled")

    def refresh_billing(self):
        """Load bills related to this patient's appointments."""
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
                ).select_from(j).where(Appointment.patient_id == self.patient.id)
                .order_by(Billing.created_at.desc())
            ).all()

            for (bid, desc, amt, status, paid_at, method) in rows:
                self.tree_bill.insert(
                    "", "end",
                    values=(
                        bid,
                        desc or "",
                        f"{amt:.2f}",
                        status.value if hasattr(status, "value") else str(status),
                        paid_at.strftime(DATE_FMT) if paid_at else "",
                        method.value if method else "",
                    )
                )

    # ---------------- Actions: booking ----------------
    def book(self):
        if not self.controller.current_user or not self.patient:
            messagebox.showerror("Error", "Not logged in as patient.")
            return

        label = self.doctor_cmb.get()
        if not label:
            messagebox.showwarning("Missing", "Select a doctor.")
            return
        doctor_id = self.doctors.get(label)

        date_str = (
            self.date_in.get()
            if not HAS_TKCAL
            else self.date_in.get_date().strftime("%Y-%m-%d")
        )
        time_str = self.time_cmb.get().strip()
        if not time_str:
            messagebox.showwarning("Missing", "Choose a time slot (click 'Find Slots').")
            return

        try:
            when = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError:
            messagebox.showerror("Invalid time", "Invalid selected time.")
            return

        try:
            ap = AppointmentService.book(self.patient.id, doctor_id, when, self.reason_in.get().strip())
        except ValueError as e:
            messagebox.showerror("Booking error", str(e))
            return

        messagebox.showinfo("Booked", f"Appointment #{ap.id} at {when.strftime(DATE_FMT)}")
        self.refresh_appointments()
        self.refresh_slots()

    # ---------------- Actions: medical records ----------------
    def add_med_record(self):
        if not self.patient or not self.controller.current_user:
            return
        text = self.rec_txt.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Missing", "Enter a record/note.")
            return

        user = self.controller.current_user
        try:
            role_val = getattr(user.role, "value", user.role)
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
            db.add(rec)
            db.commit()

        self.rec_txt.delete("1.0", "end")
        messagebox.showinfo("Saved", "Medical record added.")

    def edit_allergies(self):
        if not self.patient:
            return
        if self.allergies_txt["state"] == "disabled":
            self.allergies_txt.config(state="normal")
            return
        new_text = self.allergies_txt.get("1.0", "end").strip()
        with SessionLocal() as db:
            p = db.get(Patient, self.patient.id)
            if p:
                p.allergies = new_text
                db.commit()
        self.allergies_txt.config(state="disabled")
        messagebox.showinfo("Saved", "Allergies updated.")

    # ---------------- Actions: billing ----------------
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
        pm.grid(row=1, column=0, padx=8, sticky="ew")
        pm.current(0)

        ttk.Label(top, text="Transaction ID (optional)").grid(row=2, column=0, padx=8, pady=(6, 0), sticky="w")
        tx = ttk.Entry(top, width=30)
        tx.grid(row=3, column=0, padx=8, pady=(2, 8), sticky="ew")

        def do_pay():
            method_str = pm.get()
            txn = tx.get().strip() or None
            with SessionLocal() as db:
                b = db.get(Billing, bill_id)
                if b and b.status != BillingStatus.paid:
                    b.status = BillingStatus.paid
                    b.payment_method = PaymentMethod(method_str)
                    b.paid_at = datetime.now()
                    b.transaction_id = txn
                    db.commit()
            top.destroy()
            self.refresh_billing()
            messagebox.showinfo("Paid", "Bill marked as paid.")

        ttk.Button(top, text="Confirm", command=do_pay).grid(row=4, column=0, padx=8, pady=(0, 8), sticky="ew")

    # ---------------- AI Help: helpers ----------------
    def _chat_append(self, who: str, text: str):
        """Append a chat bubble line to the chat box."""
        self.chat_box.config(state="normal")
        prefix = "You: " if who == "user" else "Bot: "
        self.chat_box.insert("end", f"{prefix}{text.strip()}\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def _ai_greet(self):
        self._chat_append("bot", "Hi! I’m your Care Assistant. I can help with booking, availability, billing, and where to find things in your dashboard.")

    def _ai_send(self):
        q = self.ai_entry.get().strip()
        if not q:
            return
        self.ai_entry.delete(0, "end")
        self._chat_append("user", q)

        # Minimal, non-sensitive context
        user = self.controller.current_user
        first_name = (getattr(user, "full_name", "") or getattr(user, "email", "there")).split()[0]
        ctx = {
            "first_name": first_name,
            "user_id": getattr(user, "id", 0),
            "patient_id": getattr(self.patient, "id", None),
            "timezone": "Australia/Melbourne",
        }
        # If a doctor is selected, add it
        label = self.doctor_cmb.get()
        if label and hasattr(self, "doctors"):
            ctx["doctor_id"] = self.doctors.get(label)

        # Show typing indicator & mark its start
        self._chat_append("bot", SLOT_HINT)
        self.chat_box.config(state="normal")
        try:
            self.chat_box.mark_set("thinking", "end-2l linestart")
        finally:
            self.chat_box.config(state="disabled")

        def worker():
            try:
                ans = self.ai_adapter.ask(q, ctx)
            except Exception:
                ans = "Sorry, I ran into a problem answering that."
            self.after(0, lambda: self._ai_replace_thinking(ans))

        threading.Thread(target=worker, daemon=True).start()

    def _ai_replace_thinking(self, answer: str):
        """Replace the '…thinking…' line with the actual answer (thread-safe)."""
        self.chat_box.config(state="normal")
        try:
            self.chat_box.delete("thinking", "thinking lineend+1c")
        except Exception:
            pass
        self.chat_box.insert("end", f"Bot: {answer.strip()}\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")


# ---------------- Swappable AI adapter (real API) ----------------
class AIAdapter:
    """
    Calls your local FastAPI endpoint for answers.
    Keep the signature ask(question, context) -> str so the UI doesn't change.
    """

    def ask(self, question: str, context: dict | None = None) -> str:
        ctx = context or {}
        payload = {
            "user_id": ctx.get("user_id", 0),
            "session_id": ctx.get("session_id", "sess-local"),
            "message": question,
            "context": ctx,
            "allow_tools": True,
        }
        try:
            r = requests.post(API_URL, json=payload, timeout=API_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception:
            return ("Sorry — the assistant service isn’t reachable right now. "
                    "Make sure the API server is running on 127.0.0.1:8000 "
                    "(python -m care_portal.api.app_bot).")

        return data.get("answer", "Sorry, I couldn't find an answer.")
