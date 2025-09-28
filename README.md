# ðŸ¥ Care Portal v1.5

<p align="center">
  <img src="https://img.shields.io/badge/Status-Production%20Ready-brightgreen?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688?style=for-the-badge&logo=fastapi" />
  <img src="https://img.shields.io/badge/Tkinter-Desktop%20UI-orange?style=for-the-badge" />
</p>

> ðŸ’¡ **Enterprise-Grade Hospital Management System** with a Local AI Chatbot â€” built for scalability and production readiness.

---

## âœ¨ Features

- ðŸ”‘ **Role-Based Dashboards** (Doctor, Patient, Receptionist, Pharmacist, Finance, Admin)
- ðŸ“… **Smart Appointment Scheduling** with availability & conflict detection
- ðŸ“œ **Medical Records & Prescriptions** tracking with exportable history
- ðŸ’³ **Billing & Payment Module** with status tracking
- ðŸ†˜ **Support Ticket System** with real-time notifications
- ðŸ¥ **Pharmacy Dispensing & Inventory**
- ðŸ‘¨â€âš•ï¸ **Staff Check-in / Check-out** attendance tracking
- ðŸ¤– **AI Chatbot** powered by `tinyllama.gguf` (FastAPI)
- ðŸŒ™ **Dark-Themed, DPI-Aware GUI**
- ðŸ“Š **Admin Analytics Dashboard**
- ðŸ“ **PDF/CSV Export** for reports and records

---

## ðŸ›  Installation

### 1ï¸âƒ£ Clone the Repository

```bash
git clone https://github.com/anilH3inso/care_portal.git
cd care_portal
```

### 2ï¸âƒ£ Create & Activate Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

### 3ï¸âƒ£ Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> âœ… Ensure you have **Git LFS** installed to download model files.

### 4ï¸âƒ£ Initialize Database

```bash
python -m care_portal.seed
```

### 5ï¸âƒ£ Run the Application

```bash
python run.py
```

This will:
- Start **FastAPI AI server** (on port `8001` by default)
- Launch **Desktop GUI** (Tkinter-based)

---

## âš™ï¸ Configuration

| Variable | Default | Description |
|---------|---------|-------------|
| `CARE_PORTAL_PORT` | `8001` | Port for AI server |
| `CARE_PORTAL_HOST` | `127.0.0.1` | Host for AI server |
| `CARE_PORTAL_DB` | `care_portal.db` | Database path |
| `OPENAI_API_KEY` | _(optional)_ | Required if using OpenAI GPT fallback |

Create a `.env` file at the project root for persistent settings.

---

## ðŸ“Š Project Structure

```plaintext
care_portal/
â”œâ”€â”€ app.py              # Tkinter GUI entrypoint
â”œâ”€â”€ run.py              # Launch GUI + AI server
â”œâ”€â”€ models.py           # SQLAlchemy models
â”œâ”€â”€ services/
â”‚   â”œâ”€â”€ ai_server.py    # FastAPI chatbot + APIs
â”‚   â”œâ”€â”€ appointments.py # Appointment service
â”‚   â””â”€â”€ notifications.py# Push notifications
â”œâ”€â”€ ui/                 # GUI frames
â”‚   â”œâ”€â”€ patient.py
â”‚   â”œâ”€â”€ doctor.py
â”‚   â””â”€â”€ theming.py
â””â”€â”€ db.py               # Database session & engine
```

---

## ðŸ§  AI Chatbot

- Uses **TinyLlama** (local) for private inference
- Context-aware answers (role, appointments, notifications)
- Supports:
  - Appointment booking/cancel
  - Doctor availability queries
  - Notifications & reminders
  - Treatment & prescription history
  - Password reset guidance

---

## ðŸ§ª Testing

Run tests with:

```bash
pytest --maxfail=1 --disable-warnings -q
```

---

## ðŸš€ Deployment

For production, use:

```bash
uvicorn care_portal.services.ai_server:app --host 0.0.0.0 --port 8001 --workers 4
```

You may also build a standalone **PyInstaller executable** for Windows/macOS/Linux.

---

## ðŸ“¸ Screenshots

<p align="center">
  <img src="https://github.com/anilH3inso/care_portal/blob/main/testing/AUTH-001.png" width="600" />
  <br><em>Dark Themed Patient Dashboard</em>
</p>

---

## ðŸ¤ Contributing

Pull requests are welcome! Please fork the repo and open a PR.

---

## ðŸ“œ License

MIT License Â© 2025 [anilH3inso](https://github.com/anilH3inso)
