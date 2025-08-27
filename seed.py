from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import User, Role, Patient, Doctor
from .auth import hash_password


def create_all():
    Base.metadata.create_all(bind=engine)


def ensure_user(email: str, password: str, role: Role, full_name: str = ""):
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == email))
        if u:
            return u

        u = User(
            email=email,
            full_name=full_name,
            role=role,
            password_hash=hash_password(password),
        )
        db.add(u)
        db.flush()

        if role == Role.patient:
            db.add(Patient(user_id=u.id))
        elif role == Role.doctor:
            db.add(Doctor(user_id=u.id, specialty="General"))

        db.commit()
        return u


if __name__ == "__main__":
    create_all()
    ensure_user("admin@care.local", "admin123", Role.admin, "Admin User")
    ensure_user("dr@care.local", "doctor123", Role.doctor, "Meredith Grey")
    ensure_user("pt@care.local", "patient123", Role.patient, "John Citizen")
    print("Database initialized & demo users created.")
