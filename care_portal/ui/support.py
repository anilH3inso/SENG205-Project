# care_portal/ui/support.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from ..db import SessionLocal
from ..models import SupportTicket, TicketStatus, User
from .base import BaseFrame

# NEW: ticket notification helpers
from ..services.notifications import (
    notify_ticket_reply_to_user,
    notify_ticket_status_update,
)


class SupportFrame(BaseFrame):
    title = "Support"

    def __init__(self, parent, controller):
        super().__init__(parent, controller)

        # Toolbar
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(6, 6))

        ttk.Button(bar, text="Refresh", command=self.refresh_data).pack(side="left")

        ttk.Button(bar, text="Assign to Me", command=self.assign_to_me).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Set Status", command=self.set_status).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Add Note", command=self.add_note).pack(side="left", padx=(8, 0))

        ttk.Label(bar, text="Filter:").pack(side="left", padx=(16, 4))
        self.cmb_filter = ttk.Combobox(
            bar, state="readonly",
            values=["(any)", "open", "in_progress", "resolved", "closed"], width=14
        )
        self.cmb_filter.set("(any)")
        self.cmb_filter.pack(side="left")
        ttk.Button(bar, text="Apply", command=self.refresh_data).pack(side="left", padx=(6, 0))

        # Table
        cols = ("id", "created", "subject", "from_user", "assigned_to", "status", "last_update")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings", height=18)
        for c, txt, w in [
            ("id", "Id", 70),
            ("created", "Created", 140),
            ("subject", "Subject", 260),
            ("from_user", "From_User", 200),
            ("assigned_to", "Assigned_To", 180),
            ("status", "Status", 120),
            ("last_update", "Last_Update", 140),
        ]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w", stretch=(c in {"subject", "from_user", "assigned_to"}))

        wrap = self.attach_tree_scrollbars(self.body, self.tree)
        wrap.pack(fill="both", expand=True)

        self.style_treeview(self.tree)

        # Ticket details
        ttk.Label(self.body, text="Ticket Details / Notes").pack(anchor="w", pady=(6, 2))
        self.txt_details = tk.Text(self.body, height=5, wrap="word")
        self.txt_details.pack(fill="both", expand=False)
        self.txt_details.configure(state="disabled")

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_details())

        self.status_lbl = ttk.Label(self.body, text="0 ticket(s) listed")
        self.status_lbl.pack(anchor="w", pady=(4, 0))

    # ---------- utils ----------
    @staticmethod
    def _fmt_dt(dt: datetime | None) -> str:
        return dt.strftime("%Y-%m-%d %H:%M") if isinstance(dt, datetime) else ""

    def _selected_id(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(self.tree.item(sel[0], "values")[0])
        except Exception:
            return None

    # ---------- hooks ----------
    def on_show(self):
        self.refresh_data()

    # ---------- actions ----------
    def refresh_data(self):
        try:
            self.tree.delete(*self.tree.get_children())
            self.txt_details.configure(state="normal")
            self.txt_details.delete("1.0", "end")
            self.txt_details.configure(state="disabled")

            with SessionLocal() as db:
                stmt = select(SupportTicket).order_by(SupportTicket.created_at.desc())
                f = self.cmb_filter.get()
                if f and f != "(any)":
                    try:
                        status = TicketStatus(f)
                        stmt = stmt.where(SupportTicket.status == status)
                    except Exception:
                        pass
                tickets = db.scalars(stmt).all()

                n = 0
                for t in tickets:
                    author = getattr(t, "author", None) or t.user  # back-compat alias -> author
                    author_name = author.full_name if author else ""
                    assignee = t.assignee.full_name if t.assignee else ""
                    last_upd = t.updated_at or t.created_at
                    self.tree.insert(
                        "",
                        "end",
                        values=(
                            t.id,
                            self._fmt_dt(t.created_at),
                            t.subject or "",
                            author_name or "",
                            assignee or "",
                            getattr(t.status, "value", t.status) if t.status else "",
                            self._fmt_dt(last_upd),
                        ),
                    )
                    n += 1
                self.status_lbl.config(text=f"{n} ticket(s) listed")
        except SQLAlchemyError as e:
            messagebox.showerror("Support", f"Load failed: {e}")
        except Exception as e:
            messagebox.showerror("Support", f"Load failed: {e}")

    def _load_details(self):
        tid = self._selected_id()
        self.txt_details.configure(state="normal")
        self.txt_details.delete("1.0", "end")
        if not tid:
            self.txt_details.configure(state="disabled")
            return
        with SessionLocal() as db:
            t = db.get(SupportTicket, tid)
            if not t:
                self.txt_details.configure(state="disabled")
                return
            details = f"Subject: {t.subject}\n\n{t.body or ''}\n\nNotes:\n{t.notes or ''}"
            self.txt_details.insert("1.0", details)
            self.txt_details.configure(state="disabled")

    def assign_to_me(self):
        if not self.current_user:
            messagebox.showerror("Assign", "No logged-in user.")
            return
        tid = self._selected_id()
        if not tid:
            messagebox.showinfo("Assign", "Select a ticket first.")
            return
        with SessionLocal() as db:
            try:
                t = db.get(SupportTicket, tid)
                if not t:
                    return
                t.assignee_id = self.current_user.id
                t.updated_at = datetime.utcnow()
                db.commit()
                self.refresh_data()
            except SQLAlchemyError as e:
                db.rollback()
                messagebox.showerror("Assign", str(e))

    def set_status(self):
        tid = self._selected_id()
        if not tid:
            messagebox.showinfo("Status", "Select a ticket first.")
            return

        # Get current status to preselect
        current_val = None
        with SessionLocal() as db:
            t = db.get(SupportTicket, tid)
            if t and t.status:
                current_val = getattr(t.status, "value", str(t.status))

        choices = [s.value for s in TicketStatus]
        dlg = tk.Toplevel(self)
        dlg.title("Set Status")
        ttk.Label(dlg, text="Status:").pack(side="left", padx=8, pady=8)
        cmb = ttk.Combobox(dlg, values=choices, state="readonly")
        cmb.pack(side="left", padx=8, pady=8)
        cmb.set(current_val or choices[0])

        def _ok():
            val = cmb.get()
            with SessionLocal() as db:
                try:
                    t = db.get(SupportTicket, tid)
                    if t:
                        t.status = TicketStatus(val)
                        t.updated_at = datetime.utcnow()
                        db.commit()
                        # Notify ticket owner about status change
                        try:
                            notify_ticket_status_update(t.id, t.status, updater_user_id=getattr(self.current_user, "id", None), db=db)
                        except Exception:
                            # Fail-safe: ignore notify errors to not block UI
                            pass
                except SQLAlchemyError:
                    db.rollback()
            dlg.destroy()
            self.refresh_data()

        ttk.Button(dlg, text="OK", command=_ok).pack(side="left", padx=8, pady=8)

    def add_note(self):
        tid = self._selected_id()
        if not tid:
            messagebox.showinfo("Note", "Select a ticket first.")
            return
        note = simpledialog.askstring("Add Note", "Enter note text:")
        if not note:
            return
        with SessionLocal() as db:
            try:
                t = db.get(SupportTicket, tid)
                if not t:
                    return
                t.notes = (t.notes or "")
                if t.notes and not t.notes.endswith("\n"):
                    t.notes += "\n"
                t.notes += f"[{datetime.utcnow():%Y-%m-%d %H:%M}] {note}"
                t.updated_at = datetime.utcnow()
                db.commit()

                # Notify the ticket owner about the new reply
                try:
                    notify_ticket_reply_to_user(t.id, reply_author_id=getattr(self.current_user, "id", None), db=db)
                except Exception:
                    # Fail-safe: ignore notify errors to not block UI
                    pass

                self._load_details()
            except SQLAlchemyError as e:
                db.rollback()
                messagebox.showerror("Add Note", str(e))
