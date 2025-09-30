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

# Care Portal Seed Accounts

This file lists the default users that are seeded into the database for quick testing and demos.

---

## Staff (DEFAULTS)

| role         | email                | password  | full_name      |
|--------------|----------------------|-----------|----------------|
| admin        | admin@care.local     | admin123  | Admin User     |
| receptionist | reception@care.local | re123     | Reception Desk |
| pharmacist   | pharma@care.local    | pharma123 | Pharmacy Desk  |
| support      | support@care.local   | support123| Support Desk   |
| finance      | finance@care.local   | finance123| Finance Desk   |

---

## Doctors

| email           | password  | full_name        | specialty          |
|-----------------|-----------|------------------|--------------------|
| dr1@care.local  | doctor123 | Meredith Grey    | General            |
| dr2@care.local  | doctor123 | Derek Shepherd   | Cardiology         |
| dr3@care.local  | doctor123 | Miranda Bailey   | Pediatrics         |
| dr4@care.local  | doctor123 | Cristina Yang    | Surgery            |
| dr5@care.local  | doctor123 | Arizona Robbins  | Orthopedics        |
| dr6@care.local  | doctor123 | Alex Karev       | Oncology           |
| dr7@care.local  | doctor123 | Amelia Shepherd  | Neurology          |
| dr8@care.local  | doctor123 | Mark Sloan       | Plastic Surgery    |
| dr9@care.local  | doctor123 | Izzie Stevens    | Dermatology        |
| dr10@care.local | doctor123 | George O'Malley  | Emergency          |
| dr11@care.local | doctor123 | April Kepner     | Trauma             |
| dr12@care.local | doctor123 | Jackson Avery    | ENT                |
| dr13@care.local | doctor123 | Callie Torres    | Orthopedics        |
| dr14@care.local | doctor123 | Teddy Altman     | Cardiothoracic     |
| dr15@care.local | doctor123 | Jo Wilson        | General            |
| dr16@care.local | doctor123 | Andrew DeLuca    | General            |

---

## Patients (1–30)

| email             | password   | full_name      |
|-------------------|------------|----------------|
| pt01@care.local   | patient123 | John Test      |
| pt02@care.local   | patient123 | Jane Test      |
| pt03@care.local   | patient123 | Michael Test   |
| pt04@care.local   | patient123 | Emily Test     |
| pt05@care.local   | patient123 | Daniel Test    |
| pt06@care.local   | patient123 | Sophia Test    |
| pt07@care.local   | patient123 | Liam Test      |
| pt08@care.local   | patient123 | Olivia Test    |
| pt09@care.local   | patient123 | Jack Test      |
| pt10@care.local  | patient123 | Ava Test       |
| pt11@care.local  | patient123 | Noah Test      |
| pt12@care.local  | patient123 | Mia Test       |
| pt13@care.local  | patient123 | Ethan Test     |
| pt14@care.local  | patient123 | Isla Test      |
| pt15@care.local  | patient123 | Lucas Test     |
| pt16@care.local  | patient123 | Amelia Test    |
| pt17@care.local  | patient123 | Harper Test    |
| pt18@care.local  | patient123 | Elijah Test    |
| pt19@care.local  | patient123 | Chloe Test     |
| pt20@care.local  | patient123 | Grace Test     |
| pt21@care.local  | patient123 | Oliver Test    |
| pt22@care.local  | patient123 | Ruby Test      |
| pt23@care.local  | patient123 | Max Test       |
| pt24@care.local  | patient123 | Zoe Test       |
| pt25@care.local  | patient123 | Henry Test     |
| pt26@care.local  | patient123 | Emma Test      |
| pt27@care.local  | patient123 | Leo Test       |
| pt28@care.local  | patient123 | Scarlett Test  |
| pt29@care.local  | patient123 | Aria Test      |
| pt30@care.local  | patient123 | Mason Test     |


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
