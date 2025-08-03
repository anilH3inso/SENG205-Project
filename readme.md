# 🏥 Smart Patient Management System (PMS)

📌 A Centralized Digital Health Solution for **Pakenham Hospital**  
Built with **Python (Tkinter)** and **SQL**, powered by AI & automation.

---

## 📖 Overview

The **Smart Patient Management System (PMS)** is a secure and scalable desktop application that digitalizes hospital operations. It supports patient registration, appointment management, attendance tracking, and an AI-powered support system — all built using **Python’s Tkinter** for the GUI.

> 🎓 Developed as part of **SENG205 (T2 2025)** at **Kent Institute Australia**

---

## 🎯 Core Features

### 🧾 Patient Registration & Records
- GUI form for new and returning patient registration  
- Secure database storing personal details, treatment history, and records  

### 📅 Appointment Scheduling
- Real-time doctor availability  
- Booking, waitlisting, and auto-assignment via GUI  

### ⏱️ Attendance Tracking
- Check-in via RFID/biometric/GUI  
- Automatic alerts for low attendance thresholds  

### 💬 AI Support & Helpdesk
- Built-in chatbot for FAQs and patient support  
- Mental health and wellbeing appointment support  
- Future-ready for ticketing system integration  

---

## 🛠️ Tech Stack

| Layer        | Technology            |
|--------------|------------------------|
| GUI (Frontend) | Tkinter (Python)       |
| Backend      | Python Modules         |
| Database     | SQLite / MySQL         |
| AI Chatbot   | DialogFlow / Rasa      |
| Deployment   | Local Executable (.py) |

---

## 🗂️ Project Structure
---
SENG205-PMS/
├── app/
│ ├── gui/
│ │ ├── login_gui.py
│ │ ├── register_gui.py
│ │ ├── appointment_gui.py
│ │ └── chatbot_gui.py
│ ├── routes/
│ │ ├── auth.py
│ │ ├── appointment.py
│ │ ├── attendance.py
│ │ └── chatbot.py
│ ├── models/
│ │ ├── patient.py
│ │ ├── appointment.py
│ │ └── attendance.py
│ └── utils/
│ ├── email_alerts.py
│ └── chatbot.py
├── database/
│ └── smartpms.db
├── tests/
├── config.py
├── main.py
├── .env
├── requirements.txt
└── README.md
---

---

## ✅ Feature to Module Mapping

| Feature                      | Module(s)                                        |
|-----------------------------|--------------------------------------------------|
| Patient Registration         | `gui/register_gui.py`, `routes/auth.py`         |
| Appointment Management       | `gui/appointment_gui.py`, `routes/appointment.py`|
| Attendance Tracking          | `routes/attendance.py`, `models/attendance.py`  |
| Low Attendance Alerts        | `utils/email_alerts.py`                         |
| Chatbot Integration          | `gui/chatbot_gui.py`, `utils/chatbot.py`        |

---

## 📌 Requirements Coverage

| Requirement                       | Status   |
|----------------------------------|----------|
| Tkinter GUI                      | ✅ Met    |
| Patient Database                 | ✅ Met    |
| Appointment Booking              | ✅ Met    |
| Attendance Monitoring            | ✅ Met    |
| AI Chatbot Support               | ✅ Met    |
| Modular Architecture             | ✅ Met    |
| Login & Secure Access            | ✅ Met    |

---

## 👥 Team Contributions

| Member    | Responsibilities                              |
|-----------|-----------------------------------------------|
| **Anil**      | Project title, abstract, GitHub setup         |
| **Mark**      | Logic diagram, system flow design             |
| **Ronak**     | Implementation plan, backend architecture     |
| **Sanjana**   | Feature requirements, chatbot functionality   |
