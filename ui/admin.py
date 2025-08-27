# ui/admin.py
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime, timedelta
from sqlalchemy import select
from ..db import SessionLocal
from ..models import User, Role
from ..auth import hash_password
from ..services.reports import ReportsService
from .base import BaseFrame

class AdminFrame(BaseFrame):
    title = "Admin Dashboard"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)

        # --- Users tab ---
        self.users_tab = ttk.Frame(self.nb)
        self.nb.add(self.users_tab, text="Users")

        actions = ttk.Frame(self.users_tab)
        actions.pack(fill="x", padx=8, pady=6)
        ttk.Button(actions, text="Create User", command=self.create_user).pack(side="left")
        ttk.Button(actions, text="Reset Password", command=self.reset_password).pack(side="left", padx=6)
        ttk.Button(actions, text="Refresh", command=self.refresh_users).pack(side="left")

        self.users_tree = ttk.Treeview(
            self.users_tab, columns=("id","name","email","role"), show="headings"
        )
        for c, w in ("id",60), ("name",200), ("email",240), ("role",120):
            self.users_tree.heading(c, text=c.title()); self.users_tree.column(c, width=w)
        self.users_tree.pack(fill="both", expand=True, padx=8, pady=8)

        # --- Reports tab ---
        self.reports_tab = ttk.Frame(self.nb)
        self.nb.add(self.reports_tab, text="Reports")

        filt = ttk.Frame(self.reports_tab); filt.pack(fill="x", padx=8, pady=8)
        ttk.Label(filt, text="Start (YYYY-MM-DD)").pack(side="left")
        self.start_in = ttk.Entry(filt, width=14); self.start_in.pack(side="left", padx=4)
        ttk.Label(filt, text="End (YYYY-MM-DD)").pack(side="left")
        self.end_in = ttk.Entry(filt, width=14); self.end_in.pack(side="left", padx=4)
        ttk.Button(filt, text="Run", command=self.run_report).pack(side="left", padx=6)

        self.report_tree = ttk.Treeview(
            self.reports_tab, columns=("doctor","count"), show="headings"
        )
        self.report_tree.heading("doctor", text="Doctor")
        self.report_tree.heading("count", text="Appointments")
        self.report_tree.column("doctor", width=300)
        self.report_tree.column("count", width=140)
        self.report_tree.pack(fill="both", expand=True, padx=8, pady=8)

        # Defaults for report range
        today = datetime.now().date()
        start = today - timedelta(days=7)
        self.start_in.insert(0, start.strftime("%Y-%m-%d"))
        self.end_in.insert(0, today.strftime("%Y-%m-%d"))

        self.refresh_users()

    # ---- Users ----
    def refresh_users(self):
        for i in self.users_tree.get_children(): self.users_tree.delete(i)
        with SessionLocal() as db:
            users = db.scalars(select(User).order_by(User.id)).all()
            for u in users:
                self.users_tree.insert("", "end", iid=str(u.id),
                    values=(u.id, u.full_name, u.email, u.role))

    def _get_selected_user_id(self):
        sel = self.users_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a user row first.")
            return None
        return int(sel[0])

    def create_user(self):
        role = simpledialog.askstring(
            "Role", "Enter role (patient/doctor/admin/receptionist):", parent=self
        )
        if not role: return
        role = role.strip().lower()
        if role not in {r.value for r in Role}:
            messagebox.showerror("Invalid", "Unknown role.")
            return
        name = simpledialog.askstring("Full Name", "Enter full name:", parent=self) or ""
        email = simpledialog.askstring("Email", "Enter email:", parent=self)
        if not email: return
        pw = simpledialog.askstring("Password", "Enter initial password:", show="*", parent=self)
        if not pw: return
        with SessionLocal() as db:
            if db.scalar(select(User).where(User.email == email.strip().lower())):
                messagebox.showerror("Exists", "Email already in use.")
                return
            u = User(
                email=email.strip().lower(),
                full_name=name.strip(),
                role=Role(role),
                password_hash=hash_password(pw),
            )
            db.add(u); db.commit()
        self.refresh_users()
        messagebox.showinfo("Created", f"User {email} created.")

    def reset_password(self):
        uid = self._get_selected_user_id()
        if uid is None: return
        new = simpledialog.askstring("Reset Password", "Enter new password:", show="*", parent=self)
        if not new: return
        with SessionLocal() as db:
            u = db.get(User, uid)
            if not u:
                messagebox.showerror("Missing", "User not found.")
                return
            u.password_hash = hash_password(new)
            db.commit()
        messagebox.showinfo("Updated", "Password reset.")

    # ---- Reports ----
    def run_report(self):
        try:
            start = datetime.strptime(self.start_in.get().strip(), "%Y-%m-%d")
            end = datetime.strptime(self.end_in.get().strip(), "%Y-%m-%d").replace(
                hour=23, minute=59, second=59
            )
        except ValueError:
            messagebox.showerror("Invalid", "Dates must be YYYY-MM-DD")
            return
        rows = ReportsService.appointments_per_doctor(start, end)
        for i in self.report_tree.get_children(): self.report_tree.delete(i)
        for doctor_name, count in rows:
            self.report_tree.insert("", "end", values=(doctor_name or "(unnamed)", count))
