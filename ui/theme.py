# -*- coding: utf-8 -*-
"""
ui/theme.py — PISUM Design System
Dark medical-grade palette + typography + spacing.
"""
import customtkinter as ctk

# ── Appearance ─────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Color Tokens ───────────────────────────────────────────────────────────
class C:
    # Surfaces
    BG          = "#0D1117"   # app background
    SIDEBAR     = "#010409"   # sidebar
    SURFACE     = "#161B22"   # cards, panels
    SURFACE_2   = "#1C2128"   # elevated card
    SURFACE_3   = "#21262D"   # inputs, hover
    BORDER      = "#30363D"   # subtle border
    BORDER_2    = "#484F58"   # stronger border

    # Brand
    TEAL        = "#14B8A6"   # primary action
    TEAL_DARK   = "#0F8A7A"   # hover / pressed
    TEAL_DIM    = "#0D2D29"   # teal tint bg
    GOLD        = "#D97706"   # accent / badges
    GOLD_DIM    = "#2D1F06"   # gold tint bg

    # Text
    TEXT_1      = "#E6EDF3"   # primary text
    TEXT_2      = "#8B949E"   # secondary text
    TEXT_3      = "#6E7681"   # muted / captions
    TEXT_INV    = "#0D1117"   # text on teal buttons

    # Semantic
    SUCCESS     = "#3FB950"
    SUCCESS_DIM = "#0A2A10"
    WARNING     = "#D29922"
    WARNING_DIM = "#2D220A"
    ERROR       = "#F85149"
    ERROR_DIM   = "#2D0A09"
    INFO        = "#58A6FF"
    INFO_DIM    = "#0A1A2D"

    # Plan badges
    FREE_BG     = "#21262D"
    FREE_FG     = "#8B949E"
    SOLO_BG     = "#0D2D29"
    SOLO_FG     = "#14B8A6"
    PRO_BG      = "#2D1F06"
    PRO_FG      = "#D97706"
    LOCK_FG     = "#484F58"


# ── Typography ─────────────────────────────────────────────────────────────
class F:
    FAMILY      = "Segoe UI"

    DISPLAY     = (FAMILY, 28, "bold")
    TITLE_LG    = (FAMILY, 20, "bold")
    TITLE       = (FAMILY, 16, "bold")
    HEADING     = (FAMILY, 14, "bold")
    SUBHEADING  = (FAMILY, 13, "bold")
    BODY_LG     = (FAMILY, 14)
    BODY        = (FAMILY, 13)
    BODY_SM     = (FAMILY, 12)
    CAPTION     = (FAMILY, 11)
    MONO        = ("Cascadia Code", 13)  # fallback to Consolas


# ── Spacing ────────────────────────────────────────────────────────────────
class S:
    XS   = 4
    SM   = 8
    MD   = 12
    LG   = 16
    XL   = 24
    XXL  = 32
    XXXL = 48


# ── Radius ─────────────────────────────────────────────────────────────────
class R:
    SM   = 4
    MD   = 8
    LG   = 12
    XL   = 16
    PILL = 999


# ── Sidebar dimensions ─────────────────────────────────────────────────────
SIDEBAR_W        = 220   # expanded
SIDEBAR_W_MINI   = 64    # collapsed (icon only)
HEADER_H         = 56
STATUS_BAR_H     = 28


# ── CTk widget defaults (injected globally) ───────────────────────────────
CTK_DEFAULTS = dict(
    fg_color        = C.SURFACE,
    border_color    = C.BORDER,
    text_color      = C.TEXT_1,
    button_color    = C.TEAL,
    button_hover_color = C.TEAL_DARK,
)


def apply_scrollbar_style(widget):
    """Apply minimal dark scrollbar to a CTkTextbox or CTkScrollableFrame."""
    try:
        sb = widget._scrollbar
        sb.configure(
            fg_color=C.SURFACE,
            button_color=C.SURFACE_3,
            button_hover_color=C.BORDER_2,
        )
    except Exception:
        pass
