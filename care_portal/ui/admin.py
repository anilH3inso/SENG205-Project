# care_portal/ui/admin.py — streamlined Admin Dashboard (users, patients, check-ins, attendance, tickets, invites)
from __future__ import annotations

import csv
import secrets
import string
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
from datetime import datetime, timedelta, date

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload

from ..db import SessionLocal
from ..models import (
    User, Role, Patient,
)
from ..auth import hash_password
from .base import BaseFrame

# ---------- Optional/variant imports based on your schema ----------
try:
    from ..models import SupportTicket as Ticket  # matches your models.py
except Exception:  # pragma: no cover
    Ticket = None  # type: ignore

# Staff checkins service (your existing services/checkin.py)
try:
    from ..services.checkin import today_checkins
except Exception:  # pragma: no cover
    today_checkins = None  # type: ignore

# Optional models that may or may not exist in your DB schema
try:
    from ..models import StaffCheckin  # noqa: F401
except Exception:  # pragma: no cover
    StaffCheckin = None  # type: ignore

# No InviteCode model in your models.py; keep feature optional
try:
    from ..models import InviteCode  # type: ignore
except Exception:  # pragma: no cover
    InviteCode = None  # type: ignore


# ---------------- Utilities ----------------
STAFF_ROLES = {"doctor", "receptionist", "admin", "pharmacist", "support", "finance"}

# Roles that can receive invite codes (explicitly exclude 'patient')
STAFF_INVITE_ROLES = ["doctor", "receptionist", "admin", "pharmacist", "support", "finance"]


def _role_to_value(val: str):
    """Map free-text role -> Role enum if available; fallback to string."""
    try:
        if hasattr(Role, "__members__"):
            m = {k.lower(): v for k, v in Role.__members__.items()}
            return m.get(val.lower(), list(Role)[0])
        return Role(val)  # type: ignore[call-arg]
    except Exception:
        return val


def _role_to_text(val) -> str:
    try:
        return getattr(val, "value", None) or getattr(val, "name", None) or str(val)
    except Exception:
        return str(val)


def _gen_invite_code(n: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(max(2, n // 4)))


def _gen_temp_password(n: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


class AdminFrame(BaseFrame):
    title = "Admin Dashboard"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # UI theme & fonts
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook.Tab", font=("Segoe UI", 11, "bold"))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))

        self.nb = ttk.Notebook(self.body)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)

        # ======== USERS tab ========
        self.users_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.users_tab, text="👥 Users")
        self._build_users_tab()

        # ======== PATIENTS tab ========
        self.patients_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.patients_tab, text="🧑‍⚕️ Patients")
        self._build_patients_tab()

        # ======== STAFF CHECK-INS tab ========
        self.checkins_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.checkins_tab, text="🕒 Staff Check-ins")
        self._build_checkins_tab()

        # ======== ATTENDANCE (from check-ins) tab ========
        self.att_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.att_tab, text="✅ Attendance")
        self._build_attendance_tab()

        # ======== SUPPORT/TICKETS tab ========
        self.ticket_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.ticket_tab, text="💬 Support/Tickets")
        self._build_tickets_tab()

        # ======== INVITES / REGISTRATION CODES (optional) ========
        self.invite_tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(self.invite_tab, text="🔐 Invites")
        self._build_invites_tab()

        # ---- initial loads ----
        self.refresh_users()
        self.refresh_patients()
        self.refresh_checkins()
        self.refresh_attendance()
        self.refresh_tickets()
        self.refresh_invites()

    # ================= USERS =================
    def _build_users_tab(self):
        actions = ttk.Frame(self.users_tab)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="➕ Create User", command=self.create_user).pack(side="left", padx=4)
        ttk.Button(actions, text="✏️ Edit", command=self.edit_user).pack(side="left", padx=4)
        ttk.Button(actions, text="🗑 Remove", command=self.remove_user).pack(side="left", padx=4)
        ttk.Button(actions, text="🔑 Reset Password", command=self.reset_password).pack(side="left", padx=4)
        ttk.Button(actions, text="🔄 Refresh", command=self.refresh_users).pack(side="left", padx=4)
        ttk.Button(actions, text="📤 Export CSV", command=self.export_users_csv).pack(side="left", padx=4)

        cols = ("id", "name", "email", "role", "phone", "created")
        self.users_tree = ttk.Treeview(self.users_tab, columns=cols, show="headings", height=12)
        headers = {
            "id": ("ID", 60),
            "name": ("Name", 200),
            "email": ("Email", 240),
            "role": ("Role", 120),
            "phone": ("Phone", 140),
            "created": ("Created", 160),
        }
        for key in cols:
            t, w = headers[key]
            self.users_tree.heading(key, text=t)
            self.users_tree.column(key, width=w, anchor="center")
        self.users_tree.pack(fill="both", expand=True)
        self.users_tree.bind("<Double-1>", lambda e: self.edit_user())

    def refresh_users(self):
        for i in self.users_tree.get_children():
            self.users_tree.delete(i)
        with SessionLocal() as db:
            users = db.scalars(select(User).order_by(User.id)).all()
            for u in users:
                self.users_tree.insert(
                    "", "end", iid=str(u.id),
                    values=(
                        u.id,
                        getattr(u, "full_name", ""),
                        getattr(u, "email", ""),
                        _role_to_text(getattr(u, "role", "")),
                        getattr(u, "phone", ""),
                        getattr(u, "created_at", ""),
                    ),
                )

    def _get_selected_id(self, tree: ttk.Treeview):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a row first.")
            return None
        return int(sel[0])

    def create_user(self):
        self._open_user_editor()

    def edit_user(self):
        uid = self._get_selected_id(self.users_tree)
        if uid is None:
            return
        self._open_user_editor(uid)

    def remove_user(self):
        uid = self._get_selected_id(self.users_tree)
        if uid is None:
            return
        if not messagebox.askyesno("Confirm", "Delete this user? This cannot be undone."):
            return
        with SessionLocal() as db:
            u = db.get(User, uid)
            if not u:
                messagebox.showerror("Missing", "User not found.")
                return
            db.delete(u)
            db.commit()
        self.refresh_users()
        messagebox.showinfo("Removed", "User deleted.")

    def reset_password(self):
        uid = self._get_selected_id(self.users_tree)
        if uid is None:
            return
        new = simpledialog.askstring("Reset Password", "Enter new password:", show="*", parent=self)
        if not new:
            return
        with SessionLocal() as db:
            u = db.get(User, uid)
            if not u:
                messagebox.showerror("Missing", "User not found.")
                return
            u.password_hash = hash_password(new)
            db.commit()
        messagebox.showinfo("Updated", "Password reset.")

    def export_users_csv(self):
        if not self.users_tree.get_children():
            messagebox.showinfo("No data", "No users to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = [self.users_tree.item(i)["values"] for i in self.users_tree.get_children()]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "name", "email", "role", "phone", "created"])
            writer.writerows(rows)
        messagebox.showinfo("Exported", f"Users exported to {path}")

    def _open_user_editor(self, uid: int | None = None):
        """Simple modal editor for creating/updating users."""
        win = tk.Toplevel(self)
        win.title("User Editor")
        win.transient(self)
        win.grab_set()

        rows = [
            ("Full Name", "full_name", 28),
            ("Email", "email", 28),
            ("Phone", "phone", 20),
            ("Role (patient/doctor/admin/receptionist/pharmacist/support/finance)", "role", 42),
        ]
        # On create: add a password field prefilled with random code
        if uid is None:
            rows.append(("Password (prefilled)", "password", 28))

        for i, (label, key, width) in enumerate(rows):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=6)
            show = "*" if key == "password" else ""
            ent = ttk.Entry(win, width=width, show=show)
            ent.grid(row=i, column=1, sticky="we", padx=8, pady=6)
            setattr(win, f"_ent_{key}", ent)

        if uid is None:
            # prefill with temp password
            tmp = _gen_temp_password(10)
            win._ent_password.insert(0, tmp)

        if uid is not None:
            with SessionLocal() as db:
                u = db.get(User, uid)
                if not u:
                    messagebox.showerror("Missing", "User not found.")
                    win.destroy()
                    return
                win._ent_full_name.insert(0, getattr(u, "full_name", ""))
                win._ent_email.insert(0, getattr(u, "email", ""))
                win._ent_phone.insert(0, getattr(u, "phone", ""))
                win._ent_role.insert(0, _role_to_text(getattr(u, "role", "")))

        btns = ttk.Frame(win)
        btns.grid(row=99, column=0, columnspan=2, sticky="e", padx=8, pady=8)

        def save():
            name = getattr(win, "_ent_full_name").get().strip()
            email = getattr(win, "_ent_email").get().strip().lower()
            phone = getattr(win, "_ent_phone").get().strip()
            role_txt = getattr(win, "_ent_role").get().strip().lower()
            pwd = getattr(win, "_ent_password").get().strip() if hasattr(win, "_ent_password") else None

            if not email:
                messagebox.showerror("Invalid", "Email is required.")
                return
            with SessionLocal() as db:
                if uid is None:
                    if db.scalar(select(User).where(User.email == email)):
                        messagebox.showerror("Exists", "Email already in use.")
                        return
                    u = User(
                        full_name=name,
                        email=email,
                        phone=phone,
                        role=_role_to_value(role_txt),
                        password_hash=hash_password(pwd or _gen_temp_password(10)),
                    )
                    db.add(u)
                else:
                    u = db.get(User, uid)
                    if not u:
                        messagebox.showerror("Missing", "User not found.")
                        return
                    u.full_name = name
                    u.email = email
                    try:
                        u.phone = phone
                    except Exception:
                        pass
                    try:
                        u.role = _role_to_value(role_txt)
                    except Exception:
                        pass
                db.commit()
            self.refresh_users()
            win.destroy()

        ttk.Button(btns, text="💾 Save", command=save).pack(side="right", padx=6)
        ttk.Button(btns, text="✖ Cancel", command=win.destroy).pack(side="right", padx=6)

    # ================= PATIENTS =================
    def _build_patients_tab(self):
        top = ttk.Frame(self.patients_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Search:").pack(side="left", padx=4)
        self.pat_search = ttk.Entry(top, width=30)
        self.pat_search.pack(side="left", padx=4)
        ttk.Button(top, text="🔎 Go", command=self.refresh_patients).pack(side="left", padx=4)
        ttk.Button(top, text="➕ Add", command=self.create_patient).pack(side="left", padx=6)
        ttk.Button(top, text="✏️ Edit", command=self.edit_patient).pack(side="left", padx=6)
        ttk.Button(top, text="🗑 Remove", command=self.remove_patient).pack(side="left", padx=6)
        ttk.Button(top, text="📤 Export CSV", command=self.export_patients_csv).pack(side="left", padx=8)

        cols = ("id", "name", "dob", "phone", "status", "email")
        self.patients_tree = ttk.Treeview(self.patients_tab, columns=cols, show="headings", height=12)
        headers = {
            "id": ("ID", 60),
            "name": ("Name", 220),
            "dob": ("DOB", 110),
            "phone": ("Phone", 120),
            "status": ("Status", 120),
            "email": ("Email", 240),
        }
        for key in cols:
            t, w = headers[key]
            self.patients_tree.heading(key, text=t)
            self.patients_tree.column(key, width=w, anchor="center")
        self.patients_tree.pack(fill="both", expand=True)
        self.patients_tree.bind("<Double-1>", lambda e: self.edit_patient())

    def refresh_patients(self):
        q = (self.pat_search.get() or "").strip().lower()
        for i in self.patients_tree.get_children():
            self.patients_tree.delete(i)
        with SessionLocal() as db:
            stmt = select(Patient).options(joinedload(Patient.user)).order_by(Patient.id)
            if q:
                stmt = (
                    stmt.join(User, Patient.user_id == User.id)
                    .where(or_(User.full_name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
                )
            patients = db.scalars(stmt).all()
            for p in patients:
                u = getattr(p, "user", None)
                name = getattr(u, "full_name", "") if u else ""
                phone = getattr(u, "phone", "") if u else ""
                email = getattr(u, "email", "") if u else ""
                dob = getattr(p, "dob", "")
                status = getattr(p, "status", "active")
                self.patients_tree.insert("", "end", iid=str(p.id), values=(p.id, name, dob, phone, status, email))

    def _get_selected_patient_id(self):
        return self._get_selected_id(self.patients_tree)

    def create_patient(self):
        self._open_patient_editor()

    def edit_patient(self):
        pid = self._get_selected_patient_id()
        if pid is None:
            return
        self._open_patient_editor(pid)

    def remove_patient(self):
        pid = self._get_selected_patient_id()
        if pid is None:
            return
        if not messagebox.askyesno("Confirm", "Delete this patient? This cannot be undone."):
            return
        with SessionLocal() as db:
            p = db.get(Patient, pid)
            if not p:
                messagebox.showerror("Missing", "Patient not found.")
                return
            db.delete(p)
            db.commit()
        self.refresh_patients()
        messagebox.showinfo("Removed", "Patient deleted.")

    def _open_patient_editor(self, pid: int | None = None):
        win = tk.Toplevel(self)
        win.title("Patient Editor")
        win.transient(self)
        win.grab_set()
        for i, (label, key, width) in enumerate([
            ("Full Name", "full_name", 28),
            ("Email", "email", 28),
            ("Phone", "phone", 20),
            ("DOB (YYYY-MM-DD)", "dob", 14),
            ("Status", "status", 14),
        ]):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=6)
            ent = ttk.Entry(win, width=width)
            ent.grid(row=i, column=1, sticky="we", padx=8, pady=6)
            setattr(win, f"_ent_{key}", ent)

        if pid is not None:
            with SessionLocal() as db:
                p = db.get(Patient, pid)
                if not p:
                    messagebox.showerror("Missing", "Patient not found.")
                    win.destroy()
                    return
                u = p.user
                if u:
                    win._ent_full_name.insert(0, getattr(u, "full_name", ""))
                    win._ent_email.insert(0, getattr(u, "email", ""))
                    win._ent_phone.insert(0, getattr(u, "phone", ""))
                win._ent_dob.insert(0, getattr(p, "dob", ""))
                win._ent_status.insert(0, getattr(p, "status", "active"))

        btns = ttk.Frame(win)
        btns.grid(row=99, column=0, columnspan=2, sticky="e", padx=8, pady=8)

        def save():
            name = win._ent_full_name.get().strip()
            email = win._ent_email.get().strip().lower()
            phone = win._ent_phone.get().strip()
            dob_txt = win._ent_dob.get().strip()
            status = win._ent_status.get().strip() or "active"
            dob_val = None
            if dob_txt:
                try:
                    dob_val = datetime.strptime(dob_txt, "%Y-%m-%d").date()
                except ValueError:
                    messagebox.showerror("Invalid", "DOB must be YYYY-MM-DD")
                    return
            with SessionLocal() as db:
                if pid is None:
                    # Create backing user first if email not exists
                    u = db.scalar(select(User).where(User.email == email))
                    if not u:
                        u = User(full_name=name, email=email, phone=phone, role=_role_to_value("patient"))
                        db.add(u)
                        db.flush()
                    p = Patient(user_id=u.id)
                    try:
                        p.dob = dob_val
                    except Exception:
                        pass
                    try:
                        p.status = status
                    except Exception:
                        pass
                    db.add(p)
                else:
                    p = db.get(Patient, pid)
                    if not p:
                        messagebox.showerror("Missing", "Patient not found.")
                        return
                    u = p.user
                    if u:
                        u.full_name = name
                        u.email = email
                        try:
                            u.phone = phone
                        except Exception:
                            pass
                    try:
                        p.dob = dob_val
                    except Exception:
                        pass
                    try:
                        p.status = status
                    except Exception:
                        pass
                db.commit()
            self.refresh_patients()
            win.destroy()

        ttk.Button(btns, text="💾 Save", command=save).pack(side="right", padx=6)
        ttk.Button(btns, text="✖ Cancel", command=win.destroy).pack(side="right", padx=6)

    def export_patients_csv(self):
        if not self.patients_tree.get_children():
            messagebox.showinfo("No data", "No patients to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = [self.patients_tree.item(i)["values"] for i in self.patients_tree.get_children()]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "name", "dob", "phone", "status", "email"])
            writer.writerows(rows)
        messagebox.showinfo("Exported", f"Patients exported to {path}")

    # ================= STAFF CHECK-INS =================
    def _build_checkins_tab(self):
        top = ttk.Frame(self.checkins_tab)
        top.pack(fill="x", pady=(0, 6))
        ttk.Button(top, text="🔄 Refresh", command=self.refresh_checkins).pack(side="left")
        self.chk_info = ttk.Label(top, text="")
        self.chk_info.pack(side="left", padx=12)

        cols = ("when", "name", "role", "status", "method", "location")
        self.checkins_tree = ttk.Treeview(self.checkins_tab, columns=cols, show="headings", height=16)
        headers = {
            "when": ("Time", 120),
            "name": ("Name", 240),
            "role": ("Role", 120),
            "status": ("Status", 120),
            "method": ("Method", 120),
            "location": ("Location", 160),
        }
        for key in cols:
            t, w = headers[key]
            self.checkins_tree.heading(key, text=t)
            self.checkins_tree.column(key, width=w, anchor="center")
        self.checkins_tree.pack(fill="both", expand=True)

    def refresh_checkins(self):
        """Load today's staff check-ins with eager-loaded users to avoid DetachedInstanceError."""
        for iid in self.checkins_tree.get_children():
            self.checkins_tree.delete(iid)

        today = datetime.now().date()
        start = datetime(today.year, today.month, today.day, 0, 0, 0)
        end = start + timedelta(days=1)

        try:
            rows = []
            if StaffCheckin is not None:
                with SessionLocal() as db:
                    rows = db.scalars(
                        select(StaffCheckin)
                        .options(joinedload(StaffCheckin.user))  # eager-load related user
                        .where(and_(StaffCheckin.ts >= start, StaffCheckin.ts < end))
                        .order_by(StaffCheckin.ts)
                    ).all()
            elif today_checkins:  # service fallback
                rows = today_checkins()
            else:
                rows = []
        except Exception as e:  # pragma: no cover
            messagebox.showerror("Error", f"Failed to load check-ins:\n{e}")
            return

        count = 0
        for r in rows:
            user_obj = getattr(r, "user", None)
            who = getattr(user_obj, "full_name", None) or getattr(user_obj, "email", "Unknown")
            ts = getattr(r, "ts", None)
            ts_txt = ts.strftime("%H:%M") if isinstance(ts, datetime) else str(ts)
            role_val = _role_to_text(getattr(user_obj, "role", "")) if user_obj else ""
            status_val = getattr(getattr(r, "status", None), "value", str(getattr(r, "status", ""))) or ""
            method_val = getattr(getattr(r, "method", None), "value", str(getattr(r, "method", ""))) or ""
            loc = getattr(r, "location", "") or ""
            self.checkins_tree.insert("", "end", values=(ts_txt, who, role_val, status_val, method_val, loc))
            count += 1
        self.chk_info.configure(text=f"Today: {count} check-ins")

    # ================= ATTENDANCE (derived from check-ins) =================
    def _build_attendance_tab(self):
        top = ttk.Frame(self.att_tab)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Month (YYYY-MM)").pack(side="left", padx=4)
        self.att_month = ttk.Entry(top, width=8)
        self.att_month.pack(side="left", padx=4)
        self.att_month.insert(0, datetime.now().strftime("%Y-%m"))
        ttk.Button(top, text="📆", width=3, command=self._open_month_picker).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="🔎 Load", command=self.refresh_attendance).pack(side="left", padx=6)
        ttk.Button(top, text="📤 Export CSV", command=self.export_attendance_csv).pack(side="left", padx=6)

        cols = ("day", "checked_in", "active_staff", "daily_rate_%", "mtd_rate_%")
        self.att_tree = ttk.Treeview(self.att_tab, columns=cols, show="headings", height=16)
        headers = {
            "day": ("Day", 100),
            "checked_in": ("# Checked-in", 120),
            "active_staff": ("# Active Staff", 140),
            "daily_rate_%": ("Daily %", 120),
            "mtd_rate_%": ("MTD %", 120),
        }
        for key in cols:
            t, w = headers[key]
            self.att_tree.heading(key, text=t)
            self.att_tree.column(key, width=w, anchor="center")
        self.att_tree.pack(fill="both", expand=True)

        self.att_summary = ttk.Label(self.att_tab, text="")
        self.att_summary.pack(anchor="w", pady=6)

    def _open_month_picker(self):
        """Simple Year/Month picker without external deps."""
        win = tk.Toplevel(self)
        win.title("Pick Month")
        win.grab_set(); win.resizable(False, False)
        frm = ttk.Frame(win, padding=10); frm.pack()

        now = datetime.now()
        ttk.Label(frm, text="Year").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        years = [str(y) for y in range(now.year - 10, now.year + 2)]
        ycmb = ttk.Combobox(frm, values=years, state="readonly", width=8)
        ycmb.set(str(now.year)); ycmb.grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(frm, text="Month").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        months = [f"{m:02d}" for m in range(1, 13)]
        mcmb = ttk.Combobox(frm, values=months, state="readonly", width=8)
        mcmb.set(f"{now.month:02d}"); mcmb.grid(row=1, column=1, padx=4, pady=4)

        def apply():
            self.att_month.delete(0, "end")
            self.att_month.insert(0, f"{ycmb.get()}-{mcmb.get()}")
            win.destroy()

        btns = ttk.Frame(frm); btns.grid(row=2, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="OK", command=apply).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (win.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{x}+{y}")

    def _active_staff_count(self, db) -> int:
        try:
            users = db.scalars(select(User)).all()
            return sum(1 for u in users if _role_to_text(getattr(u, "role", "")).lower() in STAFF_ROLES)
        except Exception:
            return 0

    def refresh_attendance(self):
        month_txt = (self.att_month.get() or "").strip()
        try:
            start = datetime.strptime(month_txt + "-01", "%Y-%m-%d") if month_txt else datetime.now().replace(day=1)
        except ValueError:
            messagebox.showerror("Invalid", "Month must be YYYY-MM")
            return
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)  # next month 1st

        for i in self.att_tree.get_children():
            self.att_tree.delete(i)

        with SessionLocal() as db:
            active_staff = self._active_staff_count(db)
            rows = []
            if StaffCheckin is not None:
                rows = db.scalars(
                    select(StaffCheckin)
                    .where(and_(StaffCheckin.ts >= start, StaffCheckin.ts < end))
                ).all()

            per_day: dict[date, set[int]] = {}
            for r in rows:
                ts = getattr(r, "ts", None)
                uid = getattr(r, "user_id", None)
                if not isinstance(ts, datetime) or uid is None:
                    continue
                d = ts.date()
                per_day.setdefault(d, set()).add(int(uid))

            # iterate calendar days and compute rates
            day = start.date()
            total_days = 0
            sum_rates = 0.0
            while day < end.date():
                checked_in = len(per_day.get(day, set()))
                daily_rate = (checked_in / active_staff * 100.0) if active_staff else 0.0
                total_days += 1
                sum_rates += daily_rate
                mtd_rate = (sum_rates / total_days) if total_days else 0.0
                self.att_tree.insert(
                    "", "end",
                    values=(day.isoformat(), checked_in, active_staff, f"{daily_rate:.1f}", f"{mtd_rate:.1f}")
                )
                day += timedelta(days=1)

            overall = (
                (sum(len(s) for s in per_day.values()) / (active_staff * max(total_days, 1)) * 100.0)
                if active_staff else 0.0
            )
            self.att_summary.configure(
                text=f"Active staff: {active_staff} · Days: {total_days} · Overall attendance: {overall:.1f}%"
            )

    def export_attendance_csv(self):
        if not self.att_tree.get_children():
            messagebox.showinfo("No data", "No attendance to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = [self.att_tree.item(i)["values"] for i in self.att_tree.get_children()]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["day", "checked_in", "active_staff", "daily_rate_%", "mtd_rate_%"])
            writer.writerows(rows)
        messagebox.showinfo("Exported", f"Attendance exported to {path}")

    # ================= TICKETS / SUPPORT =================
    def _build_tickets_tab(self):
        actions = ttk.Frame(self.ticket_tab)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="🔄 Refresh", command=self.refresh_tickets).pack(side="left", padx=4)
        ttk.Button(actions, text="✉ Respond", command=self.respond_ticket).pack(side="left", padx=4)
        ttk.Button(actions, text="✔ Close Ticket", command=self.close_ticket).pack(side="left", padx=4)

        cols = ("id", "patient", "subject", "status", "created")
        self.tickets_tree = ttk.Treeview(self.ticket_tab, columns=cols, show="headings", height=14)
        headers = {
            "id": ("ID", 80),
            "patient": ("Uploader (User)", 200),
            "subject": ("Subject", 320),
            "status": ("Status", 120),
            "created": ("Created", 160),
        }
        for key in cols:
            t, w = headers[key]
            self.tickets_tree.heading(key, text=t)
            self.tickets_tree.column(key, width=w, anchor="center")
        self.tickets_tree.pack(fill="both", expand=True)

    def refresh_tickets(self):
        for i in self.tickets_tree.get_children():
            self.tickets_tree.delete(i)
        if Ticket is None:
            self.tickets_tree.insert("", "end", values=("", "", "Ticket model not found", "", ""))
            return
        with SessionLocal() as db:
            tickets = db.scalars(select(Ticket).order_by(getattr(Ticket, "created_at").desc())).all()
            for t in tickets:
                user_obj = getattr(t, "user", None)
                patient_name = getattr(user_obj, "full_name", None) or getattr(user_obj, "email", "")
                self.tickets_tree.insert(
                    "", "end", iid=str(getattr(t, "id", "")),
                    values=(
                        getattr(t, "id", ""),
                        patient_name or "",
                        getattr(t, "subject", ""),
                        getattr(getattr(t, "status", None), "value", getattr(t, "status", "")),
                        getattr(t, "created_at", ""),
                    ),
                )

    def _get_selected_ticket_id(self):
        sel = self.tickets_tree.selection()
        if not sel:
            messagebox.showwarning("Select", "Select a ticket first.")
            return None
        return int(sel[0])

    def respond_ticket(self):
        if Ticket is None:
            messagebox.showerror("Unavailable", "Ticket model not available in this build.")
            return
        tid = self._get_selected_ticket_id()
        if tid is None:
            return
        reply = simpledialog.askstring("Reply", "Enter your reply to the ticket:", parent=self)
        if not reply:
            return
        with SessionLocal() as db:
            t = db.get(Ticket, tid)
            if not t:
                messagebox.showerror("Missing", "Ticket not found.")
                return
            # Minimal inline note append (you can extend with a SupportTicketResponse table later)
            try:
                if not hasattr(t, "notes") or t.notes is None:
                    t.notes = ""
                t.notes = (t.notes or "") + f"\n[admin {datetime.utcnow():%Y-%m-%d %H:%M}] {reply}"
            except Exception:
                pass
            try:
                # If enum, accept attribute or value
                st = getattr(t, "status", None)
                if hasattr(st, "__class__") and hasattr(st.__class__, "in_progress"):
                    t.status = st.__class__.in_progress
                else:
                    t.status = "in_progress"
            except Exception:
                pass
            db.commit()
        messagebox.showinfo("Sent", "Reply saved and patient notified (if notifications are configured).")
        self.refresh_tickets()

    def close_ticket(self):
        if Ticket is None:
            messagebox.showerror("Unavailable", "Ticket model not available in this build.")
            return
        tid = self._get_selected_ticket_id()
        if tid is None:
            return
        with SessionLocal() as db:
            t = db.get(Ticket, tid)
            if not t:
                messagebox.showerror("Missing", "Ticket not found.")
                return
            try:
                st = getattr(t, "status", None)
                if hasattr(st, "__class__") and hasattr(st.__class__, "closed"):
                    t.status = st.__class__.closed
                else:
                    t.status = "closed"
            except Exception:
                pass
            db.commit()
        messagebox.showinfo("Closed", "Ticket closed.")
        self.refresh_tickets()

    # ================= INVITES (unique registration codes) =================
    def _build_invites_tab(self):
        actions = ttk.Frame(self.invite_tab)
        actions.pack(fill="x", pady=(0, 8))
        ttk.Button(actions, text="➕ Generate Code", command=self.generate_invite).pack(side="left", padx=4)
        ttk.Button(actions, text="🔄 Refresh", command=self.refresh_invites).pack(side="left", padx=4)
        ttk.Button(actions, text="📤 Export CSV", command=self.export_invites_csv).pack(side="left", padx=4)

        cols = ("id", "code", "created", "expires", "used_by")
        self.invites_tree = ttk.Treeview(self.invite_tab, columns=cols, show="headings", height=12)
        headers = {
            "id": ("ID", 60),
            "code": ("Code", 180),
            "created": ("Created", 160),
            "expires": ("Expires", 160),
            "used_by": ("Used By", 220),
        }
        for key in cols:
            t, w = headers[key]
            self.invites_tree.heading(key, text=t)
            self.invites_tree.column(key, width=w, anchor="center")
        self.invites_tree.pack(fill="both", expand=True)

        self.invite_hint = ttk.Label(
            self.invite_tab,
            text=(
                "Tip: If the InviteCode table doesn't exist yet, generated codes will be shown in a dialog\n"
                "so you can copy & share. You can wire code validation in your registration form."
            ),
        )
        self.invite_hint.pack(anchor="w", pady=6)

    def refresh_invites(self):
        for i in self.invites_tree.get_children():
            self.invites_tree.delete(i)
        if InviteCode is None:
            self.invites_tree.insert("", "end", values=("-", "(no table)", "-", "-", "-"))
            return
        with SessionLocal() as db:
            # Prefer newest first if a created_at exists
            created_col = getattr(InviteCode, "created_at", getattr(InviteCode, "created", None))
            stmt = select(InviteCode)
            if created_col is not None:
                stmt = stmt.order_by(created_col.desc())
            for inv in db.scalars(stmt).all():
                self.invites_tree.insert(
                    "", "end", iid=str(getattr(inv, "id", "")),
                    values=(
                        getattr(inv, "id", ""),
                        getattr(inv, "code", ""),
                        getattr(inv, "created_at", getattr(inv, "created", "")),
                        getattr(inv, "expires_at", getattr(inv, "expires", "")),
                        getattr(inv, "used_by", ""),
                    )
                )

    def generate_invite(self):
        """Ask for a staff role and generate a code (or 'all' to bulk for all staff roles)."""
        role = simpledialog.askstring(
            "Invite Role",
            f"Enter role for this invite (or 'all'):\n{', '.join(STAFF_INVITE_ROLES)}",
            parent=self,
        )
        if role is None:
            return
        role = role.strip().lower()
        if role != "all" and role not in STAFF_INVITE_ROLES:
            messagebox.showerror("Invalid role", f"Please enter one of: {', '.join(STAFF_INVITE_ROLES)} or 'all'")
            return

        days = simpledialog.askinteger(
            "Invite Expiry",
            "Valid for how many days? (default 14)",
            initialvalue=14,
            minvalue=1,
            maxvalue=365,
        )
        if days is None:
            days = 14
        expires = datetime.utcnow() + timedelta(days=int(days))

        if InviteCode is None:
            # Just show codes so admin can copy them
            if role == "all":
                codes = [(_r, _gen_invite_code(8)) for _r in STAFF_INVITE_ROLES]
                text = "\n".join([f"{r}: {c}" for r, c in codes])
                messagebox.showinfo("Invite Codes (not saved)", f"Expires: {expires:%Y-%m-%d}\n\n{text}")
            else:
                code = _gen_invite_code(8)
                messagebox.showinfo(
                    "Invite Code",
                    f"Code: {code}\nRole: {role}\nExpires: {expires:%Y-%m-%d}\n\n(InviteCode table not found; not saved)"
                )
            return

        created = []
        with SessionLocal() as db:
            targets = STAFF_INVITE_ROLES if role == "all" else [role]
            for r in targets:
                code = _gen_invite_code(8)
                inv = InviteCode(code=code)
                try:
                    inv.role_allowed = r
                except Exception:
                    pass
                try:
                    inv.expires_at = expires
                except Exception:
                    pass
                try:
                    inv.disabled = False
                except Exception:
                    pass
                db.add(inv)
                created.append((r, code))
            db.commit()

        self.refresh_invites()
        if role == "all":
            text = "\n".join([f"{r}: {c}" for r, c in created])
            messagebox.showinfo("Invites Created", f"Expires: {expires:%Y-%m-%d}\n\n{text}")
        else:
            messagebox.showinfo("Invite Created", f"Code: {created[0][1]}\nRole: {created[0][0]}\nExpires: {expires:%Y-%m-%d}")

    def export_invites_csv(self):
        if not self.invites_tree.get_children():
            messagebox.showinfo("No data", "No invites to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = [self.invites_tree.item(i)["values"] for i in self.invites_tree.get_children()]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "code", "created", "expires", "used_by"])
            writer.writerows(rows)
        messagebox.showinfo("Exported", f"Invites exported to {path}")
