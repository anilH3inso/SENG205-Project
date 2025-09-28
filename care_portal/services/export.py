# care_portal/services/export.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Iterable, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    User, Patient, MedicalRecord, Prescription
)

def export_treatment_history_pdf(db: Session, patient_id: int, filepath: str, include_prescriptions: bool = True):
    """
    Export a patient's treatment history to a PDF file.
    Includes MedicalRecord entries, and (optionally) any Prescription rows
    that might not be mirrored into MedicalRecord (for safety).
    """
    try:
        # Lazy import so the app remains usable without the dependency.
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib import colors
    except Exception as e:
        raise RuntimeError(
            "Missing dependency: reportlab. Install it with:  pip install reportlab"
        ) from e

    # ---------------- Load patient & entries ----------------
    p = db.get(Patient, patient_id)
    if not p:
        raise ValueError(f"Patient id={patient_id} not found")
    u = db.get(User, p.user_id) if p.user_id else None

    # Medical records (doctor or patient authored)
    records = db.scalars(
        select(MedicalRecord)
        .where(MedicalRecord.patient_id == patient_id)
        .order_by(MedicalRecord.created_at.asc())
    ).all()

    # If include_prescriptions: load prescriptions, but we might have already mirrored them as [Rx] records
    prescriptions = []
    if include_prescriptions:
        prescriptions = db.scalars(
            select(Prescription)
            .where(Prescription.patient_id == patient_id)
            .order_by(Prescription.created_at.asc())
        ).all()

    # ---------------- Build PDF ----------------
    doc = SimpleDocTemplate(filepath, pagesize=A4, title="Treatment History")
    styles = getSampleStyleSheet()
    story = []

    title = f"Treatment History — {u.full_name or u.email if u else 'Patient'}"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 8))

    # Demographics
    info_lines = [
        f"MRN: {p.mrn or '-'}",
        f"DOB: {p.dob or '-'}",
        f"Phone: {u.phone if u and u.phone else '-'}",
        f"Address: {p.address or '-'}",
    ]
    for line in info_lines:
        story.append(Paragraph(line, styles["Normal"]))
    story.append(Spacer(1, 12))

    # Table: Medical Records
    story.append(Paragraph("Medical Records", styles["Heading2"]))
    if records:
        data = [["Date", "Author", "Entry"]]
        for r in records:
            who = "Doctor" if getattr(r, "author_role", None) and r.author_role.name.lower() == "doctor" else "Patient"
            when = r.created_at.strftime("%Y-%m-%d %H:%M") if isinstance(r.created_at, datetime) else "-"
            text = (r.text or "").replace("\n", "<br/>")
            data.append([when, who, text])

        tbl = Table(data, colWidths=[110, 70, 320])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#233247")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#334155")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#1D2A3C"), colors.HexColor("#233247")]),
            ("LEFTPADDING", (0,0), (-1,-1), 6),
            ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 4),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ]))
        story.append(tbl)
    else:
        story.append(Paragraph("No records found.", styles["Italic"]))
    story.append(Spacer(1, 16))

    # Table: Prescriptions (optional safety net)
    if include_prescriptions:
        story.append(Paragraph("Prescriptions (raw)", styles["Heading2"]))
        if prescriptions:
            data = [["Date", "Medication", "Dose", "Frequency", "Duration", "Notes"]]
            for rx in prescriptions:
                when = rx.created_at.strftime("%Y-%m-%d %H:%M") if isinstance(rx.created_at, datetime) else "-"
                data.append([
                    when,
                    rx.medication or "-",
                    rx.dosage or "-",
                    "-",  # rx may store freq/duration in rx.text; we keep columns consistent
                    "-",
                    rx.instructions or "-"
                ])
            tbl = Table(data, colWidths=[110, 130, 70, 90, 80, 140])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#233247")),
                ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#334155")),
                ("VALIGN", (0,0), (-1,-1), "TOP"),
                ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#1D2A3C"), colors.HexColor("#233247")]),
                ("LEFTPADDING", (0,0), (-1,-1), 6),
                ("RIGHTPADDING", (0,0), (-1,-1), 6),
                ("TOPPADDING", (0,0), (-1,-1), 4),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ]))
            story.append(tbl)
        else:
            story.append(Paragraph("No prescriptions found.", styles["Italic"]))
        story.append(Spacer(1, 8))

    doc.build(story)
    return filepath
