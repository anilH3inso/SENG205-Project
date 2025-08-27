# care_portal/models.py
from __future__ import annotations

import enum
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Enum,
    Date,
    Numeric,
)
from sqlalchemy.orm import relationship, Mapped, mapped_column

from .db import Base


# -------------------- Core roles --------------------
class Role(str, enum.Enum):
    patient = "patient"
    doctor = "doctor"
    admin = "admin"
    receptionist = "receptionist"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # One-to-ones
    patient: Mapped["Patient"] = relationship("Patient", back_populates="user", uselist=False)
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="user", uselist=False)
    receptionist: Mapped["Receptionist"] = relationship("Receptionist", back_populates="user", uselist=False)
    admin_profile: Mapped["AdminProfile"] = relationship("AdminProfile", back_populates="user", uselist=False)

    # One-to-many
    tickets: Mapped[list["SupportTicket"]] = relationship(
        "SupportTicket", back_populates="user", cascade="all, delete-orphan"
    )
    medical_records_authored: Mapped[list["MedicalRecord"]] = relationship(
        "MedicalRecord", back_populates="author"
    )


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

    user: Mapped[User] = relationship("User", back_populates="patient")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="patient")
    medical_records: Mapped[list["MedicalRecord"]] = relationship(
        "MedicalRecord", back_populates="patient", cascade="all, delete-orphan"
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

    user: Mapped[User] = relationship("User", back_populates="doctor")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="doctor")
    availability: Mapped[list["DoctorAvailability"]] = relationship(
        "DoctorAvailability", back_populates="doctor", cascade="all, delete-orphan"
    )


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

    user: Mapped[User] = relationship("User", back_populates="receptionist")


# -------------------- Admin Profile --------------------
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

    user: Mapped[User] = relationship("User", back_populates="admin_profile")


# -------------------- Appointments & extras --------------------
class AppointmentStatus(str, enum.Enum):
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
    status: Mapped[AppointmentStatus] = mapped_column(Enum(AppointmentStatus), default=AppointmentStatus.booked)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="appointments")

    # Billing one-to-many
    billing_items: Mapped[list["Billing"]] = relationship(
        "Billing", back_populates="appointment", cascade="all, delete-orphan"
    )


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


class Prescription(Base):
    __tablename__ = "prescriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# -------------------- Support Tickets --------------------
class TicketStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="tickets")


# -------------------- Doctor Availability (NEW) --------------------
class DoctorAvailability(Base):
    __tablename__ = "doctor_availability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), nullable=False)
    # date-only availability; use date portion of this field
    day: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5), default="17:00")    # "HH:MM"
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)

    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="availability")


# -------------------- Medical Records (NEW) --------------------
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

    patient: Mapped["Patient"] = relationship("Patient", back_populates="medical_records")
    author: Mapped["User"] = relationship("User", back_populates="medical_records_authored")


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


class Billing(Base):
    __tablename__ = "billing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), nullable=False)

    description: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0.00"))
    status: Mapped[BillingStatus] = mapped_column(Enum(BillingStatus), default=BillingStatus.unpaid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # NEW
    payment_method: Mapped[PaymentMethod | None] = mapped_column(Enum(PaymentMethod), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    appointment: Mapped["Appointment"] = relationship("Appointment", back_populates="billing_items")
