from tkinter import ttk
from .base import BaseFrame


class ReceptionistFrame(BaseFrame):
    title = "Receptionist Dashboard"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        ttk.Label(
            self,
            text="(Stub) Manage bookings & check-ins here",
            font=("Segoe UI", 12)
        ).pack(pady=20)
