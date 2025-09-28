from __future__ import annotations

import platform
import threading
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont
from pathlib import Path
from typing import Tuple, Optional, Callable, Any

# Robust PNG loading (handles transparency + scaling)
try:
    from PIL import Image, ImageTk  # type: ignore
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# shared dark theme + density controls (provided by your project)
from .theming import PALETTE, apply_dark_theme, set_density


# ---------------- DPI ----------------
def _set_dpi_awareness():
    """Improve sharpness on HiDPI screens (Windows in particular)."""
    try:
        if platform.system() == "Windows":
            try:
                import ctypes  # type: ignore
                if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
                    # Per-monitor v2 when available
                    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
            except Exception:
                pass
    except Exception:
        pass


# ---------------- Window sizing ----------------
def maximize_root(root: tk.Tk, min_size: Tuple[int, int] = (1100, 750)) -> None:
    """Best-effort maximize with sensible min size & safe fallbacks."""
    try:
        root.update_idletasks()
    except Exception:
        pass

    # Try native zooming first
    for call in (lambda: root.state("zoomed"),
                 lambda: root.attributes("-zoomed", True)):
        try:
            call()
        except Exception:
            continue

    # Last resort: set near-fullscreen geometry
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        if sw and sh:
            root.geometry(f"{max(sw-40, min_size[0])}x{max(sh-80, min_size[1])}+20+20")
    except Exception:
        pass

    try:
        root.minsize(*min_size)
    except Exception:
        pass

    # Apply Tk scaling to keep widgets proportionate on HiDPI
    try:
        ppi = root.winfo_fpixels("1i")
        if ppi and ppi > 0:
            root.tk.call("tk", "scaling", max(1.0, float(ppi) / 72.0))
    except Exception:
        pass


def set_fixed_size(root: tk.Tk, width: int = 1280, height: int = 800, lock: bool = False) -> None:
    """Set a fixed initial window size; optionally lock resizing."""
    try:
        root.geometry(f"{width}x{height}+120+60")
        root.minsize(width, height)
        if lock:
            root.resizable(False, False)
    except Exception:
        pass


# ---------------- Fonts ----------------
def _choose_ui_family(root: tk.Misc) -> str:
    try:
        fams = {f.lower() for f in tkfont.families(root)}
    except Exception:
        fams = set()
    for name in (
        "Inter", "Poppins", "Segoe UI", "Noto Sans",
        "Cantarell", "DejaVu Sans", "Helvetica", "Arial", "Sans"
    ):
        if name.lower() in fams:
            return name
    # Fallback to Tk default
    return tkfont.nametofont("TkDefaultFont").cget("family")


# ---------------- Global styling wrapper ----------------
def _apply_global_style(root: tk.Misc):
    """Apply dark theme, set global fonts, register helper styles, set density."""
    apply_dark_theme(root)

    ui_family = _choose_ui_family(root)
    try:
        scale = max(1.0, root.winfo_fpixels("1i") / 72.0)
    except Exception:
        scale = 1.0
    base = (11 if scale < 1.4 else 12)

    # Configure Tk named fonts
    try:
        tkfont.nametofont("TkDefaultFont").configure(family=ui_family, size=base)
        tkfont.nametofont("TkTextFont").configure(family=ui_family, size=base)
        tkfont.nametofont("TkHeadingFont").configure(family=ui_family, size=base+1, weight="bold")
        tkfont.nametofont("TkFixedFont").configure(size=base)
    except Exception:
        pass

    style = ttk.Style(root)
    style.configure("Page.TFrame", background=PALETTE.get("frame", "#263140"))

    # Entries
    style.map(
        "TEntry",
        foreground=[("disabled", PALETTE.get("muted", "#A8B3C5"))],
        fieldbackground=[("!disabled", PALETTE.get("frame", "#263140"))],
    )
    style.configure("Placeholder.TEntry",
                    foreground=PALETTE.get("muted", "#A8B3C5"))
    style.configure("Valid.TEntry",
                    fieldbackground=PALETTE.get("frame", "#263140"),
                    bordercolor=PALETTE.get("success", "#22c55e"))
    style.configure("Invalid.TEntry",
                    fieldbackground=PALETTE.get("frame", "#263140"),
                    bordercolor=PALETTE.get("danger", "#ef4444"))

    # Labels + pills
    style.configure("Muted.TLabel",
                    foreground=PALETTE.get("muted", "#A8B3C5"),
                    background=PALETTE.get("frame", "#263140"))
    style.configure("Pill.TLabel",
                    background=PALETTE.get("subtle", "#2B394B"),
                    foreground=PALETTE.get("fg", "#E8EEF7"),
                    padding=(10, 4))

    # Buttons (ghost & danger as fallbacks)
    style.configure("Ghost.TButton",
                    background=PALETTE.get("frame", "#263140"),
                    foreground=PALETTE.get("fg", "#E8EEF7"),
                    borderwidth=0,
                    padding=(10, 6))
    style.map("Ghost.TButton",
              relief=[("pressed", "sunken"), ("!pressed", "flat")])

    style.configure("Danger.TButton",
                    background=PALETTE.get("danger", "#ef4444"),
                    foreground=PALETTE.get("accent_fg", "#111827"),
                    padding=(10, 6))
    style.map("Danger.TButton",
              relief=[("pressed", "sunken"), ("!pressed", "raised")])

    # Card styles (for dashboard tiles)
    style.configure("Card.TFrame",
                    background=PALETTE.get("subtle", "#2B394B"),
                    borderwidth=0,
                    padding=(12, 10))
    style.configure("MetricNum.TLabel",
                    background=PALETTE.get("subtle", "#2B394B"),
                    foreground=PALETTE.get("fg", "#E8EEF7"),
                    font=(ui_family, base + 6, "bold"))
    style.configure("MetricCap.TLabel",
                    background=PALETTE.get("subtle", "#2B394B"),
                    foreground=PALETTE.get("muted", "#A8B3C5"),
                    font=(ui_family, base - 1))

    # Default density
    set_density(root, "comfy")


# ---------------- Form helpers ----------------
def attach_placeholder(entry: ttk.Entry, text: str):
    """Lightweight placeholder behavior for ttk.Entry."""
    def on_focus_in(_):
        if entry.get() == text:
            entry.delete(0, "end")
            entry.configure(style="TEntry")

    def on_focus_out(_):
        if not entry.get():
            entry.insert(0, text)
            entry.configure(style="Placeholder.TEntry")

    # Initialize
    try:
        if not entry.get():
            entry.insert(0, text)
            entry.configure(style="Placeholder.TEntry")
    except Exception:
        pass

    entry.bind("<FocusIn>", on_focus_in, add="+")
    entry.bind("<FocusOut>", on_focus_out, add="+")


def mark_valid(widget: ttk.Entry, ok: bool):
    """Switch between Valid/Invalid styles without changing widget API."""
    widget.configure(style="Valid.TEntry" if ok else "Invalid.TEntry")


# ---------------- Table helpers ----------------
def _try_float_or_dt(s: str) -> tuple[int, Any]:
    """Return a tuple keyed for robust sorting: (type_rank, comparable_value).
    type_rank ensures consistent grouping: numbers < dates < strings.
    """
    s = str(s).strip()
    # Try float (numbers with commas allowed)
    try:
        val = float(s.replace(",", ""))
        return (0, val)
    except Exception:
        pass
    # Try datetime (YYYY-MM-DD HH:MM or YYYY-MM-DD)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            from datetime import datetime
            return (1, datetime.strptime(s, fmt))
        except Exception:
            continue
    # String fallback (case-insensitive)
    return (2, s.lower())


def make_tree_sortable(tree: ttk.Treeview):
    """Make all Treeview columns clickable-sortable with numeric/date smarts."""
    def sort_by(col, reverse=False):
        rows = [(tree.set(k, col), k) for k in tree.get_children("")]
        rows.sort(key=lambda x: _try_float_or_dt(x[0]), reverse=reverse)
        for i, (_, k) in enumerate(rows):
            tree.move(k, "", i)
        tree.heading(col, command=lambda: sort_by(col, not reverse))

    for col in tree["columns"]:
        tree.heading(col, command=lambda c=col: sort_by(c, False))


def autofit_tree_columns(tree: ttk.Treeview, pad=24):
    """Autosize columns to fit content (cheap pass; call after data insert)."""
    try:
        tree.update_idletasks()
    except Exception:
        pass
    fnt = tkfont.Font()
    for col in tree["columns"]:
        maxw = fnt.measure(col) + pad
        for iid in tree.get_children(""):
            txt = tree.set(iid, col)
            maxw = max(maxw, fnt.measure(str(txt)) + pad)
        tree.column(col, width=maxw)


# ---------------- Toast ----------------
class Toast(tk.Toplevel):
    def __init__(self, parent, text, ms=1600):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        bg = PALETTE.get("subtle", "#2B394B")
        fg = PALETTE.get("fg", "#E8EEF7")
        self.configure(bg=bg)
        lbl = ttk.Label(self, text=text, background=bg, foreground=fg, padding=(12, 8))
        lbl.pack()
        self.after(ms, self.destroy)
        self.update_idletasks()
        try:
            x = parent.winfo_rootx() + parent.winfo_width() - self.winfo_width() - 24
            y = parent.winfo_rooty() + parent.winfo_height() - self.winfo_height() - 24
        except Exception:
            x, y = 50, 50
        self.geometry(f"+{x}+{y}")


def show_toast(root, text, ms=1600):
    Toast(root.winfo_toplevel(), text, ms)


# ---------------- Loading overlay ----------------
class LoadingOverlay(tk.Toplevel):
    def __init__(self, parent, text="Working…"):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.configure(bg=PALETTE.get("bg", "#1E2633"))
        except Exception:
            self.configure(bg="#222")
        try:
            self.attributes("-alpha", 0.85)
        except Exception:
            pass
        # Cover parent window
        try:
            w, h = parent.winfo_width(), parent.winfo_height()
            x, y = parent.winfo_rootx(), parent.winfo_rooty()
        except Exception:
            w = h = 400
            x = y = 0
        self.geometry(f"{w}x{h}+{x}+{y}")
        frame = ttk.Frame(self, padding=20)
        frame.place(relx=0.5, rely=0.5, anchor="center")
        ttk.Label(frame, text=text).pack()
        self._spin = ttk.Progressbar(frame, mode="indeterminate", length=180)
        self._spin.pack(pady=10)
        self._spin.start(12)


def with_overlay(frame: tk.Misc, fn: Callable[[], Any], text="Working…"):
    """Run a function while showing a blocking overlay in a background thread."""
    parent = frame.winfo_toplevel()
    overlay = LoadingOverlay(parent, text)

    def _runner():
        try:
            fn()
        except Exception:
            pass
        finally:
            try:
                overlay.destroy()
            except Exception:
                pass

    threading.Thread(target=_runner, daemon=True).start()


# ---------------- Empty state ----------------
def show_empty_state(parent, title="No records", hint="Try adjusting filters."):
    box = ttk.Frame(parent, padding=24)
    ttk.Label(box, text=title, style="Muted.TLabel").pack()
    ttk.Label(box, text=hint, style="Muted.TLabel").pack()
    return box


# ---------------- Floating Chat Launcher ----------------
class ChatLauncher(ttk.Frame):
    """Floating 'chat bubble' bottom-right. Clicking opens HelpdeskChat."""
    def __init__(self, parent, controller):
        super().__init__(parent, style="TFrame")
        self.controller = controller
        self.configure(padding=0)

        # Use a native tk.Button for emoji rendering fidelity
        self.btn = tk.Button(
            self,
            text="💬",
            bd=0,
            relief="flat",
            bg=PALETTE.get("accent", "#60a5fa"),
            fg=PALETTE.get("accent_fg", "#0b1220"),
            activebackground=PALETTE.get("focus", "#334155"),
            activeforeground=PALETTE.get("accent_fg", "#0b1220"),
            font=("Segoe UI", 14, "bold"),
            cursor="hand2",
        )
        self.btn.configure(width=3, height=1)
        self.btn.pack(side="left", padx=(0, 6), pady=0)

        self.hint = ttk.Label(self, text="Help", style="Pill.TLabel")
        self.hint.pack(side="left", padx=(0, 0))

        # Subtle hover effect
        def _hover(_):
            try:
                self.btn.configure(bg=PALETTE.get("accent_hover", PALETTE.get("accent", "#60a5fa")))
            except Exception:
                pass

        def _leave(_):
            try:
                self.btn.configure(bg=PALETTE.get("accent", "#60a5fa"))
            except Exception:
                pass

        self.btn.bind("<Enter>", _hover)
        self.btn.bind("<Leave>", _leave)

        self.btn.bind("<Button-1>", self._open_chat)
        self.hint.bind("<Button-1>", self._open_chat)

    def _open_chat(self, *_):
        try:
            from .helpdesk_chat import HelpdeskChat
            HelpdeskChat(self.controller)
        except Exception as e:
            show_toast(self, f"Chat error: {e}", ms=2200)


# ---------------- Logo utils ----------------
def _find_logo_path(base_dir: Path) -> Optional[Path]:
    """Try both 'style/logo.png' and 'styles/logo.png' so it works regardless of folder name."""
    candidates = [
        base_dir / "style" / "logo.png",
        base_dir / "styles" / "logo.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _load_logo_image(root: tk.Misc, max_h: int = 28) -> Optional[tk.PhotoImage]:
    """Load and scale the logo from care_portal/style(s)/logo.png. Uses PIL if available."""
    try:
        base_dir = Path(__file__).resolve().parents[1]  # care_portal/
        logo_path = _find_logo_path(base_dir)
        if not logo_path:
            return None

        try:
            scale = max(1.0, root.winfo_fpixels("1i") / 72.0)
        except Exception:
            scale = 1.0

        target_h = int(max_h * (1.2 if scale > 1.4 else 1.0))  # slightly larger on HiDPI

        if _HAS_PIL:
            img = Image.open(str(logo_path)).convert("RGBA")
            w, h = img.size
            if h > target_h:
                w = int(w * (target_h / h))
                h = target_h
                img = img.resize((w, h), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        else:
            img = tk.PhotoImage(file=str(logo_path))
            h = img.height()
            if h > target_h and hasattr(img, "subsample"):
                factor = max(1, int(h / target_h))
                img = img.subsample(factor, factor)
            return img
    except Exception:
        return None


# ---------------- BaseFrame ----------------
class BaseFrame(ttk.Frame):
    """
    Professional base layout with:
      - Branded header (logo + title + actions)
      - Scrollable page body
      - Floating chat bubble (bottom-right)
    """
    title: str = "Care Portal"

    def __init__(self, parent: tk.Misc, controller):
        super().__init__(parent)
        _set_dpi_awareness()
        _apply_global_style(self)

        self.controller = controller
        self.current_user = None

        # ----- Header -----
        header = ttk.Frame(self, padding=(10, 8))
        header.pack(side="top", fill="x")

        # Left: Logo + Title
        left = ttk.Frame(header)
        left.pack(side="left", fill="x", expand=True)

        self._logo_img = _load_logo_image(self, max_h=30)
        if self._logo_img is not None:
            tk.Label(left, image=self._logo_img, bg=PALETTE.get("frame", "#263140")).pack(side="left", padx=(2, 10))
        else:
            ttk.Label(left, text="🩺", font=("Segoe UI", 16)).pack(side="left", padx=(4, 8))

        try:
            title_font = tkfont.Font(family=_choose_ui_family(self), size=14, weight="bold")
        except Exception:
            title_font = None
        self.title_lbl = ttk.Label(left, text=self.title, font=title_font)
        self.title_lbl.pack(side="left")

        # Right cluster (created now; shown when user is set)
        right = ttk.Frame(header)
        right.pack(side="right")

        def _toggle_density():
            rowh = ttk.Style(self).lookup("Treeview", "rowheight")
            try:
                rowh_int = int(rowh) if rowh else 28
            except Exception:
                rowh_int = 28
            set_density(self, "compact" if rowh_int >= 28 else "comfy")

        self.density_btn = ttk.Button(right, text="↕ Density", style="Ghost.TButton", command=_toggle_density)
        self.density_btn.pack(side="right", padx=(8, 0))

        self.env_pill = ttk.Label(right, text="DEV", style="Pill.TLabel")
        self.env_pill.pack(side="right", padx=(8, 0))

        # Created but hidden until login
        self.user_lbl = ttk.Label(right, text="", style="Muted.TLabel")
        self.logout_btn = ttk.Button(right, text="Logout", style="Danger.TButton", command=self._logout)

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ----- Scrollable body -----
        container = ttk.Frame(self)
        container.pack(side="top", fill="both", expand=True)

        self._canvas = tk.Canvas(
            container,
            highlightthickness=0,
            borderwidth=0,
            background=PALETTE.get("bg", "#1E2633"),
        )
        self._vbar = ttk.Scrollbar(container, orient="vertical", command=self._canvas.yview)
        self._hbar = ttk.Scrollbar(container, orient="horizontal", command=self._canvas.xview)
        self._canvas.configure(yscrollcommand=self._vbar.set, xscrollcommand=self._hbar.set)

        self.body = ttk.Frame(self._canvas, style="Page.TFrame")
        self._window_id = self._canvas.create_window((0, 0), window=self.body, anchor="nw")

        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._vbar.grid_remove()
        self._hbar.grid_remove()
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # Scroll logic
        def _overflow_state():
            try:
                self._canvas.update_idletasks()
            except Exception:
                pass
            bbox = self._canvas.bbox(self._window_id) or (0, 0, 0, 0)
            x1, y1, x2, y2 = bbox
            view_w = self._canvas.winfo_width()
            view_h = self._canvas.winfo_height()
            return (x2 - x1) > view_w + 1, (y2 - y1) > view_h + 1, bbox, view_w, view_h

        def _clamp_scrollregion():
            need_h, need_v, bbox, view_w, view_h = _overflow_state()
            x1, y1, x2, y2 = bbox
            max_w = max(x2 - x1, view_w)
            max_h = max(y2 - y1, view_h)
            self._canvas.configure(scrollregion=(0, 0, max_w, max_h))
            return need_h, need_v

        def _update_scrollbars():
            need_h, need_v = _clamp_scrollregion()
            if need_h:
                self._hbar.grid(row=1, column=0, sticky="ew")
            else:
                self._hbar.grid_remove()
            if need_v:
                self._vbar.grid(row=0, column=1, sticky="ns")
            else:
                self._vbar.grid_remove()

        self.body.bind("<Configure>", lambda _e=None: _update_scrollbars())

        def _on_canvas_resize(event):
            try:
                self._canvas.itemconfigure(self._window_id, width=event.width)
            except Exception:
                pass
            _update_scrollbars()

        self._canvas.bind("<Configure>", _on_canvas_resize)

        # Mouse wheel (V) / Shift+wheel (H) / Linux buttons / macOS delta
        def _wheel(event):
            need_h, need_v, *_ = _overflow_state()
            delta = event.delta
            if delta == 0 and getattr(event, "num", None) in (4, 5):
                delta = 120 if event.num == 4 else -120
            horiz = bool(event.state & 0x0001)  # Shift = horizontal
            step = int(-delta / 120)
            if horiz:
                if not need_h:
                    return "break"
                self._canvas.xview_scroll(step, "units")
            else:
                if not need_v:
                    return "break"
                self._canvas.yview_scroll(step, "units")

        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind(seq, _wheel, add="+")

        self.after(0, _update_scrollbars)

        # Keyboard shortcuts
        self.bind_all("<Alt-l>", lambda _e: self._logout())
        self.bind_all("<Home>", lambda _e: self._canvas.yview_moveto(0.0))
        self.bind_all("<End>", lambda _e: self._canvas.yview_moveto(1.0))
        self.bind_all("<Prior>", lambda _e: self._canvas.yview_scroll(-1, "pages"))  # PageUp
        self.bind_all("<Next>", lambda _e: self._canvas.yview_scroll(1, "pages"))   # PageDown

        # ----- Initial header state (no user) -----
        self._show_header_for_logged_out()

        # ----- Floating chat bubble -----
        self._chat_launcher = None
        self.after(0, self._ensure_chat_launcher)

    # ---------- header show/hide ----------
    def _show_header_for_logged_out(self):
        try:
            if self.logout_btn.winfo_ismapped():
                self.logout_btn.pack_forget()
            if self.user_lbl.winfo_ismapped():
                self.user_lbl.pack_forget()
        except Exception:
            pass
        self.env_pill.config(text="DEV")

    def _show_header_for_user(self, display_name: str, role_text: str | None):
        if not self.user_lbl.winfo_ismapped():
            self.user_lbl.pack(side="right", padx=(8, 0))
        if not self.logout_btn.winfo_ismapped():
            self.logout_btn.pack(side="right", padx=(8, 0))
        self.user_lbl.config(text=display_name)
        env = "PROD" if getattr(self.controller, "is_prod", False) else "DEV"
        self.env_pill.config(text=f"{role_text} · {env}" if role_text else env)

    # ---------- hooks ----------
    def set_user(self, user):
        """Safe to call *after* subclass widgets exist."""
        self.current_user = user
        if user:
            try:
                name = getattr(user, "full_name", None) or getattr(user, "email", None) or "User"
            except Exception:
                name = "User"
            role = getattr(user, "role", None)
            self._show_header_for_user(name, str(role).upper() if role else None)
        else:
            self._show_header_for_logged_out()

    def on_show(self):
        pass

    def on_app_ready(self):
        pass

    def on_logout(self):
        pass

    # ---------- actions ----------
    def _logout(self):
        try:
            if hasattr(self, "controller"):
                self.controller.logout()
        finally:
            self.set_user(None)
            show_toast(self, "Logged out")

    # ---------- helpers ----------
    def make_section(self, parent, title: str) -> ttk.Labelframe:
        return ttk.Labelframe(parent, text=title, padding=10)

    def style_treeview(self, tree: ttk.Treeview, zebra: bool = True, stretch_last: bool = True):
        cols = tree["columns"]
        if cols:
            for i, c in enumerate(cols):
                width = 160 if i == 0 else 140
                tree.column(c, width=width, stretch=(i != 0))
        if stretch_last and cols:
            tree.column(cols[-1], stretch=True)
        if zebra:
            tree.tag_configure("odd", background=PALETTE.get("stripe1", "#233247"), foreground=PALETTE.get("fg", "#E8EEF7"))
            tree.tag_configure("even", background=PALETTE.get("stripe2", "#1D2A3C"), foreground=PALETTE.get("fg", "#E8EEF7"))
            if not getattr(tree, "_striping_patched", False):
                orig_insert = tree.insert

                def tagged_insert(parent, index, **kw):
                    iid = orig_insert(parent, index, **kw)
                    children = tree.get_children(parent)
                    pos = children.index(iid) if iid in children else len(children) - 1
                    tree.item(iid, tags=("odd" if pos % 2 else "even",))
                    return iid

                tree.insert = tagged_insert  # type: ignore
                tree._striping_patched = True  # type: ignore

    def attach_tree_scrollbars(self, parent, tree: ttk.Treeview):
        wrap = ttk.Frame(parent)
        # Reparent tree into a scrolled wrapper
        try:
            tree.pack_forget()
        except Exception:
            pass
        tree.master = wrap  # type: ignore[attr-defined]
        tree.pack(side="left", fill="both", expand=True)
        vbar = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        hbar = ttk.Scrollbar(wrap, orient="horizontal", command=tree.xview)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        return wrap

    def clear_treeview(self, tree: ttk.Treeview):
        for iid in tree.get_children():
            tree.delete(iid)

    def enhance_treeview(self, tree: ttk.Treeview, zebra: bool = True):
        self.style_treeview(tree, zebra=zebra)
        make_tree_sortable(tree)
        autofit_tree_columns(tree)

    # ---------- chat launcher position ----------
    def _ensure_chat_launcher(self):
        if self._chat_launcher is None:
            try:
                self._chat_launcher = ChatLauncher(self.winfo_toplevel(), self.controller)
                self._chat_launcher.place(relx=1.0, rely=1.0, anchor="se", x=-20, y=-20)
            except Exception:
                self._chat_launcher = None
        if self._chat_launcher:
            self.winfo_toplevel().bind(
                "<Configure>",
                lambda _e: self._chat_launcher.place_configure(relx=1.0, rely=1.0, anchor="se", x=-20, y=-20),
            )
