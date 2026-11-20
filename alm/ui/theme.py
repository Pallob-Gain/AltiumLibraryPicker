"""Minimal light theme: neutral greys, white fields, one muted accent for the main actions."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")

BG = "#f6f7f9"          # window
SURFACE = "#ffffff"     # fields, tables, messages
BORDER = "#d9dce1"
BORDER_STRONG = "#c5c9d0"
TEXT = "#1f2328"
MUTED = "#6b7280"
DISABLED = "#a3a9b1"
HOVER = "#eef0f3"
PRESSED = "#e2e5e9"
SELECT = "#e6eef8"      # selected rows
ACCENT = "#3a6ea5"      # muted blue, primary buttons only
ACCENT_HOVER = "#325f8f"
ACCENT_PRESSED = "#2b5179"

# message / table colours (quiet)
OK = "#2f7d46"
WARN = "#9a6700"
ERR = "#b42318"
ROW_EXACT = "#eef6ef"
ROW_SIMILAR = "#fbf7ee"


def apply(root: tk.Tk) -> None:
    s = ttk.Style(root)
    s.theme_use("clam")                       # the only built-in theme that takes colours
    root.configure(background=BG)
    root.option_add("*Toplevel.background", BG)
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", FONT)

    s.configure(".", background=BG, foreground=TEXT, font=FONT, bordercolor=BORDER,
                lightcolor=BG, darkcolor=BG, troughcolor=BG, focuscolor=BORDER_STRONG,
                selectbackground=SELECT, selectforeground=TEXT, fieldbackground=SURFACE,
                insertcolor=TEXT)
    s.map(".", foreground=[("disabled", DISABLED)])

    s.configure("TLabelframe", background=BG, bordercolor=BORDER, relief="solid",
                borderwidth=1, lightcolor=BG, darkcolor=BG)
    s.configure("TLabelframe.Label", background=BG, foreground=TEXT, font=FONT_BOLD)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Card.TFrame", background=SURFACE)
    s.configure("Status.TLabel", background=SURFACE, foreground=MUTED, padding=(10, 3))

    s.configure("TButton", background=SURFACE, foreground=TEXT, bordercolor=BORDER_STRONG,
                lightcolor=SURFACE, darkcolor=SURFACE, padding=(10, 3), relief="solid",
                borderwidth=1, focuscolor=SURFACE)
    s.configure("Small.TButton", padding=(4, 2))
    s.map("TButton",
          background=[("disabled", BG), ("pressed", PRESSED), ("active", HOVER)],
          lightcolor=[("pressed", PRESSED), ("active", HOVER)],
          darkcolor=[("pressed", PRESSED), ("active", HOVER)],
          bordercolor=[("focus", MUTED)])
    s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                lightcolor=ACCENT, darkcolor=ACCENT, focuscolor=ACCENT, font=FONT_BOLD)
    s.map("Accent.TButton",
          background=[("disabled", "#9db4cc"), ("pressed", ACCENT_PRESSED),
                      ("active", ACCENT_HOVER)],
          lightcolor=[("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
          darkcolor=[("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
          bordercolor=[("pressed", ACCENT_PRESSED), ("active", ACCENT_HOVER)],
          foreground=[("disabled", "#eef2f6")])

    for w in ("TEntry", "TCombobox"):
        s.configure(w, fieldbackground=SURFACE, bordercolor=BORDER_STRONG, lightcolor=SURFACE,
                    darkcolor=SURFACE, padding=(5, 3), arrowcolor=MUTED, background=SURFACE)
        s.map(w, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", SURFACE)],
              fieldbackground=[("readonly", SURFACE), ("disabled", BG)])
    s.map("TEntry", fieldbackground=[("readonly", "#f3f4f6")])
    s.map("TCombobox", selectbackground=[("readonly", SURFACE)],
          selectforeground=[("readonly", TEXT)], background=[("active", HOVER)])

    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=BG, indicatorbackground=SURFACE, indicatorcolor=SURFACE,
                    focuscolor=BG)
        s.map(w, background=[("active", BG)],
              indicatorcolor=[("selected", ACCENT), ("pressed", PRESSED)])

    s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, rowheight=24)
    s.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    s.configure("Treeview.Heading", background=BG, foreground=MUTED, font=FONT,
                relief="flat", bordercolor=BORDER, lightcolor=BG, darkcolor=BG, padding=(6, 4))
    s.map("Treeview.Heading", background=[("active", HOVER)])

    s.configure("TScrollbar", background="#dfe2e6", troughcolor=SURFACE, bordercolor=SURFACE,
                lightcolor="#dfe2e6", darkcolor="#dfe2e6", arrowcolor=MUTED, gripcount=0)
    s.map("TScrollbar", background=[("active", "#cfd3d8")])
    s.configure("TSeparator", background=BORDER)


def style_text(widget: tk.Text) -> None:
    """Messages box: white, flat, thin border; quiet colours for the message tags."""
    widget.configure(background=SURFACE, foreground=TEXT, font=FONT, relief="flat",
                     borderwidth=0, highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=BORDER, padx=8, pady=6, insertbackground=TEXT,
                     selectbackground=SELECT, selectforeground=TEXT)
    widget.tag_configure("bold", font=FONT_BOLD)
    widget.tag_configure("warn", foreground=WARN)
    widget.tag_configure("err", foreground=ERR)
    widget.tag_configure("ok", foreground=OK)
