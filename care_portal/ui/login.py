# care_portal/ui/login.py
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import Optional, Dict

from sqlalchemy import select, or_, func
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import (
    User,
    Role,
    Patient,
    Doctor,
    Receptionist,
    AdminProfile,
    AdminLevel,
    Pharmacist,
    SupportAgent,
    FinanceOfficer,
    StaffCheckinStatus,
    StaffCheckinMethod,
)
from ..auth import (
    hash_password,           # kept if you need local hashing in future flows
    verify_password,         # kept for completeness; not used directly in login now
    authenticate_user,       # NEW: centralized login
    register_user,           # NEW: centralized registration (handles invites, email uniqueness, hashing)
)
from .base import BaseFrame, attach_placeholder  # themed placeholders

# Staff check-in
from ..services.checkin import record_checkin, today_checkin_by_user

# Password reset services (demo accepts ANY code)
from ..services.password_reset import create_reset_token_for_user, apply_reset_with_token

# Email validator
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Roles supported by the UI (ordering also used by pickers)
ALL_ROLES = [
    Role.patient.value,
    Role.doctor.value,
    Role.receptionist.value,
    Role.admin.value,
    Role.pharmacist.value,
    Role.support.value,
    Role.finance.value,
]


# ==========================
# Role Picker (shared)
# ==========================
class RolePicker(ttk.Frame):
    """Large role selection buttons."""
    def __init__(self, parent, on_pick):
        super().__init__(parent)
        self.on_pick = on_pick
        self._build()

    def _build(self):
        lbl = ttk.Label(self, text="Choose a role", font=("Segoe UI", 12, "bold"))
        lbl.pack(pady=(0, 8))

        grid = ttk.Frame(self)
        grid.pack()

        roles = [
            ("Patient", Role.patient.value),
            ("Doctor", Role.doctor.value),
            ("Receptionist", Role.receptionist.value),
            ("Admin", Role.admin.value),
            ("Pharmacist", Role.pharmacist.value),
            ("Support", Role.support.value),
            ("Finance", Role.finance.value),
        ]

        for i, (label, value) in enumerate(roles):
            ttk.Button(grid, text=label, command=lambda v=value: self.on_pick(v))\
               .grid(row=i // 4, column=i % 4, padx=8, pady=8,
                     ipadx=10, ipady=10, sticky="nsew")

        for c in range(4):
            grid.columnconfigure(c, weight=1)


# ==========================
# Login / Register Frame
# ==========================
class LoginFrame(BaseFrame):
    title = "Care Portal — Login / Register"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # Keep a reference so we can switch tabs after registration
        self._nb = ttk.Notebook(self.body)
        self._nb.pack(expand=True, fill="both")

        # ---------------- Tab: Log In ----------------
        self.t_login = ttk.Frame(self._nb)
        self._nb.add(self.t_login, text="Log In")

        # Centering wrapper
        login_center = ttk.Frame(self.t_login)
        login_center.pack(expand=True, fill="both", padx=16, pady=16)
        login_center.grid_rowconfigure(0, weight=1)
        login_center.grid_rowconfigure(2, weight=1)
        login_center.grid_columnconfigure(0, weight=1)

        self.login_stack = ttk.Frame(login_center, padding=16)
        self.login_stack.grid(row=1, column=0)

        # Start on role picker
        self._login_role_picker = RolePicker(self.login_stack, on_pick=self._show_login_form_for_role)
        self._login_role_picker.pack()

        # role-specific login forms
        self._login_forms: Dict[str, ttk.Frame] = {}
        self._build_login_forms()

        # ---------------- Tab: Register ----------------
        self.t_reg = ttk.Frame(self._nb)
        self._nb.add(self.t_reg, text="Register")

        reg_center = ttk.Frame(self.t_reg)
        reg_center.pack(expand=True, fill="both", padx=16, pady=16)
        reg_center.grid_rowconfigure(0, weight=1)
        reg_center.grid_rowconfigure(2, weight=1)
        reg_center.grid_columnconfigure(0, weight=1)

        self.reg_stack = ttk.Frame(reg_center, padding=16)
        self.reg_stack.grid(row=1, column=0)

        self._reg_role_picker = RolePicker(self.reg_stack, on_pick=self._show_register_form_for_role)
        self._reg_role_picker.pack()

        # role-specific register forms
        self._reg_forms: Dict[str, ttk.Frame] = {}
        # store common widgets per role
        self._reg_common: Dict[str, Dict[str, ttk.Entry]] = {}
        self._build_register_forms()

        # Optional: Link-like button style
        try:
            style = ttk.Style(self)
            style.configure("Link.TButton", relief="flat", padding=0)
        except Exception:
            pass

    # ==========================================================
    # LOGIN (role-first)
    # ==========================================================
    def _build_login_forms(self):
        """Build a simple login form per role with consistent UX."""
        for role_val in ALL_ROLES:
            f = ttk.Frame(self.login_stack)
            self._login_forms[role_val] = f

            head = ttk.Frame(f)
            head.pack(fill="x")
            ttk.Label(head, text=f"Log in as {role_val.title()}",
                      font=("Segoe UI", 12, "bold")).pack(side="left")
            ttk.Button(head, text="Change role", command=self._back_to_login_role_picker).pack(side="right")

            body = ttk.Frame(f, padding=(0, 12))
            body.pack()

            ttk.Label(body, text="Email or Username (Full Name)").grid(row=0, column=0, sticky="w")
            ent_user = ttk.Entry(body, width=40)
            ent_user.grid(row=1, column=0, pady=4, sticky="w")
            attach_placeholder(ent_user, "e.g. pt01@care.local or Jane Test")

            ttk.Label(body, text="Password").grid(row=2, column=0, sticky="w")
            ent_pw = ttk.Entry(body, width=40, show="*")
            ent_pw.grid(row=3, column=0, pady=4, sticky="w")

            # Autofocus email/username
            ent_user.focus_set()

            show_pw = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                body, text="Show password", variable=show_pw,
                command=lambda e=ent_pw, v=show_pw: e.configure(show="" if v.get() else "*")
            ).grid(row=4, column=0, sticky="w")

            btn_row = ttk.Frame(body)
            btn_row.grid(row=5, column=0, pady=(8, 0), sticky="ew")

            ttk.Button(
                btn_row, text="Log In",
                command=lambda r=role_val, u=ent_user, p=ent_pw: self._do_login_for_role(r, u.get().strip(), p.get())
            ).pack(side="left")

            # Right-aligned link actions
            link_row = ttk.Frame(body)
            link_row.grid(row=6, column=0, sticky="ew", pady=(6, 0))
            link_row.columnconfigure(0, weight=1)

            def _link(btn: ttk.Button):
                try:
                    btn.configure(style="Link.TButton", cursor="hand2")
                except Exception:
                    pass

            forgot_btn = ttk.Button(link_row, text="Forgot password?",
                                    command=self._open_pw_reset_request)
            _link(forgot_btn)
            forgot_btn.grid(row=0, column=1, sticky="e", padx=(6, 0))

            have_code_btn = ttk.Button(link_row, text="Have a reset code?",
                                       command=self._open_pw_reset_apply)
            _link(have_code_btn)
            have_code_btn.grid(row=0, column=2, sticky="e")

            # Enter to submit
            ent_user.bind("<Return>", lambda _e, r=role_val, u=ent_user, p=ent_pw:
                          self._do_login_for_role(r, u.get().strip(), p.get()))
            ent_pw.bind("<Return>", lambda _e, r=role_val, u=ent_user, p=ent_pw:
                        self._do_login_for_role(r, u.get().strip(), p.get()))

    def _show_login_form_for_role(self, role_val: str):
        """Hide role picker, show selected login form."""
        self._login_role_picker.forget()
        for f in self._login_forms.values():
            f.forget()
        frm = self._login_forms[role_val]
        frm.pack(fill="both", expand=True)

        # Try to focus the first Entry
        try:
            entries_container = [w for w in frm.winfo_children() if isinstance(w, ttk.Frame)][1]  # body
            entries = [w for w in entries_container.winfo_children() if isinstance(w, ttk.Entry)]
            if entries:
                entries[0].focus_set()
        except Exception:
            pass

    def _back_to_login_role_picker(self):
        for f in self._login_forms.values():
            f.forget()
        self._login_role_picker.pack()

    # ---------- staff check-in modal after successful login ----------
    def _prompt_staff_checkin(self, user: User, role_value: str):
        """
        Modal with 3 options:
          - Check-in (Onsite) -> checked_in/login
          - I’m Remote (Skip) -> skipped/remote
          - Skip               -> skipped/login
        Also guards against duplicate checked_in for today.
        """
        # Duplicate guard for 'checked_in' today
        try:
            todays = today_checkin_by_user(user.id)
            already_checked_in = any(r.status == StaffCheckinStatus.checked_in for r in todays)
        except Exception as e:
            print(f"[Login] today_checkin_by_user failed: {e}")
            already_checked_in = False

        win = tk.Toplevel(self)
        win.title("Staff Check-in")
        win.grab_set()
        win.resizable(False, False)

        who = getattr(user, "full_name", None) or getattr(user, "email", "User")
        ttk.Label(
            win,
            text=f"Welcome {who}\nWould you like to check in for today?",
            justify="center"
        ).pack(padx=16, pady=(16, 8))

        info = ttk.Label(
            win,
            text=("You can skip if you are not at the hospital.\n"
                  "Your choice will be recorded for Receptionist/Admin attendance."),
            justify="center"
        )
        info.pack(padx=16, pady=(0, 10))

        btns = ttk.Frame(win)
        btns.pack(padx=16, pady=(0, 16))

        def do_checkin_onsite():
            nonlocal already_checked_in
            try:
                if not already_checked_in:
                    record_checkin(
                        user_id=user.id,
                        role_value=role_value,
                        status=StaffCheckinStatus.checked_in,
                        method=StaffCheckinMethod.login,
                        note=None,
                        location="Onsite",
                    )
            except Exception as e:
                print(f"[Login] record_checkin (onsite) failed: {e}")
            win.destroy()

        def do_remote_skip():
            try:
                record_checkin(
                    user_id=user.id,
                    role_value=role_value,
                    status=StaffCheckinStatus.skipped,
                    method=StaffCheckinMethod.remote,
                    note="Remote login / not in hospital",
                    location="Offsite",
                )
            except Exception as e:
                print(f"[Login] record_checkin (remote skip) failed: {e}")
            win.destroy()

        def do_plain_skip():
            try:
                record_checkin(
                    user_id=user.id,
                    role_value=role_value,
                    status=StaffCheckinStatus.skipped,
                    method=StaffCheckinMethod.login,
                    note="User chose to skip check-in",
                    location=None,
                )
            except Exception as e:
                print(f"[Login] record_checkin (skip) failed: {e}")
            win.destroy()

        # Buttons
        ttk.Button(btns, text="Check-in (Onsite)", command=do_checkin_onsite).grid(row=0, column=0, padx=6)
        ttk.Button(btns, text="I’m Remote (Skip)", command=do_remote_skip).grid(row=0, column=1, padx=6)
        ttk.Button(btns, text="Skip", command=do_plain_skip).grid(row=0, column=2, padx=6)

        # Disable Check-in if already checked in
        if 'already_checked_in' in locals() and already_checked_in:
            for child in btns.winfo_children():
                if isinstance(child, ttk.Button) and "Check-in" in child.cget("text"):
                    child.state(["disabled"])

            ttk.Label(
                win,
                text="You’re already checked in today.",
                foreground="green"
            ).pack(pady=(6, 0))

        # center the modal
        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (win.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{x}+{y}")
        self.wait_window(win)

    def _do_login_for_role(self, role_val: str, key: str, pw: str):
        if not key or not pw:
            messagebox.showwarning("Missing", "Please enter your email/username and password.")
            return

        try:
            # Centralized authentication (email OR full name; password)
            user = authenticate_user(key, pw)
            if not user:
                messagebox.showerror("Login Failed", "Invalid credentials.")
                return

            # Ensure selected role matches the user's stored role
            u_role_val = getattr(getattr(user, "role", None), "value", getattr(user, "role", None))
            if u_role_val != role_val:
                messagebox.showerror(
                    "Wrong Portal",
                    f"This account is registered as '{u_role_val}'. "
                    f"Please choose 'Log in as {u_role_val.title()}'"
                )
                return

        except Exception as e:
            messagebox.showerror("Login Failed", f"{e}")
            return

        # Staff check-in BEFORE handing off to app (only staff roles)
        try:
            if role_val in {
                Role.doctor.value, Role.receptionist.value, Role.admin.value,
                Role.pharmacist.value, Role.support.value, Role.finance.value
            }:
                self._prompt_staff_checkin(user, role_val)
        except Exception as e:
            print(f"[Login] Staff check-in prompt error: {e}")

        # Hand off to app controller (continues to the role's dashboard)
        self.controller.set_user(user)

    # ==========================================================
    # REGISTER (role-first)
    # ==========================================================
    def _build_register_forms(self):
        """Build registration pages per role; each includes common and role-specific fields."""
        for role_val in ALL_ROLES:
            f = ttk.Frame(self.reg_stack)
            self._reg_forms[role_val] = f

            head = ttk.Frame(f)
            head.pack(fill="x")
            ttk.Label(head, text=f"Register as {role_val.title()}",
                      font=("Segoe UI", 12, "bold")).pack(side="left")
            ttk.Button(head, text="Change role", command=self._back_to_register_role_picker).pack(side="right")

            # --- Common account fields ---
            common = ttk.LabelFrame(f, text="Account", padding=12)
            common.pack(fill="x", pady=(8, 8))

            ttk.Label(common, text="Full Name").grid(row=0, column=0, sticky="w")
            r_name = ttk.Entry(common, width=40); r_name.grid(row=1, column=0, pady=2, sticky="w")

            ttk.Label(common, text="Email").grid(row=2, column=0, sticky="w")
            r_email = ttk.Entry(common, width=40); r_email.grid(row=3, column=0, pady=2, sticky="w")

            ttk.Label(common, text="Phone").grid(row=4, column=0, sticky="w")
            r_phone = ttk.Entry(common, width=40); r_phone.grid(row=5, column=0, pady=2, sticky="w")

            # Address (stored to role-specific model; not to User)
            ttk.Label(common, text="Address").grid(row=6, column=0, sticky="w")
            r_address = ttk.Entry(common, width=50); r_address.grid(row=7, column=0, pady=2, sticky="w")

            ttk.Label(common, text="Password").grid(row=8, column=0, sticky="w")
            r_pw = ttk.Entry(common, width=40, show="*"); r_pw.grid(row=9, column=0, pady=2, sticky="w")

            ttk.Label(common, text="Confirm Password").grid(row=10, column=0, sticky="w")
            r_pw2 = ttk.Entry(common, width=40, show="*"); r_pw2.grid(row=11, column=0, pady=2, sticky="w")

            # Show/hide toggle for password fields
            show_pw = tk.BooleanVar(value=False)
            def _toggle_pw():
                char = "" if show_pw.get() else "*"
                r_pw.configure(show=char); r_pw2.configure(show=char)
            ttk.Checkbutton(common, text="Show passwords", variable=show_pw, command=_toggle_pw)\
                .grid(row=12, column=0, sticky="w", pady=(4, 0))

            # Themed placeholders for nicer UX
            attach_placeholder(r_name, "Jane Test")
            attach_placeholder(r_email, "name@example.com")
            attach_placeholder(r_phone, "+61 4xx xxx xxx")
            attach_placeholder(r_address, "Street, Suburb, State, Postcode")

            self._reg_common[role_val] = dict(
                name=r_name, email=r_email, phone=r_phone, address=r_address, pw=r_pw, pw2=r_pw2
            )

            # --- Invite for privileged roles (value validated server-side in register_user) ---
            invite_frame = ttk.Frame(f); invite_frame.pack(fill="x")
            if role_val != Role.patient.value:
                ttk.Label(invite_frame, text="Admin Invite Code").grid(row=0, column=0, sticky="w", pady=(0, 2))
                inv = ttk.Entry(invite_frame, width=40, show="*"); inv.grid(row=1, column=0, pady=(0, 8), sticky="w")
            else:
                inv = None
            setattr(self, f"_inv_{role_val}", inv)

            # --- Role-specific fields ---
            role_box = ttk.LabelFrame(f, text="Role Details", padding=12)
            role_box.pack(fill="x", pady=(0, 8))
            self._build_role_specific_fields(role_val, role_box)

            # --- Create button ---
            btn = ttk.Button(f, text="Create Account", command=lambda rv=role_val: self._do_register_for_role(rv))
            btn.pack(fill="x", pady=(8, 0))

            # Enter to submit from the last common field
            r_pw2.bind("<Return>", lambda _e, rv=role_val: self._do_register_for_role(rv))

    def _back_to_register_role_picker(self):
        for fr in self._reg_forms.values():
            fr.forget()
        self._reg_role_picker.pack()

    def _show_register_form_for_role(self, role_val: str):
        self._reg_role_picker.forget()
        for fr in self._reg_forms.values():
            fr.forget()
        self._reg_forms[role_val].pack(fill="both", expand=True)
        # Autofocus first field
        try:
            c = self._reg_common[role_val]["name"]; c.focus_set()
        except Exception:
            pass

    # ---------- role-specific registration fields ----------
    def _build_role_specific_fields(self, role_val: str, box: ttk.LabelFrame):
        # PATIENT
        if role_val == Role.patient.value:
            self.p_dob = ttk.Entry(box, width=18)
            self.p_gender = ttk.Combobox(box, values=["Male", "Female", "Other"], state="readonly", width=16)
            self.p_gender.set("")
            self.p_mrn = ttk.Entry(box, width=24); self.p_ins = ttk.Entry(box, width=24)
            self.p_em_name = ttk.Entry(box, width=30); self.p_em_phone = ttk.Entry(box, width=20)
            self.p_allerg = ttk.Entry(box, width=50); self.p_chronic = ttk.Entry(box, width=50)

            r = 0
            ttk.Label(box, text="DOB (YYYY-MM-DD)").grid(row=r, column=0, sticky="w"); self.p_dob.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Gender").grid(row=r, column=0, sticky="w"); self.p_gender.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="MRN").grid(row=r, column=0, sticky="w"); self.p_mrn.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Insurance No").grid(row=r, column=0, sticky="w"); self.p_ins.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Emergency Contact (Name / Phone)").grid(row=r, column=0, sticky="w")
            wrap = ttk.Frame(box); wrap.grid(row=r, column=1, sticky="w", padx=6, pady=2)
            self.p_em_name.pack(in_=wrap, side="left"); ttk.Label(wrap, text=" / ").pack(side="left"); self.p_em_phone.pack(in_=wrap, side="left"); r += 1
            ttk.Label(box, text="Allergies").grid(row=r, column=0, sticky="w"); self.p_allerg.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Chronic Conditions").grid(row=r, column=0, sticky="w"); self.p_chronic.grid(row=r, column=1, padx=6, pady=2)

        # DOCTOR
        elif role_val == Role.doctor.value:
            self.d_license = ttk.Entry(box, width=24); self.d_spec = ttk.Entry(box, width=24)
            self.d_title = ttk.Entry(box, width=24); self.d_years = ttk.Entry(box, width=6)
            self.d_empid = ttk.Entry(box, width=20)
            self.d_degree = ttk.Entry(box, width=24); self.d_univ = ttk.Entry(box, width=24)
            self.d_certs = ttk.Entry(box, width=50); self.d_workaddr = ttk.Entry(box, width=50)

            r = 0
            ttk.Label(box, text="License No").grid(row=r, column=0, sticky="w"); self.d_license.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Specialty/Dept").grid(row=r, column=0, sticky="w"); self.d_spec.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Designation").grid(row=r, column=0, sticky="w"); self.d_title.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Years Experience").grid(row=r, column=0, sticky="w"); self.d_years.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Employee ID").grid(row=r, column=0, sticky="w"); self.d_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Degree / University").grid(row=r, column=0, sticky="w")
            wrap = ttk.Frame(box); wrap.grid(row=r, column=1, sticky="w", padx=6, pady=2)
            self.d_degree.pack(in_=wrap, side="left"); ttk.Label(wrap, text=" / ").pack(side="left"); self.d_univ.pack(in_=wrap, side="left"); r += 1
            ttk.Label(box, text="Certifications").grid(row=r, column=0, sticky="w"); self.d_certs.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Work Address").grid(row=r, column=0, sticky="w"); self.d_workaddr.grid(row=r, column=1, padx=6, pady=2)

        # RECEPTIONIST
        elif role_val == Role.receptionist.value:
            self.rc_empid = ttk.Entry(box, width=20); self.rc_desig = ttk.Entry(box, width=24)
            self.rc_dept = ttk.Entry(box, width=24); self.rc_shift = ttk.Combobox(box, values=["Morning", "Evening", "Night"], state="readonly", width=16)
            self.rc_loc = ttk.Entry(box, width=24); self.rc_sup = ttk.Entry(box, width=24)
            self.rc_shift.set("Morning")

            r = 0
            ttk.Label(box, text="Employee ID").grid(row=r, column=0, sticky="w"); self.rc_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Designation").grid(row=r, column=0, sticky="w"); self.rc_desig.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Department").grid(row=r, column=0, sticky="w"); self.rc_dept.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Shift").grid(row=r, column=0, sticky="w"); self.rc_shift.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Work Location").grid(row=r, column=0, sticky="w"); self.rc_loc.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Supervisor").grid(row=r, column=0, sticky="w"); self.rc_sup.grid(row=r, column=1, padx=6, pady=2)

        # ADMIN
        elif role_val == Role.admin.value:
            self.a_empid = ttk.Entry(box, width=20); self.a_dept = ttk.Entry(box, width=24)
            self.a_title = ttk.Entry(box, width=24)
            self.a_level = ttk.Combobox(box, values=[a.value for a in AdminLevel], state="readonly", width=24)
            self.a_level.set(AdminLevel.user_admin.value)

            r = 0
            ttk.Label(box, text="Employee ID").grid(row=r, column=0, sticky="w"); self.a_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Department").grid(row=r, column=0, sticky="w"); self.a_dept.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Job Title").grid(row=r, column=0, sticky="w"); self.a_title.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Admin Level").grid(row=r, column=0, sticky="w"); self.a_level.grid(row=r, column=1, padx=6, pady=2)

        # PHARMACIST
        elif role_val == Role.pharmacist.value:
            self.ph_empid = ttk.Entry(box, width=20)
            self.ph_license = ttk.Entry(box, width=24)
            self.ph_dept = ttk.Entry(box, width=24)
            r = 0
            ttk.Label(box, text="Employee ID").grid(row=r, column=0, sticky="w"); self.ph_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="License No").grid(row=r, column=0, sticky="w"); self.ph_license.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Department").grid(row=r, column=0, sticky="w"); self.ph_dept.grid(row=r, column=1, padx=6, pady=2)

        # SUPPORT
        elif role_val == Role.support.value:
            self.su_empid = ttk.Entry(box, width=20)
            self.su_team = ttk.Entry(box, width=24)
            r = 0
            ttk.Label(box, text="Employee ID").grid(row=r, column=0, sticky="w"); self.su_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Team").grid(row=r, column=0, sticky="w"); self.su_team.grid(row=r, column=1, padx=6, pady=2)

        # FINANCE
        elif role_val == Role.finance.value:
            self.fi_empid = ttk.Entry(box, width=20)
            self.fi_title = ttk.Entry(box, width=24)
            r = 0
            ttk.Label(box, text="Employee ID").grid(row=r, column=0, sticky="w"); self.fi_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
            ttk.Label(box, text="Title").grid(row=r, column=0, sticky="w"); self.fi_title.grid(row=r, column=1, padx=6, pady=2)

    # ---------- register actions ----------
    @staticmethod
    def _password_ok(pw: str) -> bool:
        """Minimum 8 chars with at least one letter and one digit."""
        return bool(len(pw) >= 8 and re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw))

    def _do_register_for_role(self, role_val: str):
        # Common fields
        c = self._reg_common[role_val]
        name = c["name"].get().strip()
        email = c["email"].get().strip().lower()
        phone = c["phone"].get().strip()
        addr = c["address"].get().strip()
        pw = c["pw"].get()
        pw2 = c["pw2"].get()

        if not name or not email or not pw or not pw2:
            messagebox.showwarning("Missing", "Please fill all required fields.")
            return
        if not EMAIL_RE.match(email):
            messagebox.showerror("Invalid Email", "Please enter a valid email address.")
            return
        if pw != pw2:
            messagebox.showerror("Mismatch", "Passwords do not match.")
            return
        if not self._password_ok(pw):
            messagebox.showerror("Weak Password", "Password must be at least 8 characters and include letters and numbers.")
            return

        try:
            with SessionLocal() as db:
                # Pull invite from the UI (validated in auth.register_user)
                invite_code = None
                inv_widget = getattr(self, f"_inv_{role_val}")
                if role_val != Role.patient.value and inv_widget is not None:
                    invite_code = inv_widget.get().strip() or None

                # Centralized user creation (email uniqueness, invite validation, hashing)
                user: User = register_user(
                    db=db,
                    email=email,
                    password=pw,
                    full_name=name,
                    phone=phone,
                    role_value=role_val,
                    invite_code=invite_code,
                )
                db.flush()  # ensure user.id is available

                # --- role-specific inserts (same as your previous code) ---
                if role_val == Role.patient.value:
                    dob = None
                    dob_txt = getattr(self, "p_dob").get().strip()
                    if dob_txt:
                        try:
                            dob = datetime.strptime(dob_txt, "%Y-%m-%d").date()
                        except ValueError:
                            messagebox.showerror("Invalid", "DOB must be YYYY-MM-DD.")
                            db.rollback()
                            return

                    db.add(Patient(
                        user_id=user.id,
                        dob=dob,
                        gender=getattr(self, "p_gender").get() or "",
                        mrn=getattr(self, "p_mrn").get().strip(),
                        insurance_no=getattr(self, "p_ins").get().strip(),
                        address=addr,
                        emergency_contact_name=getattr(self, "p_em_name").get().strip(),
                        emergency_contact_phone=getattr(self, "p_em_phone").get().strip(),
                        allergies=getattr(self, "p_allerg").get().strip(),
                        chronic_conditions=getattr(self, "p_chronic").get().strip(),
                    ))

                elif role_val == Role.doctor.value:
                    years_txt = getattr(self, "d_years").get().strip()
                    try:
                        years = int(years_txt) if years_txt else 0
                        if years < 0:
                            raise ValueError()
                    except ValueError:
                        messagebox.showerror("Invalid", "Years of experience must be a non-negative integer.")
                        db.rollback()
                        return

                    work_addr = getattr(self, "d_workaddr").get().strip() or addr
                    db.add(Doctor(
                        user_id=user.id,
                        license_no=getattr(self, "d_license").get().strip(),
                        specialty=getattr(self, "d_spec").get().strip() or "General",
                        designation=getattr(self, "d_title").get().strip(),
                        years_exp=years,
                        employee_id=getattr(self, "d_empid").get().strip(),
                        degree=getattr(self, "d_degree").get().strip(),
                        university=getattr(self, "d_univ").get().strip(),
                        certifications=getattr(self, "d_certs").get().strip(),
                        work_address=work_addr,
                    ))

                elif role_val == Role.receptionist.value:
                    work_loc = getattr(self, "rc_loc").get().strip() or addr
                    db.add(Receptionist(
                        user_id=user.id,
                        employee_id=getattr(self, "rc_empid").get().strip(),
                        designation=getattr(self, "rc_desig").get().strip() or "Receptionist",
                        department=getattr(self, "rc_dept").get().strip() or "OPD",
                        work_shift=getattr(self, "rc_shift").get().strip() or "Morning",
                        work_location=work_loc,
                        supervisor=getattr(self, "rc_sup").get().strip(),
                    ))

                elif role_val == Role.admin.value:
                    try:
                        admin_level = AdminLevel(getattr(self, "a_level").get())
                    except Exception:
                        admin_level = AdminLevel.user_admin

                    db.add(AdminProfile(
                        user_id=user.id,
                        employee_id=getattr(self, "a_empid").get().strip(),
                        department=getattr(self, "a_dept").get().strip() or "IT",
                        title=getattr(self, "a_title").get().strip() or "System Admin",
                        admin_level=admin_level,
                    ))

                elif role_val == Role.pharmacist.value:
                    db.add(Pharmacist(
                        user_id=user.id,
                        employee_id=getattr(self, "ph_empid").get().strip(),
                        license_no=getattr(self, "ph_license").get().strip(),
                        department=getattr(self, "ph_dept").get().strip() or "Pharmacy",
                    ))

                elif role_val == Role.support.value:
                    db.add(SupportAgent(
                        user_id=user.id,
                        employee_id=getattr(self, "su_empid").get().strip(),
                        team=getattr(self, "su_team").get().strip() or "Helpdesk",
                    ))

                elif role_val == Role.finance.value:
                    db.add(FinanceOfficer(
                        user_id=user.id,
                        employee_id=getattr(self, "fi_empid").get().strip(),
                        title=getattr(self, "fi_title").get().strip() or "Accounts",
                    ))

                db.commit()

            messagebox.showinfo("Success", f"{role_val.title()} account created. You can now log in.")
            # Switch to Login tab, pre-select role and prefill email
            self._nb.select(self.t_login)
            self._back_to_login_role_picker()
            self._show_login_form_for_role(role_val)

            # Prefill the email in that role's user field
            try:
                frm = self._login_forms[role_val]
                body = [w for w in frm.winfo_children() if isinstance(w, ttk.Frame)][1]  # the "body" frame
                entries = [w for w in body.winfo_children() if isinstance(w, ttk.Entry)]
                if entries:
                    entries[0].delete(0, "end")
                    entries[0].insert(0, email)
                    entries[0].focus_set()
            except Exception:
                pass

        except SQLAlchemyError as e:
            messagebox.showerror("Database Error", f"Could not create account.\n\nDetails: {e}")
        except ValueError as e:
            # register_user can raise ValueError for invite/email issues
            messagebox.showerror("Registration Error", str(e))

    # ==========================================================
    # Password reset modals (offline demo accepts ANY code)
    # ==========================================================
    def _open_pw_reset_request(self):
        win = tk.Toplevel(self)
        win.title("Password reset — request")
        win.grab_set(); win.resizable(False, False)

        frm = ttk.Frame(win, padding=12); frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Enter your email or full name").grid(row=0, column=0, sticky="w")
        ent = ttk.Entry(frm, width=40); ent.grid(row=1, column=0, pady=(2, 8), sticky="w")

        out = tk.StringVar(value="")
        msg = ttk.Label(frm, textvariable=out, wraplength=360, justify="left")
        msg.grid(row=2, column=0, sticky="w")

        def do_request():
            key = ent.get().strip()
            if not key:
                out.set("Please enter your email or full name."); return
            try:
                token = create_reset_token_for_user(key)
                out.set(
                    "Reset request created.\n\n"
                    "Your one-time reset code (valid 30 minutes):\n"
                    f"{token}\n\n"
                    "Click “Have a reset code?” on the login screen to apply it."
                )
            except Exception as e:
                out.set(f"Could not create reset request.\n{e}")

        ttk.Button(frm, text="Create reset request", command=do_request).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Button(frm, text="Close", command=win.destroy).grid(row=4, column=0, sticky="e", pady=(8, 0))

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (win.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{x}+{y}")

    def _open_pw_reset_apply(self):
        win = tk.Toplevel(self)
        win.title("Password reset — apply")
        win.grab_set(); win.resizable(False, False)

        frm = ttk.Frame(win, padding=12); frm.pack(fill="both", expand=True)

        # User key field (needed in offline demo mode)
        ttk.Label(frm, text="Email or Full Name").grid(row=0, column=0, sticky="w")
        key_ent = ttk.Entry(frm, width=46); key_ent.grid(row=1, column=0, pady=(2, 8), sticky="w")

        ttk.Label(frm, text="Reset code (any code works in offline mode)").grid(row=2, column=0, sticky="w")
        tok = ttk.Entry(frm, width=46); tok.grid(row=3, column=0, pady=(2, 8), sticky="w")

        ttk.Label(frm, text="New password").grid(row=4, column=0, sticky="w")
        pw1 = ttk.Entry(frm, width=40, show="*"); pw1.grid(row=5, column=0, pady=(2, 8), sticky="w")

        ttk.Label(frm, text="Confirm new password").grid(row=6, column=0, sticky="w")
        pw2 = ttk.Entry(frm, width=40, show="*"); pw2.grid(row=7, column=0, pady=(2, 8), sticky="w")

        out = tk.StringVar(value="")
        msg = ttk.Label(frm, textvariable=out, wraplength=380, justify="left")
        msg.grid(row=8, column=0, sticky="w")

        def do_apply():
            user_key = key_ent.get().strip()
            t = tok.get().strip()
            p1 = pw1.get(); p2 = pw2.get()
            if not user_key or not t or not p1 or not p2:
                out.set("Please fill all fields."); return
            if p1 != p2:
                out.set("Passwords do not match."); return
            if not self._password_ok(p1):
                out.set("Password must be at least 8 characters and include letters and numbers."); return
            try:
                # In offline demo mode, ANY code is accepted; user_key selects the account
                apply_reset_with_token(t, p1, user_key=user_key)
                out.set("Password updated. You can now log in with your new password.")
            except Exception as e:
                out.set(f"Could not apply reset.\n{e}")

        ttk.Button(frm, text="Apply reset", command=do_apply).grid(row=9, column=0, sticky="w", pady=(8, 0))
        ttk.Button(frm, text="Close", command=win.destroy).grid(row=10, column=0, sticky="e", pady=(8, 0))

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (win.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (win.winfo_height() // 2)
        win.geometry(f"+{x}+{y}")
