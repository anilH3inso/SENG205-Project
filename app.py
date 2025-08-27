# care_portal/app.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional, Dict

from .db import Base, engine
from .models import Role, User

# UI frames (some may not exist in your project; we guard their creation)
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


class App(tk.Tk):
    """
    Main Tk application shell for Care Portal.
    - Creates the SQLite tables on startup.
    - Hosts all Frames and handles navigation.
    - Stores the current logged-in User (self.current_user).
    - Provides logout() and role-based routing.
    """
    def __init__(self):
        super().__init__()
        self.title("Care Portal – Desktop")
        self.geometry("1100x700")

        # Theme
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Ensure tables exist
        Base.metadata.create_all(bind=engine)

        # Current session user
        self.current_user: Optional[User] = None

        # Main container
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # Build frames safely
        self.frames: Dict[str, tk.Frame] = {}
        self._add_frame(LoginFrame, container)
        self._add_frame(PatientFrame, container)
        self._add_frame(DoctorFrame, container)
        if ReceptionistFrame is not None:
            self._add_frame(ReceptionistFrame, container)
        if AdminFrame is not None:
            self._add_frame(AdminFrame, container)

        # Start at Login
        self.show_frame("LoginFrame")

    # ------------- Frame helpers -------------

    def _add_frame(self, FrameCls, parent):
        """Create a frame instance, register it, and grid it. Skip if it fails."""
        try:
            frame = FrameCls(parent, self)
            name = FrameCls.__name__
            self.frames[name] = frame
            frame.grid(row=0, column=0, sticky="nsew")
        except Exception as e:
            # It's OK if some dashboards are not implemented yet.
            print(f"[App] Skipping frame {getattr(FrameCls, '__name__', FrameCls)}: {e}")

    def show_frame(self, name: str) -> None:
        """Raise a frame by name and call its on_show() hook if present."""
        frame = self.frames[name]
        # Notify the frame it is being shown (refresh user badge/data, etc.)
        if hasattr(frame, "on_show"):
            try:
                frame.on_show()
            except Exception as e:
                print("[App] on_show error in", name, "->", e)
        frame.tkraise()

    # ------------- Session / routing -------------

    def set_user(self, user: User) -> None:
        """
        Called by LoginFrame after successful authentication.
        Sets the current user, informs frames that care, then routes by role.
        """
        self.current_user = user

        # Let frames that support set_user() receive the new user
        for frame in self.frames.values():
            if hasattr(frame, "set_user"):
                try:
                    frame.set_user(user)
                except Exception as e:
                    print("[App] set_user error in", type(frame).__name__, "->", e)

        # Route to the correct dashboard
        target = self._route_for_role(user.role)
        self.show_frame(target)

    def _route_for_role(self, role: Role | str) -> str:
        """Map a role to a frame name."""
        rv = getattr(role, "value", role)
        mapping = {
            "patient": "PatientFrame",
            "doctor": "DoctorFrame",
            "receptionist": "ReceptionistFrame",
            "admin": "AdminFrame",
        }
        # Fallback to login if target frame not present
        target = mapping.get(rv, "LoginFrame")
        if target not in self.frames:
            target = "LoginFrame"
        return target

    def logout(self) -> None:
        """
        Clear the current session and return to Login.
        Frames can call this via their Logout button.
        """
        self.current_user = None
        # Optionally notify frames that user is gone
        for frame in self.frames.values():
            if hasattr(frame, "set_user"):
                try:
                    frame.set_user(None)  # type: ignore
                except Exception:
                    pass
        self.show_frame("LoginFrame")


if __name__ == "__main__":
    App().mainloop()
