# 🏥 Smart Patient Management System (PMS)

📌 A Centralized Digital Health Solution for Pakenham Hospital  
Built with **Python** and **SQL**, powered by AI & automation.

---

## 📖 Overview

The **Smart Patient Management System (PMS)** is an integrated platform designed to digitalize and streamline patient treatment processes for hospitals and healthcare facilities. This system enables efficient patient enrollment, real-time appointment scheduling, attendance tracking, and AI-powered support—helping healthcare providers deliver better, faster, and more personalized care.

This project is developed as part of **SENG205 (T2 2025)** for **Kent Institute Australia**, with a focus on modern, scalable, and secure healthcare technology.

---

## 🎯 Core Features

### 🧾 Patient Registration & Records
- Online registration for new and returning patients  
- Centralized database for patient data, history, and personal info  

### 📅 Appointment Scheduling
- Real-time view of doctor availability  
- Intelligent booking, waitlisting, and automatic doctor allocation  

### ⏱️ Attendance Tracking
- Check-in via biometric, RFID, or online interface  
- Automated alert system for low attendance thresholds  

### 💬 AI Support & Helpdesk
- AI chatbot for answering FAQs and patient support  
- Ticketing system for mental health and wellbeing appointments  

---

## 🛠️ Tech Stack

| Layer       | Technology             |
|-------------|------------------------|
| Backend     | Python (Flask)         |
| Database    | MySQL / PostgreSQL     |
| Frontend    | HTML, CSS, Bootstrap   |
| AI Chatbot  | DialogFlow / Rasa      |
| Hardware    | Raspberry Pi + RFID    |
| Deployment  | Docker / Heroku / AWS  |

---

## 🗃️ Modules Breakdown

📌 1. Patient Enrolment
✅ app/routes/auth.py – Handles registration/login.

✅ app/models/patient.py – Stores personal details, treatment history, and disciplinary records.

✅ database/smartpms.db – Centralized SQL database.

📌 2. Appointment & Management
✅ app/routes/appointment.py – For viewing doctor availability, booking, waitlists.

✅ app/models/appointment.py – Stores appointment logic and waitlist automation.

📌 3. Tracking & Monitoring
✅ app/routes/attendance.py – Check-ins via online or hardware integrations.

✅ app/models/attendance.py – Logs timestamps, calculates attendance rates.

✅ utils/email_alerts.py – Can be used for sending low attendance notifications.

📌 4. Patient Support & Helpdesk System
✅ app/routes/chatbot.py – For AI-powered chatbot responses.

✅ utils/chatbot.py – NLP or AI model integration logic.

✅ Future extension possible for a ticketing system for advanced support.

✅ Appointment routing for mental health & wellbeing is supported in appointment.py.

🔐 Additional Requirements Met
Requirement	Met in Structure?
✅ Scalable & modular design	Yes – separated by domain in /routes/ and /models/
✅ Secure access (login, .env)	Yes – has auth.py and .env file handling
✅ Database integration	Yes – via SQLAlchemy or SQLite file
✅ AI integration (chatbot)	Yes – modular AI support in utils/
✅ Config separation	Yes – config.py and instance/config.py
✅ Testing support	Yes – /tests/ folder for unit/integration testing

---

## 👥 Team Contributions

| Member   | Responsibility                              |
|----------|----------------------------------------------|
| Anil     | Project Title, Abstract, GitHub Setup        |
| Mark     | Logic Diagram and System Flow                |
| Ronak    | Implementation Strategy, Backend Plan        |
| Sanjana  | Objectives, Feature Requirements             |
