# ✨ Care Portal v1.5  

![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen) 
![Python](https://img.shields.io/badge/Python-3.11%2B-blue) 
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-green) 
![Tkinter](https://img.shields.io/badge/Tkinter-Desktop%20UI-orange)  

> 🏥 **Enterprise-Grade Hospital Management System**  
> Built with Python, SQLAlchemy, and a local AI Chatbot (`tinyllama.gguf`) — designed for scalability, reliability, and production readiness.

---

## ✨ Features  

- 📊 **Role-Based Dashboards** – Doctor, Patient, Receptionist, Pharmacist, Finance, Support, Admin  
- 📅 **Smart Appointment Scheduling** – Availability tracking & conflict detection  
- 🏥 **Medical Records & Prescriptions** – Complete history with exportable records  
- 💰 **Billing & Payments** – Status tracking, receipts, invoice generation  
- 🎟️ **Support Ticket System** – Real-time patient/staff communication  
- 💊 **Pharmacy Management** – Dispensing & inventory tracking  
- ⏱️ **Staff Check-in / Check-out** – Attendance module for all roles  
- 🤖 **AI Chatbot** – Powered by TinyLlama + FastAPI (local inference)  
- 🖤 **Dark-Themed, DPI-Aware GUI** – Modern, sleek design  
- 📈 **Admin Analytics Dashboard** – Usage stats & insights  
- 📑 **PDF/CSV Export** – For appointments, billing, and medical reports  

---

## 🛠 Installation  

### 1️⃣ Clone the Repository (with Git LFS for model files)  
```bash
git lfs install
git clone https://github.com/anilH3inso/care_portal.git
cd care_portal
```

### 2️⃣ Setup Python Environment  
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install --upgrade pip
pip install -r care_portal/requirements.txt
```

### 3️⃣ Database Setup  
```bash
python -m care_portal.seed
```

This seeds:  
- **Admin:** `admin@care.local / admin123`  
- Receptionist, Pharmacist, Support, Finance  
- 16 Doctors + 30 Sample Patients  
- 90+ Pre-created appointments for testing  

### 4️⃣ Run the App  
```bash
python run.py
```

This will:  
- Kill any previous process on `:8001`  
- Start FastAPI AI server (chatbot)  
- Launch Tkinter desktop GUI  

---

## 🤖 AI Chatbot  

- Powered by **TinyLlama (gguf)** for local inference  
- Uses FastAPI microservice (`ai_server.py`)  
- Handles:  
  - Appointment booking / cancellation  
  - Treatment history queries  
  - Notifications & reminders  
  - Doctor availability lookup  
  - Natural language & typo-tolerant parsing  

---

## 📜 License  
[![Typing SVG](https://readme-typing-svg.demolab.com?font=Inter&weight=600&size=28&duration=2500&pause=700&center=true&vCenter=true&multiline=true&repeat=true&width=800&height=160&lines=Anil+Budthapa;Sanjana+Tanwar;Mark+David;Ronak+Pradhan)](https://git.io/typing-svg)




MIT License © 2025 [anilH3inso](https://github.com/anilH3inso)
