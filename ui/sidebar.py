# -*- coding: utf-8 -*-
"""
ui/sidebar.py — PISUM Navigation Sidebar
Collapsible sidebar with icon + label nav items.
"""
import customtkinter as ctk
from ui.theme import C, F, S, R, SIDEBAR_W, SIDEBAR_W_MINI


# ── Nav item data ──────────────────────────────────────────────────────────
NAV_ITEMS = [
    ("dashboard",  "⊞",  "Dashboard"),
    ("worklist",   "☰",  "Worklist"),
    ("reports",    "◧",  "Reports"),
    ("templates",  "⊡",  "Templates"),
    ("dictation",  "◉",  "Dictation"),
    ("license",    "◈",  "License"),
    ("settings",   "⚙",  "Settings"),
]


class NavItem(ctk.CTkFrame):
    """Single sidebar navigation button."""

    def __init__(self, master, key, icon, label, command, **kw):
        super().__init__(master, fg_color="transparent", corner_radius=R.MD, **kw)
        self.grid_columnconfigure(1, weight=1)

        self._key     = key
        self._command = command
        self._active  = False

        # Icon label
        self._icon_lbl = ctk.CTkLabel(
            self, text=icon,
            width=36, height=36,
            font=ctk.CTkFont("Segoe UI", 18),
            text_color=C.TEXT_3,
            fg_color="transparent",
        )
        self._icon_lbl.grid(row=0, column=0, padx=(S.MD, S.XS))

        # Text label
        self._text_lbl = ctk.CTkLabel(
            self, text=label,
            font=ctk.CTkFont(*F.BODY),
            text_color=C.TEXT_2,
            anchor="w",
        )
        self._text_lbl.grid(row=0, column=1, sticky="w", padx=(0, S.MD))

        # Active indicator bar
        self._bar = ctk.CTkFrame(
            self, width=3, height=28, fg_color="transparent", corner_radius=R.PILL
        )
        self._bar.place(relx=0, rely=0.15, relheight=0.7)

        # Bind clicks to entire row
        for w in (self, self._icon_lbl, self._text_lbl):
            w.bind("<Button-1>", self._on_click)
            w.bind("<Enter>",    self._on_enter)
            w.bind("<Leave>",    self._on_leave)

    def _on_click(self, _e=None):
        self._command(self._key)

    def _on_enter(self, _e=None):
        if not self._active:
            self.configure(fg_color=C.SURFACE_3)

    def _on_leave(self, _e=None):
        if not self._active:
            self.configure(fg_color="transparent")

    def set_active(self, active: bool):
        self._active = active
        if active:
            self.configure(fg_color=C.TEAL_DIM)
            self._icon_lbl.configure(text_color=C.TEAL)
            self._text_lbl.configure(text_color=C.TEAL,
                                     font=ctk.CTkFont(*F.BODY, weight="bold"))
            self._bar.configure(fg_color=C.TEAL)
        else:
            self.configure(fg_color="transparent")
            self._icon_lbl.configure(text_color=C.TEXT_3)
            self._text_lbl.configure(text_color=C.TEXT_2,
                                     font=ctk.CTkFont(*F.BODY))
            self._bar.configure(fg_color="transparent")

    def show_text(self, show: bool):
        if show:
            self._text_lbl.grid()
        else:
            self._text_lbl.grid_remove()


class Sidebar(ctk.CTkFrame):
    """
    Left navigation sidebar.
    Passes (key: str) to on_navigate when a nav item is clicked.
    """

    def __init__(self, master, on_navigate, lm=None, translations=None, **kw):
        kw.setdefault("fg_color", C.SIDEBAR)
        kw.setdefault("corner_radius", 0)
        super().__init__(master, width=SIDEBAR_W, **kw)
        self.pack_propagate(False)

        self._on_navigate  = on_navigate
        self._lm           = lm
        self._t            = translations or {}
        self._active_key   = "worklist"
        self._expanded     = True
        self._nav_items: dict[str, NavItem] = {}

        self._build()

    # ── Build ──────────────────────────────────────────────────────────────
    def _build(self):
        # Logo area
        self._logo_frame = ctk.CTkFrame(
            self, fg_color="transparent", height=64, corner_radius=0
        )
        self._logo_frame.pack(fill="x")
        self._logo_frame.pack_propagate(False)

        self._logo_icon = ctk.CTkLabel(
            self._logo_frame,
            text="⚕",
            font=ctk.CTkFont("Segoe UI", 22, "bold"),
            text_color=C.TEAL,
        )
        self._logo_icon.pack(side="left", padx=(S.LG, S.SM), pady=S.LG)

        self._logo_text = ctk.CTkLabel(
            self._logo_frame,
            text="PISUM",
            font=ctk.CTkFont("Segoe UI", 18, "bold"),
            text_color=C.TEXT_1,
        )
        self._logo_text.pack(side="left")

        # Collapse toggle
        self._toggle_btn = ctk.CTkButton(
            self._logo_frame,
            text="«",
            width=28, height=28,
            fg_color="transparent",
            hover_color=C.SURFACE_3,
            text_color=C.TEXT_3,
            font=ctk.CTkFont("Segoe UI", 14),
            corner_radius=R.MD,
            command=self._toggle_collapse,
        )
        self._toggle_btn.pack(side="right", padx=S.SM)

        # Divider
        ctk.CTkFrame(self, height=1, fg_color=C.BORDER, corner_radius=0).pack(fill="x")

        # Nav items
        self._nav_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._nav_frame.pack(fill="x", pady=S.MD)

        for key, icon, label in NAV_ITEMS:
            item = NavItem(
                self._nav_frame, key, icon, label,
                command=self._handle_navigate,
            )
            item.pack(fill="x", padx=S.SM, pady=2)
            self._nav_items[key] = item

        self._nav_items[self._active_key].set_active(True)

        # Spacer
        ctk.CTkFrame(self, fg_color="transparent").pack(fill="both", expand=True)

        # Divider
        ctk.CTkFrame(self, height=1, fg_color=C.BORDER, corner_radius=0).pack(fill="x")

        # Bottom: plan badge + version
        self._bottom = ctk.CTkFrame(self, fg_color="transparent")
        self._bottom.pack(fill="x", pady=S.MD, padx=S.SM)
        self._bottom.grid_columnconfigure(0, weight=1)

        self._plan_badge = ctk.CTkLabel(
            self._bottom,
            text=self._get_plan_text(),
            font=ctk.CTkFont(*F.CAPTION),
            fg_color=C.SURFACE_3,
            text_color=C.TEXT_2,
            corner_radius=R.PILL,
            padx=S.SM, pady=3,
        )
        self._plan_badge.grid(row=0, column=0, sticky="ew")

        self._ver_lbl = ctk.CTkLabel(
            self._bottom,
            text=self._get_version(),
            font=ctk.CTkFont(*F.CAPTION),
            text_color=C.TEXT_3,
        )
        self._ver_lbl.grid(row=1, column=0, pady=(S.XS, 0))

    # ── Helpers ────────────────────────────────────────────────────────────
    def _get_plan_text(self):
        if self._lm:
            plan = self._lm.get_plan_name().upper()
            name = getattr(self._lm, "user_name", "") or ""
            return f"  {plan}  •  {name[:16]}" if name else f"  {plan}  "
        return "  FREE  "

    def _get_version(self):
        try:
            import json, os
            vp = os.path.join(os.path.dirname(os.path.dirname(__file__)), "version.json")
            with open(vp) as f:
                return "v" + json.load(f).get("version", "")
        except Exception:
            return ""

    # ── Nav ────────────────────────────────────────────────────────────────
    def _handle_navigate(self, key: str):
        if key == self._active_key:
            return
        self._nav_items[self._active_key].set_active(False)
        self._active_key = key
        self._nav_items[key].set_active(True)
        self._on_navigate(key)

    def navigate_to(self, key: str):
        """Programmatically activate a nav item (does NOT fire callback)."""
        if key in self._nav_items:
            self._nav_items[self._active_key].set_active(False)
            self._active_key = key
            self._nav_items[key].set_active(True)

    # ── Collapse ───────────────────────────────────────────────────────────
    def _toggle_collapse(self):
        self._expanded = not self._expanded
        w = SIDEBAR_W if self._expanded else SIDEBAR_W_MINI
        self.configure(width=w)

        for item in self._nav_items.values():
            item.show_text(self._expanded)

        if self._expanded:
            self._logo_text.pack(side="left")
            self._ver_lbl.grid()
            self._plan_badge.grid()
            self._toggle_btn.configure(text="«")
        else:
            self._logo_text.pack_forget()
            self._ver_lbl.grid_remove()
            self._plan_badge.grid_remove()
            self._toggle_btn.configure(text="»")

    def refresh_plan(self):
        self._plan_badge.configure(text=self._get_plan_text())
