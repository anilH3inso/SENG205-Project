# care_portal/services/appointments.py
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from ..db import SessionLocal
from ..models import (
    Appointment,
    AppointmentStatus,
    Doctor,
    DoctorAvailability,
)
from .notifications import notify_receptionists_about_request

SLOT_FMT = "%H:%M"
DATE_FMT = "%Y-%m-%d"
_MAX_CAL_DAYS = 365  # guard against huge ranges


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    hh, mm = hhmm.split(":")
    h = int(hh)
    m = int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Time must be in 24h HH:MM format.")
    return h, m


class AppointmentService:
    @staticmethod
    def list_doctors() -> List[Doctor]:
        with SessionLocal() as db:
            return db.scalars(select(Doctor)).all()

    @staticmethod
    def for_doctor_on(doctor_id: int, day: datetime) -> List[Appointment]:
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

    # ---------- availability ----------
    @staticmethod
    def set_availability(
        doctor_id: int,
        day: datetime | str,
        start_hhmm: str,
        end_hhmm: str,
        slot_minutes: int = 30,
    ) -> DoctorAvailability:
        if isinstance(day, str):
            day_dt = datetime.strptime(day, DATE_FMT)
        else:
            day_dt = day

        start_h, start_m = _parse_hhmm(start_hhmm)
        end_h, end_m = _parse_hhmm(end_hhmm)

        if slot_minutes <= 0:
            # extra safety: never allow 0 or negative (infinite loop risk)
            slot_minutes = 30

        day0 = day_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        with SessionLocal() as db:
            av = db.scalar(
                select(DoctorAvailability).where(
                    DoctorAvailability.doctor_id == doctor_id,
                    func.date(DoctorAvailability.day) == func.date(day0),
                )
            )
            if not av:
                av = DoctorAvailability(
                    doctor_id=doctor_id,
                    day=day0,
                    start_time=f"{start_h:02d}:{start_m:02d}",
                    end_time=f"{end_h:02d}:{end_m:02d}",
                    slot_minutes=slot_minutes,
                )
                db.add(av)
            else:
                av.start_time = f"{start_h:02d}:{start_m:02d}"
                av.end_time = f"{end_h:02d}:{end_m:02d}"
                av.slot_minutes = slot_minutes
            db.commit()
            db.refresh(av)
            return av

    @staticmethod
    def clear_availability(doctor_id: int, day: datetime | str) -> None:
        if isinstance(day, str):
            day_dt = datetime.strptime(day, DATE_FMT)
        else:
            day_dt = day
        day0 = day_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        with SessionLocal() as db:
            av = db.scalar(
                select(DoctorAvailability).where(
                    DoctorAvailability.doctor_id == doctor_id,
                    func.date(DoctorAvailability.day) == func.date(day0),
                )
            )
            if av:
                db.delete(av)
                db.commit()

    # ---------- internal: slot generation (guarded) ----------
    @staticmethod
    def _generate_free_slots_for_day(
        day_start: datetime,
        availability: DoctorAvailability,
        busy_str_times: set[str],
    ) -> List[str]:
        try:
            start_h, start_m = map(int, (availability.start_time or "00:00").split(":"))
            end_h, end_m = map(int, (availability.end_time or "00:00").split(":"))
        except Exception:
            return []

        slot_minutes = int(getattr(availability, "slot_minutes", 30) or 30)
        if slot_minutes <= 0:
            # never let this loop run without moving time forward
            slot_minutes = 30

        work_start = day_start.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        work_end = day_start.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

        if work_end <= work_start:
            return []

        slots: List[str] = []
        t = work_start

        # cap iterations to prevent pathological loops
        max_iters = max(1, int((work_end - work_start).total_seconds() // 60) + 2)
        iters = 0

        while t < work_end and iters < max_iters:
            s = t.strftime(SLOT_FMT)
            if s not in busy_str_times:
                slots.append(s)
            t += timedelta(minutes=slot_minutes)
            iters += 1

        return slots

    # ---------- single-day slots ----------
    @staticmethod
    def get_available_slots(doctor_id: int, day: datetime, hide_past_today: bool = False) -> List[str]:
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        next_day = day_start + timedelta(days=1)
        with SessionLocal() as db:
            av = db.scalar(
                select(DoctorAvailability)
                .where(
                    DoctorAvailability.doctor_id == doctor_id,
                    func.date(DoctorAvailability.day) == func.date(day_start),
                )
                .order_by(DoctorAvailability.id.desc())
            )
            if not av:
                return []
            taken_times = db.scalars(
                select(Appointment.scheduled_for).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.scheduled_for >= day_start,
                    Appointment.scheduled_for < next_day,
                    Appointment.status != AppointmentStatus.cancelled,
                )
            ).all()
            busy = {dt.strftime(SLOT_FMT) for dt in taken_times}

        all_free = AppointmentService._generate_free_slots_for_day(day_start, av, busy)

        if hide_past_today and day_start.date() == datetime.now().date():
            now = datetime.now()
            return [
                hhmm for hhmm in all_free
                if datetime.strptime(f"{day_start:%Y-%m-%d} {hhmm}", "%Y-%m-%d %H:%M") > now
            ]

        return all_free

    @staticmethod
    def get_available_dates(doctor_id: int, start: datetime, end: datetime) -> List[str]:
        calendar = AppointmentService.get_availability_calendar(doctor_id, start, end)
        return [row["date"] for row in calendar if row["available"] and row["free"] > 0]

    # ---------- batch calendar (guarded) ----------
    @staticmethod
    def get_available_dates_with_counts(
        doctor_id: int, start: datetime, end: datetime
    ) -> List[Tuple[str, int]]:
        cal = AppointmentService.get_availability_calendar(doctor_id, start, end)
        return [(row["date"], row["free"]) for row in cal if row["available"] and row["free"] > 0]

    @staticmethod
    def get_availability_calendar(
        doctor_id: int, start: datetime, end: datetime
    ) -> List[Dict[str, int | bool | str]]:
        d0 = start.replace(hour=0, minute=0, second=0, microsecond=0)
        d1 = end.replace(hour=0, minute=0, second=0, microsecond=0)
        if d1 < d0:
            d0, d1 = d1, d0

        # hard cap the window to avoid freezing on huge ranges
        if (d1 - d0).days > _MAX_CAL_DAYS:
            d1 = d0 + timedelta(days=_MAX_CAL_DAYS)

        day_count = (d1 - d0).days + 1
        all_days = [d0 + timedelta(days=i) for i in range(day_count)]
        all_dates_str = [dt.strftime(DATE_FMT) for dt in all_days]

        with SessionLocal() as db:
            upper = d1 + timedelta(days=1)
            av_rows = db.scalars(
                select(DoctorAvailability).where(
                    and_(
                        DoctorAvailability.doctor_id == doctor_id,
                        DoctorAvailability.day >= d0,
                        DoctorAvailability.day < upper,
                    )
                )
            ).all()

            av_by_date: Dict[str, DoctorAvailability] = {}
            # last one wins per date (sorted by id to be deterministic)
            for av in sorted(av_rows, key=lambda r: r.id):
                av_by_date[av.day.strftime(DATE_FMT)] = av

            ap_rows = db.scalars(
                select(Appointment.scheduled_for).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.status != AppointmentStatus.cancelled,
                    Appointment.scheduled_for >= d0,
                    Appointment.scheduled_for < upper,
                )
            ).all()
            busy_by_date: Dict[str, set[str]] = defaultdict(set)
            for ts in ap_rows:
                busy_by_date[ts.strftime(DATE_FMT)].add(ts.strftime(SLOT_FMT))

        results: List[Dict[str, int | bool | str]] = []
        for day_dt, day_str in zip(all_days, all_dates_str):
            av_row = av_by_date.get(day_str)
            if not av_row:
                results.append({"date": day_str, "available": False, "free": 0})
                continue
            busy_set = busy_by_date.get(day_str, set())
            free_slots = AppointmentService._generate_free_slots_for_day(day_dt, av_row, busy_set)
            results.append({
                "date": day_str,
                "available": True if free_slots is not None else False,
                "free": len(free_slots) if free_slots else 0
            })
        return results

    # ---------- booking ----------
    @staticmethod
    def book(patient_id: int, doctor_id: int, when: datetime, reason: str = "") -> Appointment:
        day0 = when.replace(hour=0, minute=0, second=0, microsecond=0)
        allowed = set(AppointmentService.get_available_slots(doctor_id, day0))
        requested_str = when.strftime(SLOT_FMT)
        if requested_str not in allowed:
            raise ValueError("Requested time is not available for this doctor.")

        with SessionLocal() as db:
            dup_same_day = db.execute(
                select(Appointment.id).where(
                    Appointment.patient_id == patient_id,
                    Appointment.doctor_id == doctor_id,
                    func.date(Appointment.scheduled_for) == when.date(),
                    Appointment.status.in_((AppointmentStatus.booked, AppointmentStatus.completed)),
                )
            ).first()
            if dup_same_day:
                raise ValueError("You already have an appointment with this doctor on this day.")

            conflict = db.scalar(
                select(Appointment.id).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.scheduled_for == when,
                    Appointment.status != AppointmentStatus.cancelled,
                )
            )
            if conflict:
                raise ValueError("That slot is already booked for the selected doctor.")

            ap = Appointment(
                patient_id=patient_id,
                doctor_id=doctor_id,
                scheduled_for=when,
                reason=reason,
                status=AppointmentStatus.booked,
            )
            db.add(ap)
            try:
                db.commit()
            except IntegrityError as e:
                db.rollback()
                msg = str(getattr(e, "orig", e))
                if "doctor_id" in msg and "scheduled_for" in msg:
                    raise ValueError("That slot is already booked for the selected doctor.")
                if "uq_appt_patient_doctor_day" in msg:
                    raise ValueError("You already have an appointment with this doctor on this day.")
                raise
            db.refresh(ap)
            return ap

    @staticmethod
    def book_at_slot(patient_id: int, doctor_id: int, day: datetime, slot_hhmm: str, reason: str = "") -> Appointment:
        base = day.replace(hour=0, minute=0, second=0, microsecond=0)
        hh, mm = _parse_hhmm(slot_hhmm)
        when = base.replace(hour=hh, minute=mm)

        if when.strftime(SLOT_FMT) not in AppointmentService.get_available_slots(doctor_id, base):
            raise ValueError("Requested time is not available for this doctor.")
        return AppointmentService.book(patient_id, doctor_id, when, reason)

    # ===== paste this to REPLACE create_request in AppointmentService =====
    @staticmethod
    def create_request(
        *, patient_id: int, when: datetime, reason: str = "", doctor_id: int | None = None
    ) -> Appointment:
        """
        Create a 'requested' appointment. If a doctor is chosen, prevent duplicate
        requests at an already-taken time and return a friendly error instead of
        throwing an sqlite IntegrityError dialog.
        """
        with SessionLocal() as db:
            # If a doctor is specified, block exact time collisions with any
            # non-cancelled appointment (booked / completed / requested).
            if doctor_id is not None:
                conflict = db.scalar(
                    select(Appointment.id).where(
                        Appointment.doctor_id == doctor_id,
                        Appointment.scheduled_for == when,
                        Appointment.status != AppointmentStatus.cancelled,
                    )
                )
                if conflict:
                    raise ValueError("That time is already taken for this doctor. Please choose another time.")

            ap = Appointment(
                patient_id=patient_id,
                doctor_id=doctor_id if doctor_id is not None else 0,  # allow “unassigned”
                scheduled_for=when,
                reason=reason or "",
                status=AppointmentStatus.requested,
            )
            db.add(ap)
            try:
                db.commit()
            except IntegrityError as e:
                # Defensive: if a DB UNIQUE hits anyway, translate to a friendly message
                db.rollback()
                msg = str(getattr(e, "orig", e))
                if "doctor_id" in msg and "scheduled_for" in msg:
                    raise ValueError("That time is already taken for this doctor.")
                if "uq_appt_patient_doctor_day" in msg:
                    raise ValueError("You already have an appointment with this doctor on that day.")
                raise
            db.refresh(ap)

            # Notify reception after successful commit
            notify_receptionists_about_request(ap, db=db)
            return ap

    # ---------- soft actions ----------
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

            day0 = new_when.replace(hour=0, minute=0, second=0, microsecond=0)
            allowed = set(AppointmentService.get_available_slots(ap.doctor_id, day0))
            if new_when.strftime(SLOT_FMT) not in allowed:
                raise ValueError("Requested new time is not available for this doctor.")

            dup_same_day = db.execute(
                select(Appointment.id).where(
                    Appointment.patient_id == ap.patient_id,
                    Appointment.doctor_id == ap.doctor_id,
                    func.date(Appointment.scheduled_for) == new_when.date(),
                    Appointment.id != appointment_id,
                    Appointment.status.in_((AppointmentStatus.booked, AppointmentStatus.completed)),
                )
            ).first()
            if dup_same_day:
                raise ValueError("You already have an appointment with this doctor on that day.")

            conflict = db.scalar(
                select(Appointment.id).where(
                    Appointment.doctor_id == ap.doctor_id,
                    Appointment.scheduled_for == new_when,
                    Appointment.id != appointment_id,
                    Appointment.status != AppointmentStatus.cancelled,
                )
            )
            if conflict:
                raise ValueError("That time is already booked for the selected doctor.")

            ap.scheduled_for = new_when
            try:
                db.commit()
            except IntegrityError as e:
                db.rollback()
                msg = str(getattr(e, "orig", e))
                if "doctor_id" in msg and "scheduled_for" in msg:
                    raise ValueError("That time is already booked for the selected doctor.")
                if "uq_appt_patient_doctor_day" in msg:
                    raise ValueError("You already have an appointment with this doctor on that day.")
                raise
