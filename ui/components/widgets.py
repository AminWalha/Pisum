# -*- coding: utf-8 -*-
"""
ui/components/widgets.py — Reusable CTk widget primitives.
"""
import customtkinter as ctk
from ui.theme import C, F, S, R


# ── Divider ────────────────────────────────────────────────────────────────
class Divider(ctk.CTkFrame):
    def __init__(self, master, orient="horizontal", **kw):
        h = kw.pop("height", 1) if orient == "horizontal" else None
        w = kw.pop("width",  1) if orient == "vertical"   else None
        super().__init__(
            master,
            height=h or 0,
            width=w or 0,
            fg_color=C.BORDER,
            corner_radius=0,
            **kw,
        )
        if orient == "horizontal":
            self.pack_propagate(False)


# ── Badge ──────────────────────────────────────────────────────────────────
class Badge(ctk.CTkLabel):
    STYLES = {
        "teal":    (C.TEAL_DIM,    C.TEAL),
        "gold":    (C.GOLD_DIM,    C.GOLD),
        "success": (C.SUCCESS_DIM, C.SUCCESS),
        "warning": (C.WARNING_DIM, C.WARNING),
        "error":   (C.ERROR_DIM,   C.ERROR),
        "muted":   (C.FREE_BG,     C.FREE_FG),
        "free":    (C.FREE_BG,     C.FREE_FG),
        "solo":    (C.SOLO_BG,     C.SOLO_FG),
        "pro":     (C.PRO_BG,      C.PRO_FG),
        "clinic":  (C.PRO_BG,      C.PRO_FG),
    }

    def __init__(self, master, text, style="teal", **kw):
        bg, fg = self.STYLES.get(style, (C.TEAL_DIM, C.TEAL))
        super().__init__(
            master,
            text=text,
            fg_color=bg,
            text_color=fg,
            font=ctk.CTkFont(*F.CAPTION),
            corner_radius=R.PILL,
            padx=S.SM,
            pady=2,
            **kw,
        )


# ── Section label ──────────────────────────────────────────────────────────
class SectionLabel(ctk.CTkLabel):
    def __init__(self, master, text, **kw):
        super().__init__(
            master,
            text=text.upper(),
            font=ctk.CTkFont(*F.CAPTION),
            text_color=C.TEXT_3,
            anchor="w",
            **kw,
        )


# ── Card ───────────────────────────────────────────────────────────────────
class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        kw.setdefault("fg_color", C.SURFACE)
        kw.setdefault("border_color", C.BORDER)
        kw.setdefault("border_width", 1)
        kw.setdefault("corner_radius", R.LG)
        super().__init__(master, **kw)


# ── Stat card ──────────────────────────────────────────────────────────────
class StatCard(ctk.CTkFrame):
    """Compact card with a big number + label + optional badge."""
    def __init__(self, master, label, value, badge_text=None,
                 badge_style="teal", value_color=None, **kw):
        kw.setdefault("fg_color", C.SURFACE)
        kw.setdefault("border_color", C.BORDER)
        kw.setdefault("border_width", 1)
        kw.setdefault("corner_radius", R.LG)
        super().__init__(master, **kw)

        self.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=S.LG, pady=(S.LG, S.XS))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top, text=label,
            font=ctk.CTkFont(*F.CAPTION),
            text_color=C.TEXT_2, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        if badge_text:
            Badge(top, badge_text, style=badge_style).grid(row=0, column=1, padx=(S.SM, 0))

        ctk.CTkLabel(
            self, text=str(value),
            font=ctk.CTkFont(*F.TITLE_LG),
            text_color=value_color or C.TEXT_1,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=S.LG, pady=(0, S.LG))


# ── Labeled input ──────────────────────────────────────────────────────────
class LabeledEntry(ctk.CTkFrame):
    def __init__(self, master, label, placeholder="", **kw):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=label,
            font=ctk.CTkFont(*F.BODY_SM),
            text_color=C.TEXT_2, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, S.XS))

        self.entry = ctk.CTkEntry(
            self,
            placeholder_text=placeholder,
            fg_color=C.SURFACE_3,
            border_color=C.BORDER,
            text_color=C.TEXT_1,
            placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont(*F.BODY),
            corner_radius=R.MD,
            height=36,
            **kw,
        )
        self.entry.grid(row=1, column=0, sticky="ew")

    def get(self): return self.entry.get()
    def set(self, v): self.entry.delete(0, "end"); self.entry.insert(0, v)
    def bind(self, *a, **kw): self.entry.bind(*a, **kw)


# ── Labeled combobox ───────────────────────────────────────────────────────
class LabeledCombo(ctk.CTkFrame):
    def __init__(self, master, label, values=None, command=None, **kw):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=label,
            font=ctk.CTkFont(*F.BODY_SM),
            text_color=C.TEXT_2, anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, S.XS))

        self.combo = ctk.CTkComboBox(
            self,
            values=values or [],
            command=command,
            fg_color=C.SURFACE_3,
            border_color=C.BORDER,
            button_color=C.SURFACE_3,
            button_hover_color=C.BORDER_2,
            text_color=C.TEXT_1,
            dropdown_fg_color=C.SURFACE_2,
            dropdown_text_color=C.TEXT_1,
            dropdown_hover_color=C.SURFACE_3,
            font=ctk.CTkFont(*F.BODY),
            dropdown_font=ctk.CTkFont(*F.BODY),
            corner_radius=R.MD,
            height=36,
            **kw,
        )
        self.combo.grid(row=1, column=0, sticky="ew")

    def get(self): return self.combo.get()
    def set(self, v): self.combo.set(v)
    def configure(self, **kw): self.combo.configure(**kw)
    def bind(self, *a, **kw): self.combo.bind(*a, **kw)


# ── Icon button ────────────────────────────────────────────────────────────
class IconButton(ctk.CTkButton):
    """Square icon-only button."""
    def __init__(self, master, icon_text, size=36,
                 color=C.TEXT_2, hover_color=C.SURFACE_3, **kw):
        super().__init__(
            master,
            text=icon_text,
            width=size, height=size,
            fg_color="transparent",
            hover_color=hover_color,
            text_color=color,
            font=ctk.CTkFont("Segoe UI", 16),
            corner_radius=R.MD,
            **kw,
        )


# ── Primary button ─────────────────────────────────────────────────────────
class PrimaryButton(ctk.CTkButton):
    def __init__(self, master, text, icon=None, **kw):
        label = f"{icon}  {text}" if icon else text
        kw.setdefault("fg_color",          C.TEAL)
        kw.setdefault("hover_color",       C.TEAL_DARK)
        kw.setdefault("text_color",        C.TEXT_INV)
        kw.setdefault("font",              ctk.CTkFont(*F.SUBHEADING))
        kw.setdefault("corner_radius",     R.MD)
        kw.setdefault("height",            38)
        super().__init__(master, text=label, **kw)


# ── Secondary button ───────────────────────────────────────────────────────
class SecondaryButton(ctk.CTkButton):
    def __init__(self, master, text, icon=None, **kw):
        label = f"{icon}  {text}" if icon else text
        kw.setdefault("fg_color",          C.SURFACE_3)
        kw.setdefault("hover_color",       C.BORDER)
        kw.setdefault("text_color",        C.TEXT_1)
        kw.setdefault("border_color",      C.BORDER)
        kw.setdefault("border_width",      1)
        kw.setdefault("font",              ctk.CTkFont(*F.SUBHEADING))
        kw.setdefault("corner_radius",     R.MD)
        kw.setdefault("height",            38)
        super().__init__(master, text=label, **kw)


# ── Ghost button ───────────────────────────────────────────────────────────
class GhostButton(ctk.CTkButton):
    def __init__(self, master, text, icon=None, color=None, **kw):
        label = f"{icon}  {text}" if icon else text
        kw.setdefault("fg_color",      "transparent")
        kw.setdefault("hover_color",   C.SURFACE_3)
        kw.setdefault("text_color",    color or C.TEXT_2)
        kw.setdefault("font",          ctk.CTkFont(*F.BODY))
        kw.setdefault("corner_radius", R.MD)
        kw.setdefault("height",        34)
        super().__init__(master, text=label, **kw)


# ── Progress bar with label ────────────────────────────────────────────────
class UsageBar(ctk.CTkFrame):
    def __init__(self, master, label, used, limit, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        self.grid_columnconfigure(0, weight=1)

        frac = 0 if (not limit or limit == -1) else min(used / limit, 1.0)
        color = C.SUCCESS if frac < 0.7 else (C.WARNING if frac < 0.9 else C.ERROR)
        limit_str = "∞" if limit == -1 else str(limit)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        row.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(row, text=label, font=ctk.CTkFont(*F.BODY_SM),
                     text_color=C.TEXT_2, anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(row, text=f"{used} / {limit_str}",
                     font=ctk.CTkFont(*F.CAPTION),
                     text_color=C.TEXT_3).grid(row=0, column=1)

        bar = ctk.CTkProgressBar(
            self, height=4, corner_radius=R.PILL,
            fg_color=C.SURFACE_3, progress_color=color,
        )
        bar.set(frac if limit != -1 else 1.0)
        if limit == -1:
            bar.configure(progress_color=C.TEAL)
        bar.grid(row=1, column=0, sticky="ew", pady=(S.XS, 0))


# ── Notification banner ────────────────────────────────────────────────────
class Banner(ctk.CTkFrame):
    STYLES = {
        "info":    (C.INFO_DIM,    C.INFO,    "ℹ"),
        "success": (C.SUCCESS_DIM, C.SUCCESS, "✓"),
        "warning": (C.WARNING_DIM, C.WARNING, "⚠"),
        "error":   (C.ERROR_DIM,   C.ERROR,   "✕"),
    }

    def __init__(self, master, message, style="info", **kw):
        bg, fg, icon = self.STYLES.get(style, self.STYLES["info"])
        kw.setdefault("fg_color", bg)
        kw.setdefault("corner_radius", R.MD)
        super().__init__(master, **kw)

        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=icon, text_color=fg,
                     font=ctk.CTkFont(*F.BODY)).grid(
            row=0, column=0, padx=(S.MD, S.SM), pady=S.MD)
        ctk.CTkLabel(self, text=message, text_color=fg,
                     font=ctk.CTkFont(*F.BODY_SM), anchor="w", wraplength=400).grid(
            row=0, column=1, sticky="w", pady=S.MD, padx=(0, S.MD))
