# care_portal/ui/helpdesk_chat.py
from __future__ import annotations

import json
import threading
import queue
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Configuration (override via env or your controller at runtime)
# ─────────────────────────────────────────────────────────────────────────────
API_BASE = "http://127.0.0.1:8001"   # your ai_server host:port
API_CHAT = f"{API_BASE}/ai/chat"
API_STREAM = f"{API_BASE}/ai/stream"
API_SESSION_RESET = f"{API_BASE}/chat/session/reset"  # optional; safe if missing

API_TIMEOUT = 15          # seconds (non-stream)
STREAM_TIMEOUT = 90       # seconds (stream)
UI_POLL_MS = 25           # UI queue polling interval
IDLE_TIMEOUT_MIN = 0      # 0 = disabled; else minutes of inactivity to auto-end

BOT_THINKING = "…thinking…"
UI_TITLE = "Care Portal • Assistant"
UNIV_FONT = ("Arial", 11)  # “universal” safe system font

# ─────────────────────────────────────────────────────────────────────────────
# Optional DB quick patient lookup (best-effort)
# ─────────────────────────────────────────────────────────────────────────────
try:
    from ..db import SessionLocal
    from ..models import Patient
    _HAS_DB = True
except Exception:
    _HAS_DB = False
    SessionLocal = None  # type: ignore
    Patient = None       # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Controller contract (what this file expects from your app controller)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class SessionManagerContract:
    """
    Minimal interface your main controller should provide.
    If you already have one, just ensure these attributes/methods exist.
    """
    token: Optional[str] = None        # Bearer JWT (string)
    session_id: str = "sess-local"     # unique per-login (regenerated on login)

    def is_logged_in(self) -> bool:
        return bool(self.token)

    # called by UI when closing chat or on logout to reset/rotate session id
    def rotate_session(self):
        # Implement in your main app; here’s a safe default
        self.session_id = f"sess-{int(time.time())}"
        # Don’t clear token here (that’s done on real logout)


class _FallbackController:
    """
    If you open this UI without a real controller, we still run for dev/testing.
    """
    def __init__(self):
        self.root = None
        self.current_user = type("U", (), {"id": 0, "full_name": None, "email": None})()
        self.session_manager = SessionManagerContract(token=None)

    def on_chat_closed(self):
        pass  # optional hook

    def on_chat_opened(self):
        pass  # optional hook


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Client with optional Bearer token + session metadata
# ─────────────────────────────────────────────────────────────────────────────
class AIHttp:
    def __init__(self, token_getter: Callable[[], Optional[str]]):
        self._get_token = token_getter

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        tok = self._get_token()
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        return h

    def ask(self, message: str, user_id: int, session_id: str, extra_ctx: Dict[str, Any]) -> requests.Response:
        payload = {
            "message": message,
            "user_id": user_id or 0,
            "session_id": session_id,
            "context": extra_ctx,
            "allow_tools": True
        }
        return requests.post(
            API_CHAT,
            headers=self._headers(),
            json=payload,
            timeout=API_TIMEOUT,
        )

    def stream(self, message: str, user_id: int, session_id: str, extra_ctx: Dict[str, Any]):
        payload = {
            "message": message,
            "user_id": user_id or 0,
            "session_id": session_id,
            "context": extra_ctx,
            "allow_tools": True
        }
        return requests.post(
            API_STREAM,
            headers=self._headers(),
            json=payload,
            timeout=STREAM_TIMEOUT,
            stream=True,
        )

    def reset_session(self) -> None:
        # Optional. Ignore failures silently (endpoint may not exist).
        try:
            requests.post(API_SESSION_RESET, headers=self._headers(), timeout=5)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Chat Window (Singleton)
# ─────────────────────────────────────────────────────────────────────────────
class HelpdeskChat:
    """
    Singleton chat:
      - Second invocation just focuses the existing window.
      - Ends/cleans session on close.
      - Reacts to login/logout via controller/session_manager.
    """
    _instance: Optional["HelpdeskChat"] = None

    WIDTH = 560
    HEIGHT = 560
    MARGIN = 16
    STICKY_BOTTOM_OFFSET = 40

    @classmethod
    def show(cls, controller: Optional[object] = None, master: Optional[tk.Misc] = None) -> "HelpdeskChat":
        if cls._instance and cls._instance._alive():
            inst = cls._instance
            inst._dock()
            inst._focus()
            return inst
        inst = HelpdeskChat(controller, master)
        cls._instance = inst
        inst._dock()
        return inst

    @classmethod
    def close_if_open(cls):
        if cls._instance:
            try:
                cls._instance._close()
            except Exception:
                pass
            cls._instance = None

    def __init__(self, controller: Optional[object] = None, master: Optional[tk.Misc] = None):
        # Controller contract
        self.controller = controller or _FallbackController()
        self.session_mgr: SessionManagerContract = getattr(self.controller, "session_manager", SessionManagerContract())
        self.current_user = getattr(self.controller, "current_user", type("U", (), {"id": 0, "full_name": None, "email": None})())

        # Tk window
        base = getattr(self.controller, "root", None)
        self.win = tk.Toplevel(base if isinstance(base, tk.Misc) else None)
        self.win.title(UI_TITLE)
        try:
            self.win.iconname(UI_TITLE)
        except Exception:
            pass
        self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self.win.bind("<Escape>", lambda _e: self._on_close())

        # Root frame
        root = ttk.Frame(self.win, padding=8)
        root.pack(fill="both", expand=True)

        # Header (session/identity line)
        self.lbl_top = ttk.Label(root, text=self._header_text(), font=(UNIV_FONT[0], 10))
        self.lbl_top.pack(fill="x", pady=(0, 6))

        # Chat box
        self.chat_box = scrolledtext.ScrolledText(root, height=20, wrap="word", state="disabled")
        self.chat_box.configure(font=UNIV_FONT)
        self.chat_box.pack(fill="both", expand=True)

        # Input row
        row = ttk.Frame(root)
        row.pack(fill="x", pady=(8, 0))

        self.entry = ttk.Entry(row, font=UNIV_FONT)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.entry.bind("<Return>", lambda _e: self._send())

        self.btn_send = ttk.Button(row, text="Send", command=self._send)
        self.btn_send.pack(side="left")

        # Idle-timeout (local) — optional
        self._idle_deadline_ts = 0.0
        if IDLE_TIMEOUT_MIN > 0:
            self._bump_idle_deadline()
            self.win.bind_all("<Key>", self._bump_idle_deadline_evt, add="+")
            self.win.bind_all("<Button>", self._bump_idle_deadline_evt, add="+")
            self._tick_idle_watchdog()

        # Networking client
        self.ai = AIHttp(lambda: getattr(self.session_mgr, "token", None))

        # UI thread queue
        self._uiq: "queue.Queue[tuple[Callable[..., None], tuple]]" = queue.Queue()
        self._poll_uiq()

        # Greeting
        name = getattr(self.current_user, "full_name", None) or getattr(self.current_user, "email", None)
        self._append("bot", f"Hi {name}, how can I help you today?" if name else "Hi! How can I help you today?")

        # Hook to controller
        if hasattr(self.controller, "on_chat_opened"):
            try:
                self.controller.on_chat_opened()
            except Exception:
                pass

        # Focus into entry
        self.win.after(10, lambda: self.entry.focus_set())

    # ── UI helpers ──────────────────────────────────────────────────────────
    def _header_text(self) -> str:
        uid = getattr(self.current_user, "id", 0) or 0
        who = getattr(self.current_user, "full_name", None) or getattr(self.current_user, "email", None) or f"User#{uid}"
        sid = getattr(self.session_mgr, "session_id", "sess")
        st = "Logged in" if self.session_mgr.is_logged_in() else "Guest"
        return f"{who} • {st} • session: {sid}"

    def _alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _dock(self):
        try:
            self.win.update_idletasks()
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            x = max(self.MARGIN, sw - self.WIDTH - self.MARGIN)
            y = max(self.MARGIN, sh - self.HEIGHT - self.MARGIN - self.STICKY_BOTTOM_OFFSET)
            self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        except Exception:
            try:
                self.win.geometry(f"+{self.MARGIN}+{self.MARGIN}")
            except Exception:
                pass

    def _focus(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self.entry.focus_set()
        except Exception:
            pass

    def _on_close(self):
        self._close()

    def _close(self):
        # Attempt to reset server-side session (optional)
        threading.Thread(target=self.ai.reset_session, daemon=True).start()
        # Rotate (local) session id for next open
        try:
            self.session_mgr.rotate_session()
        except Exception:
            pass
        # Inform controller
        if hasattr(self.controller, "on_chat_closed"):
            try:
                self.controller.on_chat_closed()
            except Exception:
                pass
        # Destroy window
        try:
            self.win.destroy()
        except Exception:
            pass
        # Clear singleton
        if HelpdeskChat._instance is self:
            HelpdeskChat._instance = None

    # ── Idle timeout (optional) ─────────────────────────────────────────────
    def _bump_idle_deadline(self):
        self._idle_deadline_ts = time.time() + (IDLE_TIMEOUT_MIN * 60)

    def _bump_idle_deadline_evt(self, _e=None):
        if IDLE_TIMEOUT_MIN > 0:
            self._bump_idle_deadline()

    def _tick_idle_watchdog(self):
        try:
            if IDLE_TIMEOUT_MIN > 0 and time.time() > self._idle_deadline_ts:
                self._append("bot", "(Session ended due to inactivity)")
                self._close()
                return
        except Exception:
            pass
        self.win.after(2_000, self._tick_idle_watchdog)

    # ── Thread-safe UI queue ────────────────────────────────────────────────
    def _enqueue(self, fn: Callable[..., None], *args):
        self._uiq.put((fn, args))

    def _poll_uiq(self):
        try:
            while True:
                fn, args = self._uiq.get_nowait()
                try:
                    fn(*args)
                except Exception as e:
                    print("UI update error:", e)
        except queue.Empty:
            pass
        self.win.after(UI_POLL_MS, self._poll_uiq)

    # ── Chat text helpers ───────────────────────────────────────────────────
    def _append(self, who: str, text: str):
        self.chat_box.config(state="normal")
        prefix = "You: " if who == "user" else "Bot: "
        self.chat_box.insert("end", f"{prefix}{text}\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def _replace_last_line(self, full_text: str):
        self.chat_box.config(state="normal")
        try:
            # remove the last two newlines + previous line (added by _append)
            self.chat_box.delete("end-3l", "end")
        except Exception:
            pass
        self.chat_box.insert("end", full_text)
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def _append_token(self, token: str):
        self.chat_box.config(state="normal")
        self.chat_box.insert("end", token)
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def _finalize_stream_line(self):
        self.chat_box.config(state="normal")
        self.chat_box.insert("end", "\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    # ── Patient context helper (best-effort) ────────────────────────────────
    def _quick_patient_id(self, user_id: int) -> Optional[int]:
        if not _HAS_DB or not user_id:
            return None
        try:
            with SessionLocal() as db:  # type: ignore
                pid = db.query(Patient.id).filter(Patient.user_id == int(user_id)).scalar()  # type: ignore
                return int(pid) if pid else None
        except Exception:
            return None

    # ── Send/Stream logic ───────────────────────────────────────────────────
    def _send(self):
        q = self.entry.get().strip()
        if not q:
            return

        # If not logged in, still allow but warn once (non-blocking UX)
        if not self.session_mgr.is_logged_in():
            # Only print a gentle inline reminder instead of modal
            self._append("bot", "⚠️ You are chatting as a guest. Log in for personalized answers.")

        self.entry.delete(0, "end")
        self._append("user", q)
        # add a placeholder that will be replaced on stream start
        self._append("bot", BOT_THINKING)

        # bump idle timer
        self._bump_idle_deadline_evt()

        # Build context
        user_id = getattr(self.current_user, "id", 0) or 0
        session_id = getattr(self.session_mgr, "session_id", "sess-local")
        ctx: Dict[str, Any] = {"user_id": user_id}
        quick_pid = self._quick_patient_id(user_id)
        if quick_pid:
            ctx["patient_id"] = quick_pid

        # disable send during request
        try:
            self.btn_send.config(state="disabled")
        except Exception:
            pass

        def worker():
            # 1) Try streaming first
            try:
                with self.ai.stream(q, user_id, session_id, ctx) as r:
                    r.raise_for_status()
                    # SSE loop
                    started = False
                    for raw in r.iter_lines(decode_unicode=True):
                        if not raw:
                            continue
                        if not raw.startswith("data:"):
                            continue
                        evt = json.loads(raw[5:])
                        typ = evt.get("type")
                        if typ == "start":
                            started = True
                            self._enqueue(self._replace_last_line, "Bot: ")
                        elif typ == "token":
                            self._enqueue(self._append_token, evt.get("text", ""))
                        elif typ == "end":
                            self._enqueue(self._finalize_stream_line)
                            break
                        # tiny yield to keep UI responsive on heavy streams
                        time.sleep(0.001)
                    if not started:
                        # If server didn’t send SSE start, fallback to non-streaming
                        raise RuntimeError("SSE did not start; falling back")
            except requests.HTTPError as http_err:
                status_code = getattr(http_err.response, "status_code", 0)
                if status_code == 401:
                    self._enqueue(self._replace_last_line, "Bot: (Session expired. Please log in again.)\n\n")
                else:
                    self._enqueue(self._replace_last_line, f"Bot: (HTTP {status_code} error contacting AI)\n\n")
            except Exception:
                # 2) Fallback to non-streaming /ai/chat
                try:
                    resp = self.ai.ask(q, user_id, session_id, ctx)
                    if resp.status_code == 401:
                        self._enqueue(self._replace_last_line, "Bot: (Session expired. Please log in again.)\n\n")
                    else:
                        resp.raise_for_status()
                        ans = resp.json().get("answer", "Sorry, no answer found.")
                        self._enqueue(self._replace_last_line, f"Bot: {ans}\n\n")
                except requests.HTTPError as http_err2:
                    sc = getattr(http_err2.response, "status_code", 0)
                    self._enqueue(self._replace_last_line, f"Bot: (HTTP {sc} error contacting AI)\n\n")
                except Exception as e2:
                    self._enqueue(self._replace_last_line, f"Bot: (Network error: {e2})\n\n")
            finally:
                # Re-enable send
                self._enqueue(lambda: self.btn_send.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint used by the rest of the app
# ─────────────────────────────────────────────────────────────────────────────
def open_helpdesk(controller: Optional[object] = None, master: Optional[tk.Misc] = None) -> HelpdeskChat:
    """
    Call this from your app (menu button / toolbar) to show the chat.
    - On repeated calls it **focuses** the existing window (no duplicates).
    - `controller` is your main app controller providing:
        • controller.root -> Tk root
        • controller.current_user -> object with `id`, `full_name`/`email`
        • controller.session_manager -> SessionManagerContract (token, session_id, rotate_session)
        • controller.on_chat_opened() [optional]
        • controller.on_chat_closed() [optional]
    """
    return HelpdeskChat.show(controller, master)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for your app to hook login/logout events (optional)
# ─────────────────────────────────────────────────────────────────────────────
def on_user_logged_out():
    """
    Call this from your global logout handler.
    Ensures any open chat is closed and session rotated.
    """
    HelpdeskChat.close_if_open()


def on_user_logged_in(controller: object):
    """
    Call this from your login success handler.
    Not required, but if you want to auto-open chat or update state,
    you can call `open_helpdesk(controller)`.
    """
    # Example (commented):
    # open_helpdesk(controller)
    pass
