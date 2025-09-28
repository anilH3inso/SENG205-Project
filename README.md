# âœ¨ Care Portal v1.5

![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green)
![Tkinter](https://img.shields.io/badge/Tkinter-Desktop%20UI-orange)

> ðŸ¥ **Enterprise-Grade Hospital Management System**  
> Built with Python, SQLAlchemy, and a local AI Chatbot (`tinyllama.gguf`) â€” designed for scalability, reliability, and production readiness.

---

## âœ¨ Features

- ðŸ“Š **Role-Based Dashboards** â€“ Doctor, Patient, Receptionist, Pharmacist, Finance, Support, Admin
- ðŸ“… **Smart Appointment Scheduling** â€“ Availability tracking & conflict detection
- ðŸ¥ **Medical Records & Prescriptions** â€“ Complete history with exportable records
- ðŸ’° **Billing & Payments** â€“ Status tracking, receipts, invoice generation
- ðŸŽŸ **Support Ticket System** â€“ Real-time patient/staff communication
- ðŸ’Š **Pharmacy Management** â€“ Dispensing & inventory tracking
- â± **Staff Check-in / Check-out** â€“ Attendance module for all roles
- ðŸ¤– **AI Chatbot** â€“ Powered by TinyLlama + FastAPI (local inference)
- ðŸ–¤ **Dark-Themed, DPI-Aware GUI** â€“ Modern, sleek design
- ðŸ“ˆ **Admin Analytics Dashboard** â€“ Usage stats & insights
- ðŸ“‘ **PDF/CSV Export** â€“ For appointments, billing, and medical reports

---

## ðŸ›  Installation

### 1ï¸âƒ£ Clone the Repository (with Git LFS for model files)

```bash
git lfs install
git clone https://github.com/anilH3inso/care_portal.git
cd care_portal
```

### 2ï¸âƒ£ Setup Python Environment

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 3ï¸âƒ£ Database Setup

```bash
python -m care_portal.seed
```

This seeds:
- Admin: `admin@care.local / admin123`
- Receptionist, Pharmacist, Support, Finance
- 16 Doctors + 30 Sample Patients
- 90+ Pre-created appointments for testing

### 4ï¸âƒ£ Run the App

```bash
python run.py
```

This will:
- Kill any previous process on `:8001`
- Start FastAPI AI server (chatbot)
- Launch Tkinter desktop GUI

---

## ðŸ¤– AI Chatbot

- Powered by **TinyLlama (gguf)** for local inference
- Uses FastAPI microservice (`ai_server.py`)
- Handles:
  - Appointment booking / cancellation
  - Treatment history queries
  - Notifications & reminders
  - Doctor availability lookup
  - Natural language & typo-tolerant parsing

---

## ðŸ“œ License

MIT License Â© 2025 [anilH3inso](https://github.com/anilH3inso)
