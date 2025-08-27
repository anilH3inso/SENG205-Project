# care_portal/ui/base.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional


class BaseFrame(ttk.Frame):
    title: str = ""

    def __init__(self, parent, controller):
        super().__init__(parent)
        self.controller = controller

        # ---- Header (title + user badge + logout) ----
        header = ttk.Frame(self)
        header.pack(fill="x", pady=6)

        self.title_lbl = ttk.Label(
            header, text=self.title or "Care Portal", font=("Segoe UI", 16, "bold")
        )
        self.title_lbl.pack(side="left")

        right = ttk.Frame(header)
        right.pack(side="right")

        self.user_lbl = ttk.Label(right, text="", foreground="#666")
        self.user_lbl.pack(side="left", padx=(0, 8))

        self.logout_btn = ttk.Button(right, text="Logout", command=self._on_logout)
        self.logout_btn.pack(side="left")

        ttk.Separator(self).pack(fill="x", pady=(0, 8))

    # Called by App.set_user(user) after login/logout
    def set_user(self, user: Optional[object]):
        self._refresh_user_badge(user)

    # Called by App.show_frame(name) whenever this frame is shown
    def on_show(self):
        self._refresh_user_badge(getattr(self.controller, "current_user", None))

    def set_title(self, text: str):
        self.title = text
        self.title_lbl.config(text=text or "Care Portal")

    # -------- helpers --------
    def _on_logout(self):
        try:
            self.controller.logout()
        except Exception as e:
            print("logout error:", e)

    def _refresh_user_badge(self, user: Optional[object]):
        if not user:
            self.user_lbl.config(text="")
            return
        # Safe reads for both Enum and plain string roles
        name = getattr(user, "full_name", "") or getattr(user, "email", "user")
        role_val = getattr(getattr(user, "role", None), "value", getattr(user, "role", None)) or ""
        self.user_lbl.config(text=f"Logged in as: {name} ({role_val})")
