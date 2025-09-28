from __future__ import annotations

import os
import signal
import traceback
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Dict

# Use the engine from db, but the Base from models (ensures all tables are registered)
from .db import engine
from .models import Role, User, Base

# UI frames
from .ui.login import LoginFrame
from .ui.patient import PatientFrame
from .ui.doctor import DoctorFrame

try:
    from .ui.receptionist import ReceptionistFrame  # optional
except Exception:  # pragma: no cover
    ReceptionistFrame = None  # type: ignore

try:
    from .ui.admin import AdminFrame  # optional
except Exception:  # pragma: no cover
    AdminFrame = None  # type: ignore

try:
    from .ui.pharmacist import PharmacistFrame  # optional
except Exception:  # pragma: no cover
    PharmacistFrame = None  # type: ignore

# new helpdesk import (safe)
try:
    from .ui.helpdesk_chat import HelpdeskChatFrame  # <- this must exist in ui/helpdesk_chat.py
except Exception:
    HelpdeskChatFrame = None

try:
    from .ui.support import SupportFrame  # optional
except Exception:  # pragma: no cover
    SupportFrame = None  # type: ignore

try:
    from .ui.finance import FinanceFrame  # optional
except Exception:  # pragma: no cover
    FinanceFrame = None  # type: ignore

# Window sizing helpers (provided in BaseFrame utilities)
from .ui.base import maximize_root, set_fixed_size


class App(tk.Tk):
    """
    Care Portal main application shell.
    - Initializes DB
    - Hosts all role dashboards (frames)
    - Tracks the current User session
    - Provides global Logout and graceful shutdown
    """
    def __init__(self):
        super().__init__()

        # ---- Window chrome
        self.title("Care Portal – Desktop")

        # Prefer maximized; allow override with env CARE_PORTAL_WINDOW
        win_mode = (os.getenv("CARE_PORTAL_WINDOW") or "max").lower()
        if win_mode.startswith("fix"):
            set_fixed_size(self, width=1280, height=800, lock=False)
        else:
            maximize_root(self, min_size=(1100, 720))

        # ---- Theme
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # ---- Ensure DB schema exists (models' Base so all tables are present)
        Base.metadata.create_all(bind=engine)

        # ---- Session
        self.current_user: Optional[User] = None

        # ---- Menu bar (global Logout, Exit, Help)
        self._build_menubar()

        # ---- Main container
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # ---- Create frames safely
        self.frames: Dict[str, tk.Frame] = {}
        self._add_frame(LoginFrame, container)
        self._add_frame(PatientFrame, container)
        self._add_frame(DoctorFrame, container)

        if ReceptionistFrame is not None:
            self._add_frame(ReceptionistFrame, container)
        if AdminFrame is not None:
            self._add_frame(AdminFrame, container)
        if PharmacistFrame is not None:
            self._add_frame(PharmacistFrame, container)
        # add helpdesk chat frame if available
        if HelpdeskChatFrame is not None:
            self._add_frame(HelpdeskChatFrame, container)

        if SupportFrame is not None:
            self._add_frame(SupportFrame, container)
        if FinanceFrame is not None:
            self._add_frame(FinanceFrame, container)

        # ---- Optional hooks
        for frame in self.frames.values():
            self._safe_call(frame, "on_app_ready")

        # ---- Shortcuts
        self.bind_all("<Control-l>", lambda _e: self.logout())
        self.bind_all("<Command-l>", lambda _e: self.logout())  # macOS

        # ---- Close button protocol
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- Start on Login
        self.show_frame("LoginFrame")

    # ========================= Menus & Window =========================

    def _build_menubar(self):
        menubar = tk.Menu(self)

        # Account menu
        m_account = tk.Menu(menubar, tearoff=False)
        m_account.add_command(label="Logout", command=self.logout, accelerator="Ctrl+L / ⌘L")
        m_account.add_separator()
        m_account.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="Account", menu=m_account)

        # Help menu
        m_help = tk.Menu(menubar, tearoff=False)
        m_help.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=m_help)

        self.config(menu=menubar)

    def _show_about(self):
        messagebox.showinfo(
            "About Care Portal",
            "Care Portal (Desktop)\nPython + Tkinter + SQL (SQLite/SQLAlchemy)\n© 2025"
        )

    def _on_close(self):
        try:
            self.destroy()
        except Exception:
            pass

    # ========================= Frame Utilities =========================

    def _add_frame(self, FrameCls, parent):
        try:
            frame = FrameCls(parent, self)
            name = FrameCls.__name__
            self.frames[name] = frame
            frame.grid(row=0, column=0, sticky="nsew")
        except Exception as e:
            print(f"[App] Skipping frame {getattr(FrameCls, '__name__', FrameCls)}: {e}")
            traceback.print_exc()

    def _safe_call(self, frame: tk.Frame, method_name: str, *args, **kwargs):
        if hasattr(frame, method_name):
            try:
                return getattr(frame, method_name)(*args, **kwargs)
            except Exception as e:
                print(f"[App] {frame.__class__.__name__}.{method_name} error:", e)
                traceback.print_exc()
        return None

    def show_frame(self, name: str) -> None:
        frame = self.frames.get(name)
        if frame is None:
            frame = self.frames.get("LoginFrame")
            if frame is None:
                raise RuntimeError("No frames available to show.")
            name = "LoginFrame"
        self._safe_call(frame, "on_show")
        frame.tkraise()

    # ========================= Session & Routing =========================

    def set_user(self, user: User) -> None:
        self.current_user = user
        # broadcast to frames (idempotent handlers)
        for frame in self.frames.values():
            self._safe_call(frame, "set_user", user)
            self._safe_call(frame, "refresh_data")
            self._safe_call(frame, "refresh_lists")
            self._safe_call(frame, "refresh_schedule")
            self._safe_call(frame, "refresh_doctors")
        self.show_frame(self._route_for_role(user.role))

    def _route_for_role(self, role: Role | str) -> str:
        rv = getattr(role, "value", role)
        mapping = {
            "patient": "PatientFrame",
            "doctor": "DoctorFrame",
            "receptionist": "ReceptionistFrame",
            "admin": "AdminFrame",
            "pharmacist": "PharmacistFrame",
            "support": "SupportFrame",
            "finance": "FinanceFrame",
        }
        target = mapping.get(rv, "LoginFrame")
        if target not in self.frames:
            target = "LoginFrame"
        return target

    def logout(self) -> None:
        self.current_user = None
        for frame in self.frames.values():
            self._safe_call(frame, "set_user", None)
            self._safe_call(frame, "on_logout")
        self.show_frame("LoginFrame")


# ========================= Main (graceful exit) =========================

def _run():
    app = App()

    def _graceful_quit(*_):
        try:
            app.after(0, app.destroy)
        finally:
            os._exit(0)

    try:
        signal.signal(signal.SIGINT, _graceful_quit)
    except Exception:
        pass

    try:
        app.mainloop()
    except KeyboardInterrupt:
        _graceful_quit()


if __name__ == "__main__":
    _run()
