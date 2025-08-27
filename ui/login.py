# care_portal/ui/login.py
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from sqlalchemy import select, or_, func
from ..db import SessionLocal
from ..models import (
    User, Role, Patient, Doctor, Receptionist, AdminProfile, AdminLevel
)
from ..auth import hash_password, verify_password
from .base import BaseFrame

# Change this for privileged registrations
INVITE_CODE = "CARE-ADMIN-123"


class LoginFrame(BaseFrame):
    title = "Care Portal — Login"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        nb = ttk.Notebook(self)
        nb.pack(expand=True, fill="both")

        # =========================
        # Log In tab
        # =========================
        t_login = ttk.Frame(nb)
        nb.add(t_login, text="Log In")

        lf = ttk.Frame(t_login, padding=20)
        lf.pack()

        self.email_or_user_in = ttk.Entry(lf, width=40)
        self.pw_in = ttk.Entry(lf, width=40, show="*")
        self.show_pw = tk.BooleanVar(value=False)

        ttk.Label(lf, text="Email or Username (Full Name)").grid(row=0, column=0, sticky="w")
        self.email_or_user_in.grid(row=1, column=0, pady=4)

        ttk.Label(lf, text="Password").grid(row=2, column=0, sticky="w")
        self.pw_in.grid(row=3, column=0, pady=4)

        showpw_chk = ttk.Checkbutton(
            lf, text="Show password", variable=self.show_pw,
            command=lambda: self.pw_in.configure(show="" if self.show_pw.get() else "*")
        )
        showpw_chk.grid(row=4, column=0, sticky="w", pady=(0, 6))

        ttk.Button(lf, text="Log In", command=self.do_login)\
            .grid(row=5, column=0, pady=10, sticky="ew")

        # =========================
        # Register tab (role-aware)
        # =========================
        t_reg = ttk.Frame(nb)
        nb.add(t_reg, text="Register (Any Role)")

        rf = ttk.Frame(t_reg, padding=16)
        rf.pack(fill="x")

        # Common account section
        ttk.Label(rf, text="Full Name").grid(row=0, column=0, sticky="w")
        self.r_name = ttk.Entry(rf, width=40); self.r_name.grid(row=1, column=0, pady=2)

        ttk.Label(rf, text="Email").grid(row=2, column=0, sticky="w")
        self.r_email = ttk.Entry(rf, width=40); self.r_email.grid(row=3, column=0, pady=2)

        ttk.Label(rf, text="Phone").grid(row=4, column=0, sticky="w")
        self.r_phone = ttk.Entry(rf, width=40); self.r_phone.grid(row=5, column=0, pady=2)

        ttk.Label(rf, text="Password").grid(row=6, column=0, sticky="w")
        self.r_pw = ttk.Entry(rf, width=40, show="*"); self.r_pw.grid(row=7, column=0, pady=2)

        ttk.Label(rf, text="Confirm Password").grid(row=8, column=0, sticky="w")
        self.r_pw2 = ttk.Entry(rf, width=40, show="*"); self.r_pw2.grid(row=9, column=0, pady=2)

        ttk.Label(rf, text="Role").grid(row=10, column=0, sticky="w")
        self.role_cmb = ttk.Combobox(
            rf, state="readonly", width=38, values=[r.value for r in Role]
        )
        self.role_cmb.grid(row=11, column=0, pady=4, sticky="ew")
        self.role_cmb.set(Role.patient.value)

        # Invite code (only for non-patient)
        self.invite_lbl = ttk.Label(
            rf, text="Admin Invite Code (required for Doctor/Receptionist/Admin)"
        )
        self.invite_in = ttk.Entry(rf, width=40, show="*")
        self.invite_lbl.grid(row=12, column=0, sticky="w", pady=(8, 0))
        self.invite_in.grid(row=13, column=0, pady=2, sticky="ew")

        # Container for dynamic role fields
        self.role_fields = ttk.LabelFrame(t_reg, text="Role Details", padding=12)
        self.role_fields.pack(fill="x", padx=16, pady=(0, 12))
        self._build_role_forms()

        ttk.Button(t_reg, text="Create Account", command=self.do_register)\
            .pack(padx=16, pady=8, fill="x")

        # Toggle invite visibility + form swap
        def on_role_change(*_):
            v = self.role_cmb.get()
            needs = v != Role.patient.value
            state = "normal" if needs else "disabled"
            self.invite_in.configure(state=state)
            self._show_role_form(v)
        self.role_cmb.bind("<<ComboboxSelected>>", on_role_change)
        on_role_change()

    # ---------- role forms ----------
    def _build_role_forms(self):
        self.forms = {}

        # Patient form
        f = ttk.Frame(self.role_fields); self.forms[Role.patient.value] = f
        self.p_dob = ttk.Entry(f, width=18)
        self.p_gender = ttk.Combobox(f, values=["Male", "Female", "Other"], width=16, state="readonly")
        self.p_mrn = ttk.Entry(f, width=24); self.p_ins = ttk.Entry(f, width=24)
        self.p_addr = ttk.Entry(f, width=50)
        self.p_em_name = ttk.Entry(f, width=30); self.p_em_phone = ttk.Entry(f, width=20)
        self.p_allerg = ttk.Entry(f, width=50); self.p_chronic = ttk.Entry(f, width=50)

        r = 0
        ttk.Label(f, text="DOB (YYYY-MM-DD)").grid(row=r, column=0, sticky="w")
        self.p_dob.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Gender").grid(row=r, column=0, sticky="w")
        self.p_gender.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="MRN").grid(row=r, column=0, sticky="w")
        self.p_mrn.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Insurance No").grid(row=r, column=0, sticky="w")
        self.p_ins.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Address").grid(row=r, column=0, sticky="w")
        self.p_addr.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Emergency Contact (Name / Phone)").grid(row=r, column=0, sticky="w")
        wrap = ttk.Frame(f); wrap.grid(row=r, column=1, sticky="w", padx=6, pady=2)
        self.p_em_name.pack(in_=wrap, side="left"); ttk.Label(wrap, text=" / ").pack(side="left"); self.p_em_phone.pack(in_=wrap, side="left"); r += 1
        ttk.Label(f, text="Allergies").grid(row=r, column=0, sticky="w")
        self.p_allerg.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Chronic Conditions").grid(row=r, column=0, sticky="w")
        self.p_chronic.grid(row=r, column=1, padx=6, pady=2)

        # Doctor form
        f = ttk.Frame(self.role_fields); self.forms[Role.doctor.value] = f
        self.d_license = ttk.Entry(f, width=24); self.d_spec = ttk.Entry(f, width=24)
        self.d_title = ttk.Entry(f, width=24); self.d_years = ttk.Entry(f, width=6)
        self.d_empid = ttk.Entry(f, width=20)
        self.d_degree = ttk.Entry(f, width=24); self.d_univ = ttk.Entry(f, width=24)
        self.d_certs = ttk.Entry(f, width=50); self.d_workaddr = ttk.Entry(f, width=50)

        r = 0
        ttk.Label(f, text="License No").grid(row=r, column=0, sticky="w"); self.d_license.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Specialty/Dept").grid(row=r, column=0, sticky="w"); self.d_spec.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Designation").grid(row=r, column=0, sticky="w"); self.d_title.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Years Experience").grid(row=r, column=0, sticky="w"); self.d_years.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Employee ID").grid(row=r, column=0, sticky="w"); self.d_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Degree / University").grid(row=r, column=0, sticky="w")
        wrap = ttk.Frame(f); wrap.grid(row=r, column=1, sticky="w", padx=6, pady=2)
        self.d_degree.pack(in_=wrap, side="left"); ttk.Label(wrap, text=" / ").pack(side="left"); self.d_univ.pack(in_=wrap, side="left"); r += 1
        ttk.Label(f, text="Certifications").grid(row=r, column=0, sticky="w"); self.d_certs.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Work Address").grid(row=r, column=0, sticky="w"); self.d_workaddr.grid(row=r, column=1, padx=6, pady=2)

        # Receptionist form
        f = ttk.Frame(self.role_fields); self.forms[Role.receptionist.value] = f
        self.rc_empid = ttk.Entry(f, width=20); self.rc_desig = ttk.Entry(f, width=24)
        self.rc_dept = ttk.Entry(f, width=24); self.rc_shift = ttk.Combobox(f, values=["Morning", "Evening", "Night"], state="readonly", width=16)
        self.rc_loc = ttk.Entry(f, width=24); self.rc_sup = ttk.Entry(f, width=24); self.rc_shift.set("Morning")

        r = 0
        ttk.Label(f, text="Employee ID").grid(row=r, column=0, sticky="w"); self.rc_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Designation").grid(row=r, column=0, sticky="w"); self.rc_desig.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Department").grid(row=r, column=0, sticky="w"); self.rc_dept.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Shift").grid(row=r, column=0, sticky="w"); self.rc_shift.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Work Location").grid(row=r, column=0, sticky="w"); self.rc_loc.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Supervisor").grid(row=r, column=0, sticky="w"); self.rc_sup.grid(row=r, column=1, padx=6, pady=2)

        # Admin form
        f = ttk.Frame(self.role_fields); self.forms[Role.admin.value] = f
        self.a_empid = ttk.Entry(f, width=20); self.a_dept = ttk.Entry(f, width=24)
        self.a_title = ttk.Entry(f, width=24)
        self.a_level = ttk.Combobox(f, values=[a.value for a in AdminLevel], state="readonly", width=24)
        self.a_level.set(AdminLevel.user_admin.value)

        r = 0
        ttk.Label(f, text="Employee ID").grid(row=r, column=0, sticky="w"); self.a_empid.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Department").grid(row=r, column=0, sticky="w"); self.a_dept.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Job Title").grid(row=r, column=0, sticky="w"); self.a_title.grid(row=r, column=1, padx=6, pady=2); r += 1
        ttk.Label(f, text="Admin Level").grid(row=r, column=0, sticky="w"); self.a_level.grid(row=r, column=1, padx=6, pady=2)

    def _show_role_form(self, role_value: str):
        for w in self.role_fields.winfo_children():
            w.pack_forget()
        self.forms[role_value].pack(fill="x")

    # ---------- actions ----------
    def do_login(self):
        key = self.email_or_user_in.get().strip()
        pw = self.pw_in.get()
        if not key or not pw:
            messagebox.showwarning("Missing", "Please enter your email/username and password.")
            return

        # Login by email OR full_name (username)
        with SessionLocal() as db:
            user = db.scalar(
                select(User).where(
                    or_(
                        func.lower(User.email) == key.lower(),
                        func.lower(User.full_name) == key.lower()
                    )
                )
            )
            if not user or not verify_password(pw, user.password_hash):
                messagebox.showerror("Login Failed", "Invalid credentials.")
                return

        self.controller.set_user(user)

    def do_register(self):
        # common fields
        name = self.r_name.get().strip()
        email = self.r_email.get().strip().lower()
        phone = self.r_phone.get().strip()
        pw = self.r_pw.get()
        pw2 = self.r_pw2.get()
        role = Role(self.role_cmb.get())

        if not name or not email or not pw or not pw2:
            messagebox.showwarning("Missing", "Please fill all required fields.")
            return
        if pw != pw2:
            messagebox.showerror("Mismatch", "Passwords do not match.")
            return
        if role != Role.patient and self.invite_in.get().strip() != INVITE_CODE:
            messagebox.showerror("Unauthorized", "Invalid invite code for privileged role.")
            return

        with SessionLocal() as db:
            if db.scalar(select(User).where(User.email == email)):
                messagebox.showerror("Error", "Email already registered.")
                return

            user = User(email=email, full_name=name, phone=phone,
                        role=role, password_hash=hash_password(pw))
            db.add(user); db.flush()

            if role == Role.patient:
                dob = None
                if self.p_dob.get().strip():
                    try:
                        dob = datetime.strptime(self.p_dob.get().strip(), "%Y-%m-%d").date()
                    except ValueError:
                        messagebox.showerror("Invalid", "DOB must be YYYY-MM-DD")
                        return
                db.add(Patient(
                    user_id=user.id,
                    dob=dob,
                    gender=(self.p_gender.get() or ""),
                    mrn=self.p_mrn.get().strip(),
                    insurance_no=self.p_ins.get().strip(),
                    address=self.p_addr.get().strip(),
                    emergency_contact_name=self.p_em_name.get().strip(),
                    emergency_contact_phone=self.p_em_phone.get().strip(),
                    allergies=self.p_allerg.get().strip(),
                    chronic_conditions=self.p_chronic.get().strip(),
                ))
            elif role == Role.doctor:
                years = int(self.d_years.get() or 0)
                db.add(Doctor(
                    user_id=user.id,
                    license_no=self.d_license.get().strip(),
                    specialty=self.d_spec.get().strip() or "General",
                    designation=self.d_title.get().strip(),
                    years_exp=years,
                    employee_id=self.d_empid.get().strip(),
                    degree=self.d_degree.get().strip(),
                    university=self.d_univ.get().strip(),
                    certifications=self.d_certs.get().strip(),
                    work_address=self.d_workaddr.get().strip(),
                ))
            elif role == Role.receptionist:
                db.add(Receptionist(
                    user_id=user.id,
                    employee_id=self.rc_empid.get().strip(),
                    designation=self.rc_desig.get().strip() or "Receptionist",
                    department=self.rc_dept.get().strip() or "OPD",
                    work_shift=self.rc_shift.get().strip() or "Morning",
                    work_location=self.rc_loc.get().strip(),
                    supervisor=self.rc_sup.get().strip(),
                ))
            elif role == Role.admin:
                db.add(AdminProfile(
                    user_id=user.id,
                    employee_id=self.a_empid.get().strip(),
                    department=self.a_dept.get().strip() or "IT",
                    title=self.a_title.get().strip() or "System Admin",
                    admin_level=AdminLevel(self.a_level.get()),
                ))

            db.commit()

        messagebox.showinfo("Success", f"{role.value.title()} account created. You can now log in.")
