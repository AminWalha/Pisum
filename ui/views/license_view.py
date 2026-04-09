# -*- coding: utf-8 -*-
"""
ui/views/license_view.py — License & Upgrade View
"""
import datetime
import webbrowser
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, StatCard, UsageBar, SectionLabel, Divider,
    PrimaryButton, SecondaryButton, GhostButton, Badge,
)

UPGRADE_URL = "https://pisum.app/upgrade"

PLAN_FEATURES = {
    "free":   {"name": "FREE",   "price": "Free",        "color": C.TEXT_2},
    "solo":   {"name": "SOLO",   "price": "€29/month",   "color": C.TEAL},
    "pro":    {"name": "PRO",    "price": "€59/month",   "color": C.GOLD},
    "clinic": {"name": "CLINIC", "price": "€149/month",  "color": C.INFO},
}

COMPARISON = [
    ("Reports / day",           {"free": "10",     "solo": "Unlimited", "pro": "Unlimited", "clinic": "Unlimited"}),
    ("Patients / day",          {"free": "20",     "solo": "200",       "pro": "Unlimited", "clinic": "Unlimited"}),
    ("Dictation (min/day)",     {"free": "10 min", "solo": "60 min",    "pro": "Unlimited", "clinic": "Unlimited"}),
    ("Custom templates",        {"free": "—",      "solo": "✓",         "pro": "✓",         "clinic": "✓"}),
    ("PACS / RIS",              {"free": "—",      "solo": "✓",         "pro": "✓",         "clinic": "✓"}),
    ("Word export (quality)",   {"free": "Basic",  "solo": "Premium",   "pro": "Premium",   "clinic": "Premium"}),
    ("Printing",                {"free": "—",      "solo": "✓",         "pro": "✓",         "clinic": "✓"}),
    ("Multi-user seats",        {"free": "1",      "solo": "1",         "pro": "3",         "clinic": "10"}),
]


class LicenseView(ctk.CTkFrame):
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
        plan = lm.get_plan_name().lower() if lm else "free"
        info = PLAN_FEATURES.get(plan, PLAN_FEATURES["free"])

        ctk.CTkLabel(
            hdr, text="License & Subscription",
            font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        row_right = ctk.CTkFrame(hdr, fg_color="transparent")
        row_right.grid(row=0, column=1, rowspan=2, padx=(S.MD, 0))

        if plan in ("free", "solo"):
            PrimaryButton(
                row_right, "Upgrade Plan", icon="◈",
                width=160,
                command=lambda: webbrowser.open(UPGRADE_URL),
            ).pack(side="right", padx=(S.SM, 0))

        SecondaryButton(
            row_right, "Activate Key", icon="◈",
            width=160,
            command=self._show_activation,
        ).pack(side="right")

        badge_row = ctk.CTkFrame(hdr, fg_color="transparent")
        badge_row.grid(row=1, column=0, sticky="w", pady=(S.XS, 0))

        ctk.CTkLabel(
            badge_row,
            text=f"Current plan: ",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_2,
        ).pack(side="left")
        ctk.CTkLabel(
            badge_row,
            text=info["name"],
            font=ctk.CTkFont(*F.BODY, weight="bold"),
            text_color=info["color"],
        ).pack(side="left")

        if lm and lm.is_active:
            days = lm.days_until_expiry()
            if days is not None:
                ctk.CTkLabel(
                    badge_row,
                    text=f"  •  Expires in {days} days" if days > 0 else "  •  Expired",
                    font=ctk.CTkFont(*F.BODY),
                    text_color=C.ERROR if days <= 7 else C.TEXT_3,
                ).pack(side="left")
        elif not (lm and lm.is_active):
            ctk.CTkLabel(
                badge_row, text="  •  Not activated",
                font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_3,
            ).pack(side="left")

    # ── Body ───────────────────────────────────────────────────────────────
    def _build_body(self):
        scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=S.XL, pady=(0, S.XL))
        scroll.grid_columnconfigure(0, weight=1)

        lm   = self._s.get("lm")
        plan = lm.get_plan_name().lower() if lm else "free"

        # ── License details card ────────────────────────────────────────
        if lm and lm.is_active:
            details = Card(scroll)
            details.grid(row=0, column=0, sticky="ew", pady=(0, S.LG))
            details.grid_columnconfigure((0, 1), weight=1)

            ctk.CTkLabel(
                details, text="License Details",
                font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
            ).grid(row=0, column=0, columnspan=2, sticky="w",
                   padx=S.LG, pady=(S.LG, S.MD))
            Divider(details).grid(row=1, column=0, columnspan=2,
                                  sticky="ew", padx=S.LG, pady=(0, S.MD))

            fields = [
                ("License key", lm.license_key or "—"),
                ("User",        lm.user_name or "—"),
                ("Plan",        lm.get_plan_name().upper()),
                ("Machine ID",  lm.machine_id[:20] + "…" if lm.machine_id else "—"),
            ]
            for i, (label, val) in enumerate(fields):
                col = i % 2
                row_n = 2 + i // 2
                cell = ctk.CTkFrame(details, fg_color="transparent")
                cell.grid(row=row_n, column=col, sticky="ew",
                          padx=S.LG, pady=(0, S.MD))
                ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(*F.CAPTION),
                             text_color=C.TEXT_3, anchor="w").pack(anchor="w")
                ctk.CTkLabel(cell, text=str(val), font=ctk.CTkFont(*F.BODY_SM),
                             text_color=C.TEXT_1, anchor="w").pack(anchor="w")

            last_row = 2 + (len(fields) - 1) // 2 + 1

            btn_row = ctk.CTkFrame(details, fg_color="transparent")
            btn_row.grid(row=last_row, column=0, columnspan=2,
                         sticky="w", padx=S.LG, pady=(S.SM, S.LG))
            SecondaryButton(
                btn_row, "Refresh License", icon="↺",
                command=self._refresh_license,
            ).pack(side="left", padx=(0, S.SM))
            GhostButton(
                btn_row, "Deactivate Device", color=C.ERROR,
                command=self._deactivate,
            ).pack(side="left")

        # ── Plan comparison ─────────────────────────────────────────────
        comp_card = Card(scroll)
        comp_card.grid(row=1, column=0, sticky="ew", pady=(0, S.LG))
        comp_card.grid_columnconfigure((0,1,2,3,4), weight=1)

        ctk.CTkLabel(
            comp_card, text="Plan Comparison",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, columnspan=5, sticky="w",
               padx=S.LG, pady=(S.LG, S.MD))
        Divider(comp_card).grid(row=1, column=0, columnspan=5,
                                sticky="ew", padx=S.LG, pady=(0, S.SM))

        plans = ["free", "solo", "pro", "clinic"]
        # Header row
        ctk.CTkLabel(comp_card, text="Feature",
                     font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
                     text_color=C.TEXT_3, anchor="w",
                     ).grid(row=2, column=0, sticky="w", padx=S.LG, pady=S.XS)
        for col, p in enumerate(plans, start=1):
            info = PLAN_FEATURES[p]
            is_current = p == plan
            ctk.CTkLabel(
                comp_card,
                text=info["name"],
                font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
                text_color=info["color"] if is_current else C.TEXT_2,
                fg_color=C.TEAL_DIM if is_current else "transparent",
                corner_radius=R.MD, padx=S.SM,
            ).grid(row=2, column=col, pady=S.XS, padx=2)

        Divider(comp_card).grid(row=3, column=0, columnspan=5,
                                sticky="ew", padx=S.LG, pady=(0, S.SM))

        for r_idx, (feature, vals) in enumerate(COMPARISON):
            bg = C.SURFACE_2 if r_idx % 2 == 0 else "transparent"
            row_frame = ctk.CTkFrame(comp_card, fg_color=bg, corner_radius=0)
            row_frame.grid(row=4+r_idx, column=0, columnspan=5,
                           sticky="ew", padx=S.SM)
            row_frame.grid_columnconfigure((0,1,2,3,4), weight=1)

            ctk.CTkLabel(
                row_frame, text=feature,
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=S.LG, pady=S.SM)

            for col, p in enumerate(plans, start=1):
                val  = vals.get(p, "—")
                is_c = p == plan
                col_c = C.TEAL if val == "✓" else (
                        C.ERROR_DIM if val == "—" else C.TEXT_1
                ) if not is_c else (
                        C.TEAL if val == "✓" else C.TEXT_1
                )
                ctk.CTkLabel(
                    row_frame, text=val,
                    font=ctk.CTkFont(*F.BODY_SM),
                    text_color=col_c if not is_c else C.TEXT_1,
                    fg_color=C.TEAL_DIM if is_c else "transparent",
                    corner_radius=0, padx=S.XS,
                ).grid(row=0, column=col, pady=S.SM, padx=2)

        last_comp_row = 4 + len(COMPARISON)
        comp_card.grid_rowconfigure(last_comp_row, minsize=S.LG)

        # ── Upgrade CTA ─────────────────────────────────────────────────
        if plan in ("free", "solo"):
            cta = Card(scroll)
            cta.grid(row=2, column=0, sticky="ew")
            cta.grid_columnconfigure(0, weight=1)

            inner = ctk.CTkFrame(cta, fg_color="transparent")
            inner.grid(row=0, column=0, sticky="ew", padx=S.LG, pady=S.LG)
            inner.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                inner, text="Upgrade to unlock everything",
                font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
            ).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                inner,
                text="Unlimited reports · AI dictation · PACS/RIS · Premium Word export",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
            ).grid(row=1, column=0, sticky="w", pady=(S.XS, S.MD))

            PrimaryButton(
                inner, "Upgrade Now", icon="◈", width=180,
                command=lambda: webbrowser.open(UPGRADE_URL),
            ).grid(row=2, column=0, sticky="w")

    # ── Actions ────────────────────────────────────────────────────────────
    def _show_activation(self):
        from ui.dialogs.activation_dialog import ActivationDialog
        dlg = ActivationDialog(
            self.winfo_toplevel(),
            lm=self._s.get("lm"),
            on_activated=self._on_activated,
        )
        dlg.grab_set()

    def _on_activated(self, success: bool, message: str):
        if success and self._nav:
            self.after(800, lambda: self._nav("dashboard"))

    def _refresh_license(self):
        lm = self._s.get("lm")
        if not lm:
            return
        ok, msg = lm.refresh_license()
        # rebuild view
        for w in self.winfo_children():
            w.destroy()
        self._build_header()
        self._build_body()

    def _deactivate(self):
        from ui.dialogs.confirm_dialog import ConfirmDialog
        dlg = ConfirmDialog(
            self.winfo_toplevel(),
            title="Deactivate device",
            message=(
                "This will remove this device from your license.\n"
                "You can re-activate later with your license key.\n\n"
                "Continue?"
            ),
            on_confirm=self._do_deactivate,
        )
        dlg.grab_set()

    def _do_deactivate(self):
        lm = self._s.get("lm")
        if lm:
            lm.deactivate_device()
        for w in self.winfo_children():
            w.destroy()
        self._build_header()
        self._build_body()
