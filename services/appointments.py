# care_portal/services/appointments.py
from datetime import datetime, timedelta
from typing import List
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from ..db import SessionLocal
from ..models import (
    Appointment,
    AppointmentStatus,
    Doctor,
    Patient,
    DoctorAvailability,
)

SLOT_FMT = "%H:%M"


class AppointmentService:
    # -------- Lookup helpers --------
    @staticmethod
    def list_doctors() -> List[Doctor]:
        """Return all doctors with linked User preloaded (prevents DetachedInstanceError)."""
        with SessionLocal() as db:
            return db.scalars(
                select(Doctor).options(selectinload(Doctor.user))
            ).all()

    @staticmethod
    def list_patient_appointments(patient_id: int) -> List[Appointment]:
        with SessionLocal() as db:
            stmt = (
                select(Appointment)
                .where(Appointment.patient_id == patient_id)
                .order_by(Appointment.scheduled_for.desc())
            )
            return db.scalars(stmt).all()

    @staticmethod
    def for_doctor_on(doctor_id: int, day: datetime) -> List[Appointment]:
        """All appointments for a doctor on a given calendar date."""
        day0 = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day1 = day0 + timedelta(days=1)
        with SessionLocal() as db:
            stmt = (
                select(Appointment)
                .where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.scheduled_for >= day0,
                    Appointment.scheduled_for < day1,
                )
                .order_by(Appointment.scheduled_for.asc())
            )
            return db.scalars(stmt).all()

    # -------- Availability & slots --------
    @staticmethod
    def get_available_slots(doctor_id: int, day: datetime) -> List[str]:
        """Return a list of 'HH:MM' strings that are free for the doctor on that calendar date."""
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)

        with SessionLocal() as db:
            # availability record (most recent if multiple)
            av = db.scalar(
                select(DoctorAvailability)
                .where(
                    DoctorAvailability.doctor_id == doctor_id,
                    func.date(DoctorAvailability.day) == func.date(day_start),
                )
                .order_by(DoctorAvailability.id.desc())
            )

            # defaults if none set
            start_h, start_m = (9, 0)
            end_h, end_m = (17, 0)
            slot_minutes = 30
            if av:
                try:
                    start_h, start_m = map(int, av.start_time.split(":"))
                    end_h, end_m = map(int, av.end_time.split(":"))
                    slot_minutes = av.slot_minutes or 30
                except Exception:
                    pass

            work_start = day_start.replace(hour=start_h, minute=start_m)
            work_end = day_start.replace(hour=end_h, minute=end_m)

            # busy times for the day (exclude cancelled)
            taken_times = db.scalars(
                select(Appointment.scheduled_for).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.scheduled_for >= day_start,
                    Appointment.scheduled_for < next_day,
                    Appointment.status != AppointmentStatus.cancelled,
                )
            ).all()
            busy = {dt.strftime(SLOT_FMT) for dt in taken_times}

        # build all possible slots and filter out busy
        slots: List[str] = []
        if work_end <= work_start:
            return slots

        t = work_start
        while t < work_end:
            s = t.strftime(SLOT_FMT)
            if s not in busy:
                slots.append(s)
            t += timedelta(minutes=slot_minutes)
        return slots

    # -------- Booking API --------
    @staticmethod
    def book(patient_id: int, doctor_id: int, when: datetime, reason: str = "") -> Appointment:
        """Book at an exact datetime; raises ValueError if the slot is already taken."""
        with SessionLocal() as db:
            conflict = db.scalar(
                select(Appointment.id).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.scheduled_for == when,
                    Appointment.status != AppointmentStatus.cancelled,
                )
            )
            if conflict:
                raise ValueError("Slot already booked")

            ap = Appointment(
                patient_id=patient_id,
                doctor_id=doctor_id,
                scheduled_for=when,
                reason=reason,
                status=AppointmentStatus.booked,
            )
            db.add(ap)
            db.commit()
            db.refresh(ap)
            return ap

    @staticmethod
    def book_at_slot(patient_id: int, doctor_id: int, day: datetime, slot_hhmm: str, reason: str = "") -> Appointment:
        """Book using 'HH:MM' on a given calendar date; validates availability first."""
        base = day.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            hh, mm = map(int, slot_hhmm.split(":"))
        except Exception:
            raise ValueError("Invalid slot format, expected HH:MM")
        when = base.replace(hour=hh, minute=mm)

        # validate against generated slots
        if when.strftime(SLOT_FMT) not in AppointmentService.get_available_slots(doctor_id, base):
            raise ValueError("Requested time is not available")

        return AppointmentService.book(patient_id, doctor_id, when, reason)

    @staticmethod
    def cancel(appointment_id: int) -> None:
        with SessionLocal() as db:
            ap = db.get(Appointment, appointment_id)
            if not ap:
                return
            ap.status = AppointmentStatus.cancelled
            db.commit()

    @staticmethod
    def reschedule(appointment_id: int, new_when: datetime) -> None:
        with SessionLocal() as db:
            ap = db.get(Appointment, appointment_id)
            if not ap:
                raise ValueError("Appointment not found")

            conflict = db.scalar(
                select(Appointment.id).where(
                    Appointment.doctor_id == ap.doctor_id,
                    Appointment.scheduled_for == new_when,
                    Appointment.id != appointment_id,
                    Appointment.status != AppointmentStatus.cancelled,
                )
            )
            if conflict:
                raise ValueError("New time is already booked")

            ap.scheduled_for = new_when
            db.commit()
