from datetime import datetime
from sqlalchemy import select, func
from ..db import SessionLocal
from ..models import Appointment, Doctor, User

class ReportsService:
    @staticmethod
    def appointments_per_doctor(start: datetime, end: datetime):
        """Return list of (doctor_name, count) between start and end (inclusive)."""
        with SessionLocal() as db:
            stmt = (
                select(User.full_name, func.count(Appointment.id))
                .join(Doctor, Doctor.user_id == User.id)
                .where(Appointment.doctor_id == Doctor.id)
                .where(Appointment.scheduled_for >= start)
                .where(Appointment.scheduled_for <= end)
                .group_by(User.full_name)
                .order_by(func.count(Appointment.id).desc())
            )
            return db.execute(stmt).all()
