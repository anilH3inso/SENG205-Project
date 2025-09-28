from __future__ import annotations

import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext
import requests
from typing import Optional

# NEW: pull patient id locally when possible
try:
    from ..db import SessionLocal
    from ..models import Patient
    _HAS_DB = True
except Exception:
    _HAS_DB = False

API_URL = "http://127.0.0.1:8001/ai/chat"
STREAM_URL = "http://127.0.0.1:8001/ai/stream"
API_TIMEOUT = 15
STREAM_TIMEOUT = 60
SLOT_HINT = "…thinking…"

# -------------------------------
# Public entrypoint
# -------------------------------
def open_helpdesk(controller: object, master: Optional[tk.Misc] = None) -> "HelpdeskChat":
    """
    Open (or focus) the singleton Helpdesk chat window.
    Call this from your app when the user clicks 'Help' or similar.
    """
    return HelpdeskChat.show(controller, master)


class AIAdapter:
    def ask(self, question: str, context: dict | None = None) -> str:
        try:
            r = requests.post(API_URL, json={"message": question, "context": context or {}}, timeout=API_TIMEOUT)
            r.raise_for_status()
            return r.json().get("answer", "Sorry, no answer found.")
        except Exception as e:
            return f"Error contacting AI: {e}"

    def stream(self, question: str, context: dict | None,
               on_start=None, on_token=None, on_end=None) -> bool:
        try:
            with requests.post(
                STREAM_URL,
                json={"message": question, "context": context or {}},
                stream=True,
                timeout=STREAM_TIMEOUT,
            ) as r:
                r.raise_for_status()
                for raw in r.iter_lines(decode_unicode=True):
                    if not raw or not raw.startswith("data:"):
                        continue
                    evt = json.loads(raw[5:])
                    typ = evt.get("type")
                    if typ == "start":
                        if on_start: on_start()
                    elif typ == "token":
                        if on_token: on_token(evt.get("text", ""))
                    elif typ == "end":
                        if on_end: on_end()
                        return True
                return False
        except Exception:
            return False


class HelpdeskChat:
    """
    Singleton Helpdesk chat window.
    - Opens once; subsequent calls focus & raise the existing window.
    - Thread-safe UI updates via _enqueue() + _poll_uiq()
    """
    _instance: Optional["HelpdeskChat"] = None
    WIDTH = 520
    HEIGHT = 520
    MARGIN = 16
    STICKY_BOTTOM_OFFSET = 40

    @classmethod
    def show(cls, controller: object, master: Optional[tk.Misc] = None) -> "HelpdeskChat":
        if cls._instance and cls._instance._is_alive():
            inst = cls._instance
            inst._dock_bottom_right()
            try:
                inst.win.deiconify()
                inst.win.lift()
                inst.win.focus_force()
            except Exception:
                pass
            return inst

        inst = HelpdeskChat(controller, master)
        cls._instance = inst
        inst._dock_bottom_right()
        return inst

    def __init__(self, controller: object, master: Optional[tk.Misc] = None):
        self.controller = controller
        self.master = master or (controller.root if hasattr(controller, "root") else None)

        self.win = tk.Toplevel(self.master) if self.master else tk.Toplevel()
        self.win.title("Helpdesk Chat")
        self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        # ------------- UI -------------
        root = ttk.Frame(self.win, padding=8)
        root.pack(fill="both", expand=True)

        self.chat_box = scrolledtext.ScrolledText(root, height=20, wrap="word", state="disabled")
        self.chat_box.pack(fill="both", expand=True)

        row = ttk.Frame(root)
        row.pack(fill="x", pady=(8, 0))
        self.entry = ttk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_send = ttk.Button(row, text="Send", command=self._send)
        self.btn_send.pack(side="left")
        self.entry.bind("<Return>", lambda _e: self._send())

        self.ai = AIAdapter()

        self._uiq: "queue.Queue[tuple[callable, tuple]]" = queue.Queue()
        self._poll_uiq()

        try:
            user = getattr(self.controller, "current_user", None)
            name = getattr(user, "full_name", None) or getattr(user, "email", None)
        except Exception:
            name = None
        if name:
            self._append("bot", f"Hi {name}, how can I help you today?")
        else:
            self._append("bot", "Hi! How can I help you today?")

        self.win.update_idletasks()
        self._dock_bottom_right()

    # -------------------------------
    # Placement helpers
    # -------------------------------
    def _dock_bottom_right(self):
        try:
            self.win.update_idletasks()
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            w = self.WIDTH; h = self.HEIGHT
            x = max(self.MARGIN, sw - w - self.MARGIN)
            y = max(self.MARGIN, sh - h - self.MARGIN - self.STICKY_BOTTOM_OFFSET)
            self.win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            try:
                self.win.geometry(f"+{self.MARGIN}+{self.MARGIN}")
            except Exception:
                pass

    def _is_alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False

    def _on_close(self):
        try:
            self.win.destroy()
        finally:
            if HelpdeskChat._instance is self:
                HelpdeskChat._instance = None

    # -------------------------------
    # Thread-safe UI queue
    # -------------------------------
    def _enqueue(self, fn, *args):
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
        self.win.after(30, self._poll_uiq)

    # -------------------------------
    # Chat UI helpers
    # -------------------------------
    def _append(self, who, text):
        self.chat_box.config(state="normal")
        prefix = "You: " if who == "user" else "Bot: "
        self.chat_box.insert("end", f"{prefix}{text}\n\n")
        self.chat_box.see("end")
        self.chat_box.config(state="disabled")

    def _replace_last_line_with(self, prefix_text: str):
        self.chat_box.config(state="normal")
        try:
            self.chat_box.delete("end-3l", "end")
        except Exception:
            pass
        self.chat_box.insert("end", prefix_text)
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

    # -------------------------------
    # Send/stream logic
    # -------------------------------
    def _send(self):
        q = self.entry.get().strip()
        if not q:
            return
        self.entry.delete(0, "end")
        self._append("user", q)

        # Build context (include patient_id if we can derive it quickly)
        user_id = getattr(getattr(self.controller, "current_user", None), "id", 0)
        ctx = {"user_id": user_id}

        if _HAS_DB and user_id:
            try:
                with SessionLocal() as db:
                    pid = db.scalar(
                        # simple one-to-one: Patient.user_id == user_id
                        # (works even if there are multiple, we pick the first)
                        db.query(Patient.id).filter(Patient.user_id == user_id).statement
                    )
                if pid:
                    ctx["patient_id"] = int(pid)
            except Exception:
                pass

        self._append("bot", SLOT_HINT)

        try:
            self.btn_send.config(state="disabled")
        except Exception:
            pass

        def worker():
            ok = self.ai.stream(
                q, ctx,
                on_start=lambda: self._enqueue(self._replace_last_line_with, "Bot: "),
                on_token=lambda t: self._enqueue(self._append_token, t),
                on_end=lambda: self._enqueue(self._finalize_stream_line),
            )
            if not ok:
                ans = self.ai.ask(q, ctx)
                self._enqueue(self._replace_last_line_with, f"Bot: {ans}\n\n")
            self._enqueue(lambda: self.btn_send.config(state="normal"))

        threading.Thread(target=worker, daemon=True).start()
