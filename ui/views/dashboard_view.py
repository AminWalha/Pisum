# -*- coding: utf-8 -*-
"""
ui/views/dashboard_view.py — Usage & Stats Dashboard
"""
import datetime
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, StatCard, UsageBar, SectionLabel, Divider,
    PrimaryButton, SecondaryButton, Badge,
)


class DashboardView(ctk.CTkFrame):
    def __init__(self, master, core_state: dict, on_navigate=None, **kw):
        kw.setdefault("fg_color", C.BG)
        super().__init__(master, **kw)

        self._s   = core_state
        self._nav = on_navigate

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_body()

    # ── Header ─────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=S.XXL, pady=(S.XXL, S.LG))
        hdr.grid_columnconfigure(0, weight=1)

        lm   = self._s.get("lm")
        name = (lm.user_name if lm else None) or "User"
        hour = datetime.datetime.now().hour
        greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")

        ctk.CTkLabel(
            hdr, text=f"{greeting}, {name}",
            font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            hdr, text=datetime.datetime.now().strftime("%A, %B %d %Y"),
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_2, anchor="w",
        ).grid(row=1, column=0, sticky="w")

        if lm:
            days = lm.days_until_expiry()
            if days is not None and days <= 14:
                style = "error" if days <= 3 else "warning"
                Badge(hdr, f"License expires in {days}d", style=style).grid(
                    row=0, column=1, sticky="e")

    # ── Body ───────────────────────────────────────────────────────────────
    def _build_body(self):
        scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=S.XL, pady=(0, S.XL))
        scroll.grid_columnconfigure((0, 1, 2, 3), weight=1)

        lm = self._s.get("lm")

        # ── Stat cards row ──────────────────────────────────────────────
        reports_used  = lm.reports_used  if lm else 0
        reports_limit = lm.get_limit("max_reports_per_day") if lm else 10
        patients_used = lm.patients_count if lm else 0
        pat_limit     = lm.get_limit("max_patients_per_day") if lm else 20
        dict_used     = lm._dictation_used_today if lm else 0
        dict_limit    = lm.get_limit("ai_dictation_minutes_per_day") if lm else 10
        plan_name     = lm.get_plan_name().upper() if lm else "FREE"

        stat_data = [
            ("Reports today",     reports_used,  "teal"),
            ("Patients today",    patients_used, "gold"),
            ("Dictation (min)",   dict_used,     "info"),
            ("Plan",              plan_name,     "muted"),
        ]
        for col, (label, value, style) in enumerate(stat_data):
            StatCard(
                scroll, label=label, value=value,
                badge_text=None,
                value_color=C.TEAL if style == "teal" else
                           (C.GOLD if style == "gold" else
                           (C.INFO if style == "info" else C.TEXT_2)),
            ).grid(row=0, column=col, padx=(0 if col else 0, S.MD),
                   pady=(0, S.LG), sticky="nsew")

        # ── Usage bars card ─────────────────────────────────────────────
        usage_card = Card(scroll)
        usage_card.grid(row=1, column=0, columnspan=4, sticky="ew",
                        pady=(0, S.LG))
        usage_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            usage_card, text="Daily Usage",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=S.LG, pady=(S.LG, S.MD))

        Divider(usage_card).grid(row=1, column=0, sticky="ew",
                                 padx=S.LG, pady=(0, S.MD))

        bars = [
            ("Reports / day",          reports_used, reports_limit),
            ("Patients / day",         patients_used, pat_limit),
            ("Dictation minutes / day", dict_used,    dict_limit),
        ]
        for i, (lbl, used, limit) in enumerate(bars):
            UsageBar(usage_card, lbl, used, limit).grid(
                row=2 + i, column=0, sticky="ew",
                padx=S.LG, pady=(0, S.MD if i < len(bars)-1 else S.LG))

        # ── Feature grid ───────────────────────────────────────────────
        feat_card = Card(scroll)
        feat_card.grid(row=2, column=0, columnspan=4, sticky="ew",
                       pady=(0, S.LG))
        feat_card.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(
            feat_card, text="Features",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, columnspan=3, sticky="w",
               padx=S.LG, pady=(S.LG, S.MD))
        Divider(feat_card).grid(row=1, column=0, columnspan=3,
                                sticky="ew", padx=S.LG, pady=(0, S.MD))

        features = [
            ("Custom Templates", "custom_templates"),
            ("PACS / RIS",       "pacs_ris"),
            ("Printing",         "printing"),
            ("Word Export",      "export_word_quality"),
            ("AI Dictation",     "ai_dictation_minutes_per_day"),
            ("History",          "history_days"),
        ]

        for idx, (label, feat) in enumerate(features):
            col = idx % 3
            row_n = 2 + (idx // 3)
            val = lm.can_use_feature(feat) if lm else False

            if isinstance(val, bool):
                state_txt = "Available" if val else "Locked"
                state_col = C.SUCCESS if val else C.LOCK_FG
                icon      = "✓" if val else "—"
            elif isinstance(val, str):
                state_txt = val.capitalize()
                state_col = C.TEAL
                icon      = "✓"
            else:
                state_txt = "Unlimited" if val else "Locked"
                state_col = C.SUCCESS if val else C.LOCK_FG
                icon      = "✓" if val else "—"

            cell = ctk.CTkFrame(feat_card, fg_color="transparent")
            cell.grid(row=row_n, column=col, sticky="ew",
                      padx=S.LG, pady=(0, S.MD))
            cell.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(cell, text=icon, text_color=state_col,
                         font=ctk.CTkFont(*F.BODY_SM), width=18,
                         ).grid(row=0, column=0, padx=(0, S.SM))
            ctk.CTkLabel(cell, text=label, text_color=C.TEXT_2,
                         font=ctk.CTkFont(*F.BODY_SM), anchor="w",
                         ).grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(cell, text=state_txt, text_color=state_col,
                         font=ctk.CTkFont(*F.CAPTION), anchor="e",
                         ).grid(row=0, column=2)

        # last row padding
        feat_card.grid_rowconfigure(2 + len(features)//3 + 1, minsize=S.LG)

        # ── Upgrade CTA (free/solo) ─────────────────────────────────────
        plan_low = (lm.get_plan_name() if lm else "free").lower()
        if plan_low in ("free", "solo"):
            cta = Card(scroll)
            cta.grid(row=3, column=0, columnspan=4, sticky="ew",
                     pady=(0, S.LG))
            cta.grid_columnconfigure(0, weight=1)

            cta_inner = ctk.CTkFrame(cta, fg_color="transparent")
            cta_inner.grid(row=0, column=0, sticky="ew",
                           padx=S.LG, pady=S.LG)
            cta_inner.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                cta_inner, text="Unlock full potential",
                font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                cta_inner,
                text="Upgrade to PRO for unlimited reports, dictation, PACS, and more.",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
            ).grid(row=1, column=0, sticky="w", pady=(S.XS, S.MD))

            PrimaryButton(
                cta_inner, "View Plans", icon="◈", width=160,
                command=lambda: self._nav("license") if self._nav else None,
            ).grid(row=2, column=0, sticky="w")
