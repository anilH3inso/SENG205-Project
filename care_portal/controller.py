"""
controller.py
A minimal app controller for the Care Portal chatbot UI.

- Works immediately in Guest mode (no login).
- Provides login/logout methods you can wire to your real API.
- Supplies the attributes that helpdesk_chat.py expects:
    • root (Tk root)
    • current_user (object with id, full_name, email)
    • session_manager (SessionManagerContract with token + session_id)
    • on_chat_opened / on_chat_closed hooks (optional)

To wire up real auth later:
- Implement login() to call your backend, set self.session_manager.token,
  and populate self.current_user from the API response.
"""

from __future__ import annotations

import time
import typing as t
import requests

try:
    # If you have DB/models, imports are optional (not required to run guest)
    from care_portal.db import SessionLocal  # type: ignore
    from care_portal.models import User  # type: ignore
    _HAS_DB = True
except Exception:
    _HAS_DB = False
    SessionLocal = None  # type: ignore
    User = None          # type: ignore

from care_portal.ui.helpdesk_chat import SessionManagerContract


# ---- Configure these to match your backend (optional for guest mode) ----
API_BASE = "http://127.0.0.1:8001"
AUTH_LOGIN = f"{API_BASE}/auth/login"          # e.g., POST {username, password} -> {access_token, user}
AUTH_ME    = f"{API_BASE}/auth/me"             # e.g., GET Bearer -> {user}
TIMEOUT    = 10


class AppController:
    """
    Minimal controller the chatbot window expects.
    You can extend this to be your main app controller later.
    """

    def __init__(self, root):
        self.root = root

        # Start as guest (no token)
        self.session_manager = SessionManagerContract(token=None)

        # Minimal current_user object with required fields
        self.current_user = type("U", (), {
            "id": 0,
            "full_name": None,
            "email": None,
        })()

        # Optional: keep a basic in-memory state
        self._last_login_ts = None

    # ----------------- Hooks used by the chat window (optional) -----------------
    def on_chat_opened(self) -> None:
        print("[controller] Chat opened")

    def on_chat_closed(self) -> None:
        print("[controller] Chat closed")

    # ----------------- Convenience: is user logged in? --------------------------
    def is_logged_in(self) -> bool:
        return self.session_manager.is_logged_in()

    # ----------------- Set current user (local helper) --------------------------
    def set_current_user(self, *, user_id: int, full_name: t.Optional[str], email: t.Optional[str]) -> None:
        """Populate the minimal current_user object the chat header uses."""
        self.current_user.id = int(user_id or 0)
        self.current_user.full_name = full_name
        self.current_user.email = email

    # ----------------- Login / Logout (wire these later to your API) ------------
    def login(self, username: str, password: str) -> bool:
        """
        Example login flow:
        - POST to your AUTH_LOGIN endpoint
        - Save JWT to session_manager.token
        - Populate current_user (id, full_name, email)
        Return True on success, False otherwise.

        Works out of the box in "fake mode" (no server) by treating any
        non-empty username/password as a guest upgrade with a fake user.
        """
        try:
            # ---- Real API mode (uncomment when your API is ready) ----
            # resp = requests.post(AUTH_LOGIN, json={"username": username, "password": password}, timeout=TIMEOUT)
            # if resp.status_code != 200:
            #     return False
            # data = resp.json()
            # token = data.get("access_token")
            # user = data.get("user") or {}
            # if not token:
            #     return False
            # self.session_manager.token = token
            # self.set_current_user(
            #     user_id=user.get("id", 0),
            #     full_name=user.get("full_name") or user.get("name"),
            #     email=user.get("email"),
            # )
            # self._last_login_ts = time.time()
            # return True

            # ---- Fake/guest-friendly mode (works now without server) ----
            if not username or not password:
                return False
            self.session_manager.token = "guest-token-" + str(int(time.time()))
            # Try to hydrate from DB if available (optional)
            if _HAS_DB:
                try:
                    with SessionLocal() as db:  # type: ignore
                        # very naive lookup by email
                        u = db.query(User).filter(User.email == username).first()  # type: ignore
                        if u:
                            self.set_current_user(user_id=u.id, full_name=getattr(u, "full_name", None), email=u.email)
                        else:
                            self.set_current_user(user_id=1, full_name="Guest User", email=username)
                except Exception:
                    self.set_current_user(user_id=1, full_name="Guest User", email=username)
            else:
                self.set_current_user(user_id=1, full_name="Guest User", email=username)
            self._last_login_ts = time.time()
            return True

        except Exception as e:
            print("[controller] login error:", e)
            return False

    def hydrate_user_from_token(self) -> bool:
        """
        Optional: if you have a saved token on disk, or after app restart,
        call the /auth/me (or equivalent) endpoint to refresh user info.
        """
        if not self.session_manager.token:
            return False
        try:
            headers = {"Authorization": f"Bearer {self.session_manager.token}"}
            resp = requests.get(AUTH_ME, headers=headers, timeout=TIMEOUT)
            if resp.status_code != 200:
                return False
            user = resp.json() or {}
            self.set_current_user(
                user_id=user.get("id", 0),
                full_name=user.get("full_name") or user.get("name"),
                email=user.get("email"),
            )
            return True
        except Exception as e:
            print("[controller] hydrate error:", e)
            return False

    def logout(self) -> None:
        """
        Clear token, rotate chat session id, and revert to guest user.
        Call this from your global logout action.
        """
        self.session_manager.token = None
        # rotate the local session id so the chatbot starts fresh next time
        try:
            self.session_manager.rotate_session()
        except Exception:
            pass
        # revert to guest
        self.set_current_user(user_id=0, full_name=None, email=None)
        print("[controller] Logged out")

