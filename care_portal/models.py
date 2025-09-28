# care_portal/models.py
from __future__ import annotations

import enum
from datetime import datetime, date

# SQLAlchemy
from sqlalchemy import (
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Enum,
    Date,
    Numeric,
    Boolean,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from .db import Base

# -------------------- Staff Check-ins (NEW) --------------------
class StaffCheckinStatus(enum.Enum):
    checked_in = "checked_in"
    checked_out = "checked_out"   # actual checkout
    skipped = "skipped"           # user explicitly chose to skip
    auto = "auto"                 # future use (e.g., geofence, kiosk)


class StaffCheckinMethod(enum.Enum):
    login = "login"               # from login popup
    manual = "manual"             # receptionist/admin marked it
    remote = "remote"             # user indicates offsite/remote
    kiosk = "kiosk"               # future hardware terminal


class StaffCheckin(Base):
    __tablename__ = "staff_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)  # store Role.value
    status: Mapped[StaffCheckinStatus] = mapped_column(Enum(StaffCheckinStatus), nullable=False)
    method: Mapped[StaffCheckinMethod] = mapped_column(Enum(StaffCheckinMethod), nullable=False, default=StaffCheckinMethod.login)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)   # e.g., "Onsite", "Offsite", etc.

    user = relationship("User", backref="staff_checkins")


# Helpful day-based index for quick “today” lookups
Index("ix_staff_checkins_user_ts", StaffCheckin.user_id, StaffCheckin.ts)

# -------------------- Core roles --------------------
class Role(str, enum.Enum):
    patient = "patient"
    doctor = "doctor"
    admin = "admin"
    receptionist = "receptionist"
    pharmacist = "pharmacist"     # NEW
    support = "support"           # NEW
    finance = "finance"           # NEW


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # One-to-many
    tickets: Mapped[list["SupportTicket"]] = relationship(
        "SupportTicket",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="SupportTicket.user_id",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )

# -------------------- Invite Codes (NEW) --------------------
class InviteCode(Base):
    """
    Optional table used by registration to gate staff roles.
    - role_allowed: if set, restricts the code to a specific role value (e.g., 'admin').
    - used_by: once consumed, stores the user id that used it.
    """
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)

    # Optional scoping & lifecycle
    role_allowed: Mapped[str | None] = mapped_column(String(50), nullable=True)  # matches Role values, lowercase
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    disabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Consumption
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    used_by_user = relationship("User", foreign_keys=[used_by])

Index("ix_invite_role_exp", InviteCode.role_allowed, InviteCode.expires_at)

# -------------------- Patient --------------------
class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str] = mapped_column(String(16), default="")
    mrn: Mapped[str] = mapped_column(String(64), default="")
    insurance_no: Mapped[str] = mapped_column(String(64), default="")

    address: Mapped[str] = mapped_column(Text, default="")
    emergency_contact_name: Mapped[str] = mapped_column(String(128), default="")
    emergency_contact_phone: Mapped[str] = mapped_column(String(32), default="")

    allergies: Mapped[str] = mapped_column(Text, default="")
    chronic_conditions: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship("User", back_populates="patient")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="patient")

    # UI relations
    prescriptions: Mapped[list["Prescription"]] = relationship(
        "Prescription", back_populates="patient", cascade="all, delete-orphan"
    )
    disciplinary_records: Mapped[list["DisciplinaryRecord"]] = relationship(
        "DisciplinaryRecord", back_populates="patient", cascade="all, delete-orphan"
    )

# -------------------- Doctor --------------------
class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    license_no: Mapped[str] = mapped_column(String(64), default="")
    specialty: Mapped[str] = mapped_column(String(128), default="General")
    designation: Mapped[str] = mapped_column(String(128), default="")
    years_exp: Mapped[int] = mapped_column(Integer, default=0)
    employee_id: Mapped[str] = mapped_column(String(64), default="")
    degree: Mapped[str] = mapped_column(String(128), default="")
    university: Mapped[str] = mapped_column(String(128), default="")
    certifications: Mapped[str] = mapped_column(Text, default="")
    work_address: Mapped[str] = mapped_column(Text, default="")

    user: Mapped["User"] = relationship("User", back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="doctor")
    prescriptions: Mapped[list["Prescription"]] = relationship("Prescription", back_populates="doctor")

# -------------------- Receptionist --------------------
class Receptionist(Base):
    __tablename__ = "receptionists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    employee_id: Mapped[str] = mapped_column(String(64), default="")
    designation: Mapped[str] = mapped_column(String(128), default="Receptionist")
    department: Mapped[str] = mapped_column(String(128), default="OPD")
    work_shift: Mapped[str] = mapped_column(String(32), default="Morning")
    work_location: Mapped[str] = mapped_column(String(128), default="")
    supervisor: Mapped[str] = mapped_column(String(128), default="")

    user: Mapped["User"] = relationship("User", back_populates="receptionist")

# -------------------- NEW stakeholder profiles --------------------
class AdminLevel(str, enum.Enum):
    super_admin = "Super Admin"
    user_admin = "User Admin"
    ops_admin = "Ops Admin"
    audit_admin = "Audit Admin"


class AdminProfile(Base):
    __tablename__ = "admin_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    employee_id: Mapped[str] = mapped_column(String(64), default="")
    department: Mapped[str] = mapped_column(String(128), default="IT")
    title: Mapped[str] = mapped_column(String(128), default="System Admin")
    admin_level: Mapped[AdminLevel] = mapped_column(Enum(AdminLevel), default=AdminLevel.user_admin)

    user: Mapped["User"] = relationship("User", back_populates="admin_profile")


class Pharmacist(Base):
    __tablename__ = "pharmacists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    employee_id: Mapped[str] = mapped_column(String(64), default="")
    license_no: Mapped[str] = mapped_column(String(64), default="")
    department: Mapped[str] = mapped_column(String(128), default="Pharmacy")

    user: Mapped["User"] = relationship("User", back_populates="pharmacist_profile")


class SupportAgent(Base):
    __tablename__ = "support_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    employee_id: Mapped[str] = mapped_column(String(64), default="")
    team: Mapped[str] = mapped_column(String(128), default="Helpdesk")

    user: Mapped["User"] = relationship("User", back_populates="support_profile")


class FinanceOfficer(Base):
    __tablename__ = "finance_officers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    employee_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(128), default="Accounts")

    user: Mapped["User"] = relationship("User", back_populates="finance_profile")

# -------------------- Appointments & extras --------------------
class AppointmentStatus(str, enum.Enum):
    requested = "requested"   # for request flow
    booked = "booked"
    cancelled = "cancelled"
    completed = "completed"


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.booked
    )

    patient: Mapped["Patient"] = relationship("Patient", back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="appointments")

    # ---- Backward-compat: many UI queries use Appointment.datetime ----
    datetime = synonym("scheduled_for")  # type: ignore[attr-defined]

# Unique (doctor, exact datetime) to guarantee an exclusive slot
Index("uq_appt_doctor_datetime", Appointment.doctor_id, Appointment.scheduled_for, unique=True)
# Range-friendly indexes
Index("ix_appt_doctor_dt", Appointment.doctor_id, Appointment.scheduled_for)
Index("ix_appt_patient_dt", Appointment.patient_id, Appointment.scheduled_for)

class AttendanceMethod(str, enum.Enum):
    web = "web"
    simulated_rfid = "simulated_rfid"
    simulated_biometric = "simulated_biometric"


class Attendance(Base):
    __tablename__ = "attendance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    checkin_method: Mapped[AttendanceMethod] = mapped_column(Enum(AttendanceMethod), default=AttendanceMethod.web)
    checkin_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

Index("ix_attendance_appt", Attendance.appointment_id)

# -------------------- Prescriptions (expanded for UI) --------------------
class Prescription(Base):
    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Linkages
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"), nullable=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), nullable=True)

    # Prescription info
    title: Mapped[str] = mapped_column(String(255), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    medication: Mapped[str] = mapped_column(String(255), default="")
    dosage: Mapped[str] = mapped_column(String(128), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    repeats: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")  # legacy/general blob

    # --- New fields required by Pharmacist UI ---
    is_dispensed: Mapped[bool] = mapped_column(Boolean, default=False)
    dispensed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    patient: Mapped["Patient"] = relationship("Patient", back_populates="prescriptions")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="prescriptions")
    appointment: Mapped["Appointment"] = relationship("Appointment")

Index("ix_rx_open", Prescription.is_dispensed, Prescription.created_at)
Index("ix_rx_patient", Prescription.patient_id)

# -------------------- Support Tickets --------------------
class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"   # NEW
    resolved = "resolved"         # NEW
    closed = "closed"


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # For Support dashboard
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # NEW
    notes: Mapped[str] = mapped_column(Text, default="")                                     # NEW
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)             # NEW

    # Disambiguate both relationships to User:
    user: Mapped["User"] = relationship(
        "User",
        back_populates="tickets",
        foreign_keys=[user_id],
    )
    assignee: Mapped["User"] = relationship(
        "User",
        foreign_keys=[assignee_id],
    )

Index("ix_ticket_status_time", SupportTicket.status, SupportTicket.created_at)
Index("ix_ticket_assignee", SupportTicket.assignee_id, SupportTicket.status)

# -------------------- Doctor Availability --------------------
class DoctorAvailability(Base):
    __tablename__ = "doctor_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), nullable=False)
    # date-only availability; store date in DateTime but use date portion
    day: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), default="17:00")    # "HH:MM"
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)

    doctor: Mapped["Doctor"] = relationship("Doctor")

Index("ix_av_doctor_dt", DoctorAvailability.doctor_id, DoctorAvailability.day)

# -------------------- Medical Records --------------------
class RecordAuthor(str, enum.Enum):
    patient = "patient"
    doctor = "doctor"


class MedicalRecord(Base):
    __tablename__ = "medical_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), nullable=False)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    author_role: Mapped[RecordAuthor] = mapped_column(Enum(RecordAuthor), default=RecordAuthor.patient)
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    patient: Mapped["Patient"] = relationship("Patient")
    author: Mapped["User"] = relationship("User")

Index("ix_medrec_patient_time", MedicalRecord.patient_id, MedicalRecord.created_at)

# -------------------- Billing --------------------
class BillingStatus(str, enum.Enum):
    unpaid = "unpaid"
    paid = "paid"
    refunded = "refunded"
    cancelled = "cancelled"


class PaymentMethod(str, enum.Enum):
    cash = "cash"
    card = "card"
    online = "online"
    insurance = "insurance"  # NEW


class Billing(Base):
    __tablename__ = "billing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    status: Mapped[BillingStatus] = mapped_column(Enum(BillingStatus), default=BillingStatus.unpaid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Payment fields
    payment_method: Mapped[PaymentMethod | None] = mapped_column(Enum(PaymentMethod), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    appointment: Mapped["Appointment"] = relationship("Appointment", back_populates="billing_items")

Index("ix_billing_appt", Billing.appointment_id)
Index("ix_billing_status_time", Billing.status, Billing.created_at)

# Backref from Appointment -> Billing
Appointment.billing_items = relationship(
    "Billing", back_populates="appointment", cascade="all, delete-orphan"
)

# -------------------- Simple Payments (for Finance UI) --------------------
class PaymentStatus(str, enum.Enum):
    paid = "PAID"
    pending = "PENDING"
    failed = "FAILED"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), nullable=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    method: Mapped[str] = mapped_column(String(32), default="Cash")
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.paid)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

Index("ix_payment_patient_time", Payment.patient_id, Payment.created_at)

# -------------------- Legacy/Compat Invoices (for older modules) --------------------
class InvoiceStatus(str, enum.Enum):
    open = "open"
    paid = "paid"
    void = "void"

class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    appointment_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus), default=InvoiceStatus.open, nullable=False)

    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)
    paid_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)

    items: Mapped[list["InvoiceItem"]] = relationship(
        "InvoiceItem", back_populates="invoice", cascade="all, delete-orphan"
    )

Index("ix_invoice_patient_time", Invoice.patient_id, Invoice.created_at)

class InvoiceItem(Base):
    __tablename__ = "invoice_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)

    description: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0.00)

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="items")

Index("ix_invoice_item_invoice", InvoiceItem.invoice_id)

# -------------------- Notifications --------------------
class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    read: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship("User", back_populates="notifications")

Index("ix_notif_user_time", Notification.user_id, Notification.created_at)

# -------------------- Disciplinary (NEW) --------------------
class DisciplinarySeverity(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class DisciplinaryStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"

# -------------------- Password Reset --------------------
class PasswordReset(Base):
    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User")


class DisciplinaryRecord(Base):
    __tablename__ = "disciplinary_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)

    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")

    severity: Mapped[DisciplinarySeverity] = mapped_column(
        Enum(DisciplinarySeverity), default=DisciplinarySeverity.low
    )
    status: Mapped[DisciplinaryStatus] = mapped_column(
        Enum(DisciplinaryStatus), default=DisciplinaryStatus.open
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="disciplinary_records")

# --- Late relationship binding (avoids “failed to locate name” on import) ---
from sqlalchemy.orm import relationship as _relationship  # local alias

# One-to-one relationships after all dependent classes are defined
User.patient = _relationship("Patient", back_populates="user", uselist=False)
User.doctor = _relationship("Doctor", back_populates="user", uselist=False)
User.receptionist = _relationship("Receptionist", back_populates="user", uselist=False)
User.admin_profile = _relationship("AdminProfile", back_populates="user", uselist=False)
User.pharmacist_profile = _relationship("Pharmacist", back_populates="user", uselist=False)
User.support_profile = _relationship("SupportAgent", back_populates="user", uselist=False)
User.finance_profile = _relationship("FinanceOfficer", back_populates="user", uselist=False)
