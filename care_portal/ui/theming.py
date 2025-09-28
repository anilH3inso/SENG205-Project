# care_portal/ui/theming.py
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

__all__ = [
    "PALETTE",
    "apply_dark_theme",
    "set_density",
    "choose_ui_family",
    "set_global_font",
]

# ---------------- Palette ----------------
PALETTE = {
    # Core
    "bg":        "#1b2533",   # page background (high contrast)
    "frame":     "#243447",   # cards/panels
    "subtle":    "#273349",   # headers / subtle fills
    "fg":        "#FFFFFF",   # primary text
    "muted":     "#B7C3D7",   # secondary text
    "border":    "#334155",   # borders / dividers
    "sel_bg":    "#2a4263",   # selection background
    "stripe1":   "#233247",   # zebra list 1
    "stripe2":   "#1D2A3C",   # zebra list 2

    # Brand / interaction
    "accent":    "#2e8cff",   # primary / links
    "accent_fg": "#0B1220",   # text on accent
    "focus":     "#8ABEFF",   # focus ring

    # Status
    "success":   "#43D18D",
    "warning":   "#F2C94C",
    "info":      "#4FD1C5",
    "danger":    "#FF4D4F",
}

# ---------------- Font helpers ----------------
def choose_ui_family(root: tk.Misc) -> str:
    """Prefer modern UI fonts; fallback to Tk default."""
    try:
        fams = {f.lower() for f in tkfont.families(root)}
    except Exception:
        fams = set()
    for name in (
        "Inter", "Segoe UI", "Poppins", "Noto Sans",
        "Cantarell", "DejaVu Sans", "Helvetica", "Arial", "Sans"
    ):
        if name.lower() in fams:
            return name
    return tkfont.nametofont("TkDefaultFont").cget("family")


def set_global_font(root: tk.Misc, base_size: int | None = None):
    """Apply global font to Tk named fonts (scales for HiDPI)."""
    family = choose_ui_family(root)
    try:
        scale = max(1.0, root.winfo_fpixels("1i") / 72.0)
    except Exception:
        scale = 1.0
    base = base_size or (16 if scale < 1.4 else 17)  # WCAG-friendly base size
    try:
        tkfont.nametofont("TkDefaultFont").configure(family=family, size=base)
        tkfont.nametofont("TkTextFont").configure(family=family, size=base)
        tkfont.nametofont("TkHeadingFont").configure(family=family, size=base+2, weight="bold")
        tkfont.nametofont("TkFixedFont").configure(family="DejaVu Sans Mono", size=base-1)
    except Exception:
        pass

# ---------------- Density ----------------
def set_density(root: tk.Misc, mode: str = "comfy"):
    """Adjust paddings and row heights based on mode."""
    pad = (16, 10) if mode == "comfy" else (10, 6)
    style = ttk.Style(root)
    for key in ("TButton", "Primary.TButton", "Secondary.TButton", "Ghost.TButton", "Danger.TButton"):
        style.configure(key, padding=pad)
    style.configure("Treeview", rowheight=32 if mode == "comfy" else 26)

# ---------------- Theme application ----------------
def apply_dark_theme(root: tk.Misc):
    style = ttk.Style(root)

    # Use a theme that supports customizations
    for theme_name in ("clam", "alt", "default"):
        try:
            style.theme_use(theme_name)
            break
        except Exception:
            continue

    # Window background
    try:
        root.winfo_toplevel().configure(bg=PALETTE["bg"])
    except Exception:
        pass

    # Apply global fonts
    set_global_font(root)

    # Base widgets
    style.configure(".", padding=1)
    style.configure("TFrame", background=PALETTE["frame"])
    style.configure("TLabel", background=PALETTE["frame"], foreground=PALETTE["fg"])
    style.configure("TLabelframe", background=PALETTE["frame"], foreground=PALETTE["fg"], bordercolor=PALETTE["border"])
    style.configure("TLabelframe.Label", background=PALETTE["frame"], foreground=PALETTE["muted"])

    # Muted labels & pills
    style.configure("Muted.TLabel", background=PALETTE["frame"], foreground=PALETTE["muted"])
    style.configure("Pill.TLabel", background=PALETTE["subtle"], foreground=PALETTE["fg"],
                    padding=(10, 4), borderwidth=1, relief="solid")

    # Header shadow bar
    style.configure("HeaderShadow.TFrame", background=PALETTE["border"])

    # Buttons
    style.configure(
        "TButton",
        background=PALETTE["accent"], foreground="#fff",
        padding=(16, 10), borderwidth=0,
        focusthickness=1, focuscolor=PALETTE["focus"]
    )
    style.map(
        "TButton",
        background=[("active", "#4b9bff"), ("pressed", "#206fe0"), ("disabled", PALETTE["subtle"])],
        foreground=[("disabled", PALETTE["muted"])]
    )
    style.configure("Primary.TButton", background=PALETTE["accent"], foreground="#fff", borderwidth=0)
    style.configure("Secondary.TButton", background=PALETTE["subtle"], foreground=PALETTE["fg"], borderwidth=0)
    style.configure("Ghost.TButton", background=PALETTE["frame"], foreground=PALETTE["fg"], borderwidth=1, relief="flat")
    style.configure("Danger.TButton", background=PALETTE["danger"], foreground="#fff", borderwidth=0)

    # Link buttons (for login links)
    style.configure("Link.TButton", background=PALETTE["frame"], foreground=PALETTE["accent"],
                    borderwidth=0, padding=0)
    style.map("Link.TButton",
              foreground=[("active", PALETTE["focus"])],
              background=[("active", PALETTE["frame"])])

    # Entries & comboboxes
    style.configure("TEntry",
                    fieldbackground=PALETTE["subtle"], foreground=PALETTE["fg"],
                    insertcolor=PALETTE["fg"], bordercolor=PALETTE["border"], relief="flat")
    style.map("TEntry",
              bordercolor=[("focus", PALETTE["focus"])],
              foreground=[("disabled", PALETTE["muted"])])
    style.configure("Valid.TEntry", fieldbackground=PALETTE["subtle"], bordercolor=PALETTE["success"])
    style.configure("Invalid.TEntry", fieldbackground="#3a2531", bordercolor=PALETTE["danger"])
    style.configure("Placeholder.TEntry", foreground=PALETTE["muted"])

    style.configure("TCombobox",
                    background=PALETTE["subtle"], fieldbackground=PALETTE["subtle"],
                    foreground=PALETTE["fg"], relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", PALETTE["subtle"])],
              foreground=[("readonly", PALETTE["fg"])],
              bordercolor=[("focus", PALETTE["focus"])],
              arrowcolor=[("pressed", PALETTE["focus"]), ("active", PALETTE["focus"])])

    # Listbox styling (for Combobox popups)
    try:
        root.option_add("*TCombobox*Listbox.background", PALETTE["frame"])
        root.option_add("*TCombobox*Listbox.foreground", PALETTE["fg"])
        root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["sel_bg"])
        root.option_add("*TCombobox*Listbox.selectForeground", PALETTE["fg"])
        root.option_add("*TCombobox*Listbox.borderWidth", 0)
        root.option_add("*TCombobox*Listbox.font", tkfont.nametofont("TkTextFont"))
    except Exception:
        pass

    # Notebook (tabs)
    style.configure("TNotebook", background=PALETTE["frame"], borderwidth=0)
    style.configure("TNotebook.Tab", background=PALETTE["subtle"], foreground=PALETTE["muted"],
                    padding=(14, 8))
    style.map("TNotebook.Tab",
              background=[("selected", PALETTE["accent"])],
              foreground=[("selected", "#fff")])

    # Treeview
    style.configure("Treeview",
                    background=PALETTE["frame"], fieldbackground=PALETTE["frame"],
                    foreground=PALETTE["fg"], rowheight=32,
                    borderwidth=0, relief="flat")
    style.configure("Treeview.Heading",
                    background=PALETTE["subtle"], foreground=PALETTE["fg"],
                    relief="flat", padding=(10, 8))
    style.map("Treeview", background=[("selected", PALETTE["sel_bg"])], foreground=[("selected", PALETTE["fg"])])

    # Scrollbars
    style.configure("Vertical.TScrollbar",
                    background=PALETTE["subtle"], arrowsize=14,
                    troughcolor=PALETTE["bg"], bordercolor=PALETTE["border"])
    style.configure("Horizontal.TScrollbar",
                    background=PALETTE["subtle"], arrowsize=14,
                    troughcolor=PALETTE["bg"], bordercolor=PALETTE["border"])

    # Dialogs / tooltips
    try:
        root.option_add("*Dialog*background", PALETTE["frame"])
        root.option_add("*Dialog*foreground", PALETTE["fg"])
        root.option_add("*Dialog*borderWidth", 1)
    except Exception:
        pass

    # Density
    set_density(root, "comfy")
