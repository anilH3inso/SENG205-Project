# care_portal/seed.py
from __future__ import annotations

"""
Seed script for Care Portal.

- Creates default logins for: admin, receptionist, pharmacist, support, finance
- Creates 16+ doctors with specialties
- Creates 30 patients with AU-flavored data
- Seeds doctor availability for the next 90 days (if DoctorAvailability model exists)
- Auto-books random appointments over the next 90 days (2–4 per patient)
  while avoiding UNIQUE collisions on (doctor_id, scheduled_for)
- NEW: Guarantees at most ONE appointment per patient per calendar day
"""

import random
from collections import defaultdict
from datetime import datetime, timedelta, date, time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

# ---- Safe engine/session import (fallback if needed) ------------------------
try:
    from .db import engine as _ENGINE, SessionLocal  # type: ignore[attr-defined]
except Exception:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from .db import DATABASE_URL

    _ENGINE = create_engine(DATABASE_URL, echo=False, future=True)
    SessionLocal = sessionmaker(
        bind=_ENGINE,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )

# Import models AFTER engine/session are settled so they can bind/register tables
from . import models as M
from .models import (
    Base,            # metadata
    User, Role,
    Patient, Doctor,
    Appointment, AppointmentStatus,
)

# Optional: DoctorAvailability may not exist in some builds
try:
    from .models import DoctorAvailability  # type: ignore
    HAS_AV = True
except Exception:
    HAS_AV = False

from .auth import hash_password


# ---------------- Schema ----------------
def create_all() -> None:
    Base.metadata.create_all(bind=_ENGINE)


# ---------------- Helpers ----------------
def _get_user(db, email: str) -> Optional[User]:
    return db.scalar(select(User).where(User.email == email))


def ensure_role_fallback(name: str, default: Role = Role.admin) -> Role:
    """Return Role.name if present, else fallback to 'default' (keeps app usable if some enums are missing)."""
    return getattr(Role, name, default)


def ensure_user(
    email: str,
    password: str,
    role: Role,
    full_name: str = "",
    phone: str | None = None,
) -> User:
    """
    Create a User. If role is patient/doctor, also create Patient/Doctor rows.
    Idempotent: returns existing user if found.
    """
    with SessionLocal() as db:
        u = _get_user(db, email)
        if u:
            return u

        u = User(
            email=email,
            full_name=full_name,
            role=role,
            phone=phone,
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


def ensure_patient(
    email: str,
    password: str,
    full_name: str,
    *,
    phone: str | None = None,
    dob: date | None = None,
    gender: str | None = None,
    address: str | None = None,
    insurance_no: str | None = None,
    emergency_contact_name: str | None = None,
    emergency_contact_phone: str | None = None,
    allergies: str | None = None,
    chronic_conditions: str | None = None,
) -> User:
    with SessionLocal() as db:
        u = _get_user(db, email)
        if not u:
            u = User(
                email=email,
                full_name=full_name,
                role=Role.patient,
                phone=phone,
                password_hash=hash_password(password),
            )
            db.add(u)
            db.flush()
            db.add(
                Patient(
                    user_id=u.id,
                    dob=dob,
                    gender=gender,
                    address=address,
                    insurance_no=insurance_no,
                    emergency_contact_name=emergency_contact_name,
                    emergency_contact_phone=emergency_contact_phone,
                    allergies=allergies,
                    chronic_conditions=chronic_conditions,
                )
            )
            db.commit()
            return u

        # Ensure Patient row exists; only fill missing fields
        p = db.scalar(select(Patient).where(Patient.user_id == u.id))
        if not p:
            p = Patient(user_id=u.id)
            db.add(p)

        if not u.phone and phone:
            u.phone = phone

        for field, value in dict(
            dob=dob,
            gender=gender,
            address=address,
            insurance_no=insurance_no,
            emergency_contact_name=emergency_contact_name,
            emergency_contact_phone=emergency_contact_phone,
            allergies=allergies,
            chronic_conditions=chronic_conditions,
        ).items():
            if getattr(p, field, None) in (None, "", 0) and value not in (None, ""):
                setattr(p, field, value)

        db.commit()
        return u


def ensure_doctor(
    email: str,
    password: str,
    full_name: str,
    specialty: str,
    *,
    phone: str | None = None,
) -> User:
    """Idempotent ensure for doctors; updates specialty/phone if they change."""
    with SessionLocal() as db:
        u = _get_user(db, email)
        if u:
            d = db.scalar(select(Doctor).where(Doctor.user_id == u.id))
            if d and specialty and getattr(d, "specialty", None) != specialty:
                d.specialty = specialty
            if phone and not u.phone:
                u.phone = phone
            db.commit()
            return u

        u = User(
            email=email,
            full_name=full_name,
            role=Role.doctor,
            phone=phone,
            password_hash=hash_password(password),
        )
        db.add(u)
        db.flush()
        db.add(Doctor(user_id=u.id, specialty=specialty or "General"))
        db.commit()
        return u


def ensure_generic(
    role_name: str,
    email: str,
    password: str,
    full_name: str,
    phone: str | None = None,
) -> User:
    role = ensure_role_fallback(role_name, default=Role.admin)
    return ensure_user(email, password, role, full_name, phone)


# ---------------- AU/Melbourne fake-data helpers ----------------
RNG = random.Random(42)

MEL_SUBURBS = [
    "Melbourne VIC 3000", "Carlton VIC 3053", "Fitzroy VIC 3065", "Richmond VIC 3121",
    "Southbank VIC 3006", "Docklands VIC 3008", "St Kilda VIC 3182", "Brunswick VIC 3056",
    "Hawthorn VIC 3122", "Prahran VIC 3181", "South Yarra VIC 3141", "Collingwood VIC 3066",
    "Footscray VIC 3011", "Northcote VIC 3070", "Camberwell VIC 3124", "Malvern VIC 3144",
]

STREET_NAMES = [
    "Collins St","Swanston St","Elizabeth St","Flinders St","Bourke St","Lygon St",
    "Chapel St","Victoria St","Bridge Rd","Brunswick St","Smith St","Toorak Rd",
    "High St","Glenferrie Rd","Burwood Rd","Kings Way",
]

ALLERGIES = ["None", "Penicillin", "Peanuts", "Dust mites", "Pollen", "Shellfish"]
CONDITIONS = ["None", "Hypertension", "Type 2 Diabetes", "Asthma", "Anxiety", "Migraine"]

def au_mobile() -> str:
    return f"+61 4{RNG.randint(10,99)} {RNG.randint(100,999):03d} {RNG.randint(100,999):03d}"

def au_landline_mel() -> str:
    return f"+61 3 {RNG.randint(1000,9999):04d} {RNG.randint(1000,9999):04d}"

def au_address() -> str:
    return f"{RNG.randint(1,399)} {RNG.choice(STREET_NAMES)}, {RNG.choice(MEL_SUBURBS)}"

def rand_dob(min_year=1958, max_year=2006) -> date:
    return date(RNG.randint(min_year, max_year), RNG.randint(1,12), RNG.randint(1,28))

def maybe(items: list[str]) -> str:
    x = RNG.choice(items)
    return "" if x == "None" else x


# ---------------- Availability seeding (random per doctor, 90 days) ----------
def seed_doctor_availability(days: int = 90) -> None:
    """
    Create random but sensible availability per doctor for the next `days` (default 90).
    Skips if DoctorAvailability model is not present.
    """
    if not HAS_AV:
        return

    start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    with SessionLocal() as db:
        doctors = db.scalars(select(Doctor)).all()
        if not doctors:
            return

        for d in doctors:
            r = random.Random(1000 + d.id)

            base_start_hour = r.choice([8, 8, 9, 9, 9, 10])
            base_start_min = r.choice([0, 0, 0, 30])
            base_len_hours = r.choice([7, 7, 8, 6])
            base_end_hour = min(19, base_start_hour + base_len_hours)
            base_end_min = 0

            slot_minutes = r.choice([15, 20, 30])
            works_some_weekends = r.random() < 0.25

            for i in range(days):
                day = start_date + timedelta(days=i)
                dow = day.weekday()  # Mon=0 .. Sun=6

                # Closed Sundays; optional Saturdays
                if dow == 6:
                    continue
                if dow == 5 and not works_some_weekends:
                    continue
                # Occasional weekday off
                if dow < 5 and r.random() < 0.10:
                    continue

                jitter_start = r.choice([-30, -15, 0, 0, 0, 15, 30])
                jitter_end = r.choice([-30, 0, 0, 15, 30, 45])

                if dow == 5:
                    start_h, start_m = 10, 0
                    end_h, end_m = 14, 0
                else:
                    start_dt = day.replace(hour=base_start_hour, minute=base_start_min) + timedelta(minutes=jitter_start)
                    end_dt = day.replace(hour=base_end_hour, minute=base_end_min) + timedelta(minutes=jitter_end)
                    if end_dt <= start_dt + timedelta(hours=4):
                        end_dt = start_dt + timedelta(hours=4)
                    start_h, start_m = start_dt.hour, start_dt.minute - (start_dt.minute % 5)
                    end_h, end_m = end_dt.hour, end_dt.minute - (end_dt.minute % 5)

                # Skip if already seeded for that day/doctor
                exists = db.scalar(
                    select(DoctorAvailability).where(
                        DoctorAvailability.doctor_id == d.id,
                        DoctorAvailability.day >= day,
                        DoctorAvailability.day < day + timedelta(days=1),
                    )
                )
                if exists:
                    continue

                db.add(
                    DoctorAvailability(
                        doctor_id=d.id,
                        day=day,
                        start_time=f"{start_h:02d}:{start_m:02d}",
                        end_time=f"{end_h:02d}:{end_m:02d}",
                        slot_minutes=slot_minutes,
                    )
                )

        db.commit()


# ---------------- Random appointment generation ----------------
OFFICE_START = time(8, 0)
OFFICE_END   = time(18, 0)

def _rand_business_dt(start: datetime, end: datetime, r: random.Random) -> datetime:
    """Pick a random datetime between start and end, constrained to office hours."""
    span_days = (end.date() - start.date()).days
    day = start + timedelta(days=r.randint(0, max(0, span_days - 1)))
    hour = r.randint(OFFICE_START.hour, OFFICE_END.hour - 1)
    minute = r.choice([0, 10, 15, 20, 30, 40, 45])  # varied minute grid = fewer clashes
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)

def _choose_free_slot_from_availability(db, doc_id: int, start: datetime, end: datetime,
                                        r: random.Random, used: dict[int, set[datetime]]) -> Optional[datetime]:
    """If DoctorAvailability exists, pick a REAL free slot; otherwise None."""
    if not HAS_AV:
        return None

    av_rows = db.execute(
        select(DoctorAvailability).where(
            DoctorAvailability.doctor_id == doc_id,
            DoctorAvailability.day >= start,
            DoctorAvailability.day < end,
        )
    ).scalars().all()
    if not av_rows:
        return None

    row = r.choice(av_rows)
    sh, sm = map(int, row.start_time.split(":"))
    eh, em = map(int, row.end_time.split(":"))
    slot = max(10, int(row.slot_minutes or 20))

    start_dt = row.day.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end_dt   = row.day.replace(hour=eh, minute=em, second=0, microsecond=0)

    slots = []
    cur = start_dt
    while cur + timedelta(minutes=slot) <= end_dt:
        slots.append(cur)
        cur += timedelta(minutes=slot)

    free = [s for s in slots if s not in used[doc_id]]
    return r.choice(free) if free else None

def seed_random_appointments(
    days: int = 90,
    patients_limit: int = 30,
    per_patient: tuple[int, int] = (2, 4),
) -> int:
    """
    Create random scheduled appointments within the next `days`.
    Guarantees:
      - a patient has at most ONE appointment per calendar day
      - avoids (doctor_id, scheduled_for) collisions
    """
    r = random.Random(777)
    start = datetime.now().replace(second=0, microsecond=0)
    end = start + timedelta(days=days)

    created = 0
    with SessionLocal() as db:
        pats: list[Patient] = db.scalars(
            select(Patient).order_by(Patient.id).limit(patients_limit)
        ).all()
        docs: list[Doctor] = db.scalars(select(Doctor).order_by(Doctor.id)).all()
        if not pats or not docs:
            return 0

        # Preload all existing bookings to avoid collisions and enforce patient/day rule
        used_by_doctor: dict[int, set[datetime]] = defaultdict(set)  # doctor_id -> set(datetime)
        used_day_by_patient: dict[int, set[date]] = defaultdict(set) # patient_id -> set(date)

        for did, pid, when in db.execute(
            select(Appointment.doctor_id, Appointment.patient_id, Appointment.scheduled_for)
        ).all():
            if when is not None:
                when = when.replace(second=0, microsecond=0)
                used_by_doctor[int(did)].add(when)
                used_day_by_patient[int(pid)].add(when.date())

        for p in pats:
            n_appts = r.randint(*per_patient)
            for _ in range(n_appts):
                # Try multiple times to find a free slot that also doesn't violate the one-per-day rule
                for _attempt in range(60):
                    d = r.choice(docs)
                    when = (
                        _choose_free_slot_from_availability(db, d.id, start, end, r, used_by_doctor)
                        or _rand_business_dt(start, end, r)
                    ).replace(second=0, microsecond=0)

                    # Enforce constraints
                    if when in used_by_doctor[d.id]:
                        continue  # slot already taken for this doctor
                    if when.date() in used_day_by_patient[p.id]:
                        continue  # patient already has a booking that day

                    ap = Appointment(
                        patient_id=p.id,           # Patient.id (not user_id)
                        doctor_id=d.id,
                        scheduled_for=when,
                        reason=r.choice(["Checkup", "Consultation", "Follow-up", "Test results", "Prescription"]),
                        status=AppointmentStatus.booked,
                    )

                    try:
                        db.add(ap)
                        db.commit()              # commit per insert; cheap on SQLite
                        # Update trackers after success
                        used_by_doctor[d.id].add(when)
                        used_day_by_patient[p.id].add(when.date())
                        created += 1
                        break
                    except IntegrityError:
                        db.rollback()            # race/collision; try another slot
                        continue
                # if all attempts fail, skip silently
    return created


# ---------------- Seed script ----------------
def main():
    create_all()

    # ---- Default accounts (emails/passwords you can log in with) ----
    DEFAULTS = {
        "admin":        ("admin@care.local", "admin123", "Admin User", au_landline_mel()),
        "receptionist": ("reception@care.local", "re123", "Reception Desk", au_landline_mel()),
        "pharmacist":   ("pharma@care.local", "pharma123", "Pharmacy Desk", au_landline_mel()),
        "support":      ("support@care.local", "support123", "Support Desk", au_landline_mel()),
        "finance":      ("finance@care.local", "finance123", "Finance Desk", au_landline_mel()),
    }

    # Staff roles (fallbacks keep seeding robust even if Role misses some names)
    ensure_generic("admin",        *DEFAULTS["admin"])
    ensure_generic("receptionist", *DEFAULTS["receptionist"])
    ensure_generic("pharmacist",   *DEFAULTS["pharmacist"])
    ensure_generic("support",      *DEFAULTS["support"])
    ensure_generic("finance",      *DEFAULTS["finance"])

    # ---- Doctors (more) ----
    docs = [
        ("dr1@care.local",  "doctor123", "Meredith Grey",    "General"),
        ("dr2@care.local",  "doctor123", "Derek Shepherd",   "Cardiology"),
        ("dr3@care.local",  "doctor123", "Miranda Bailey",   "Pediatrics"),
        ("dr4@care.local",  "doctor123", "Cristina Yang",    "Surgery"),
        ("dr5@care.local",  "doctor123", "Arizona Robbins",  "Orthopedics"),
        ("dr6@care.local",  "doctor123", "Alex Karev",       "Oncology"),
        ("dr7@care.local",  "doctor123", "Amelia Shepherd",  "Neurology"),
        ("dr8@care.local",  "doctor123", "Mark Sloan",       "Plastic Surgery"),
        # additional
        ("dr9@care.local",  "doctor123", "Izzie Stevens",    "Dermatology"),
        ("dr10@care.local", "doctor123", "George O'Malley",  "Emergency"),
        ("dr11@care.local", "doctor123", "April Kepner",     "Trauma"),
        ("dr12@care.local", "doctor123", "Jackson Avery",    "ENT"),
        ("dr13@care.local", "doctor123", "Callie Torres",    "Orthopedics"),
        ("dr14@care.local", "doctor123", "Teddy Altman",     "Cardiothoracic"),
        ("dr15@care.local", "doctor123", "Jo Wilson",        "General"),
        ("dr16@care.local", "doctor123", "Andrew DeLuca",    "General"),
    ]
    for email, pw, name, spec in docs:
        ensure_doctor(email, pw, name, spec, phone=au_mobile())

    # ---- Patients (30) ----
    first_names = [
        "John","Jane","Michael","Emily","Daniel","Sophia","Liam","Olivia",
        "Jack","Ava","Noah","Mia","Ethan","Isla","Lucas","Amelia",
        "Harper","Elijah","Chloe","Grace","Oliver","Ruby","Max","Zoe",
        "Henry","Emma","Leo","Scarlett","Aria","Mason"
    ]
    genders = ["M","F"]
    for i in range(1, 31):
        email = f"pt{i:02d}@care.local"
        name = f"{first_names[i-1]} Test"
        ensure_patient(
            email,
            "patient123",
            name,
            phone=au_mobile(),
            dob=rand_dob(1958, 2006),
            gender=random.choice(genders),
            address=au_address(),
            insurance_no=f"AUS-INS-{random.randint(100000, 999999)}",
            emergency_contact_name=random.choice(
                ["Emma Citizen","Oliver Smith","Grace Johnson","Harry Davis","Zoe Wilson",
                 "Charlie Brown","Ruby Taylor","Max Martin"]
            ),
            emergency_contact_phone=au_mobile(),
            allergies=maybe(ALLERGIES),
            chronic_conditions=maybe(CONDITIONS),
        )

    # ---- Availability for 3 months ----
    seed_doctor_availability(days=90)

    # ---- Random appointments for next 3 months (30 patients) ----
    n_appts = seed_random_appointments(days=90, patients_limit=30, per_patient=(2,4))

    # ---- Summary / Default logins ----
    print("\n=== Default logins ===")
    print(f"Admin:        {DEFAULTS['admin'][0]} / {DEFAULTS['admin'][1]}")
    print(f"Receptionist: {DEFAULTS['receptionist'][0]} / {DEFAULTS['receptionist'][1]}")
    print(f"Pharmacist:   {DEFAULTS['pharmacist'][0]} / {DEFAULTS['pharmacist'][1]}")
    print(f"Support:      {DEFAULTS['support'][0]} / {DEFAULTS['support'][1]}")
    print(f"Finance:      {DEFAULTS['finance'][0]} / {DEFAULTS['finance'][1]}")
    print("Doctors:      dr1@care.local .. dr16@care.local  / doctor123")
    print("Patients:     pt01@care.local .. pt30@care.local / patient123")

    print(
        f"\nDatabase initialized: staff (admin/receptionist/pharmacist/support/finance), "
        f"{len(docs)} doctors, 30 patients; availability 90 days; {n_appts} appointments created."
    )


if __name__ == "__main__":
    main()
