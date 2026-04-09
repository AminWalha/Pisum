# -*- coding: utf-8 -*-
"""
ui/views/settings_view.py — PISUM Settings
Practitioner config + license info.
"""
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, SectionLabel, Divider,
    PrimaryButton, SecondaryButton, LabeledEntry, LabeledCombo, Badge,
)


class SettingsView(ctk.CTkFrame):
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
        hdr.grid(row=0, column=0, sticky="ew", padx=S.XXL, pady=(S.XL, S.LG))

        ctk.CTkLabel(
            hdr, text="Settings",
            font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1, anchor="w",
        ).pack(side="left")

    # ── Body ──────────────────────────────────��────────────────────────────
    def _build_body(self):
        body = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        body.grid(row=1, column=0, sticky="nsew", padx=S.XXL, pady=(0, S.XL))
        body.grid_columnconfigure(0, weight=1)

        # ── Interface Language card ──────────────────────────────────────
        lang_card = Card(body)
        lang_card.grid(row=0, column=0, sticky="ew", pady=(0, S.LG))
        lang_card.grid_columnconfigure(0, weight=1)

        SectionLabel(lang_card, "Interface Language").grid(
            row=0, column=0, sticky="w", padx=S.LG, pady=(S.LG, S.SM))
        Divider(lang_card).grid(row=1, column=0, sticky="ew", padx=S.LG, pady=(0, S.MD))

        ctk.CTkLabel(
            lang_card,
            text="Sets the language for report templates, modality names, and section headers.",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=S.LG, pady=(0, S.MD))

        lang_row = ctk.CTkFrame(lang_card, fg_color="transparent")
        lang_row.grid(row=3, column=0, sticky="ew", padx=S.LG, pady=(0, S.LG))
        lang_row.grid_columnconfigure(0, weight=1)

        try:
            from Comptes_Rendus import AppConstants
            all_langs = list(AppConstants.AVAILABLE_LANGUAGES.keys())
        except Exception:
            all_langs = ["Français", "English", "Español", "Deutsch",
                         "Italiano", "Português", "Русский"]

        cur_lang = self._s.get("current_language", "Français")
        self._lang_combo = LabeledCombo(lang_row, "Language", values=all_langs)
        self._lang_combo.set(cur_lang)
        self._lang_combo.grid(row=0, column=0, sticky="ew", padx=(0, S.MD))

        PrimaryButton(
            lang_row, "Apply", icon="✔", width=120,
            command=self._apply_language,
        ).grid(row=0, column=1, sticky="s")

        self._lang_saved_lbl = ctk.CTkLabel(
            lang_card, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.SUCCESS,
        )
        self._lang_saved_lbl.grid(row=4, column=0, sticky="w",
                                   padx=S.LG, pady=(0, S.SM))

        # ── Practitioner card ──────────────────────────────────────────
        prac = Card(body)
        prac.grid(row=1, column=0, sticky="ew", pady=(0, S.LG))
        prac.grid_columnconfigure(0, weight=1)

        SectionLabel(prac, "Practitioner Info").grid(
            row=0, column=0, sticky="w", padx=S.LG, pady=(S.LG, S.SM))
        Divider(prac).grid(row=1, column=0, sticky="ew", padx=S.LG, pady=(0, S.MD))

        form = ctk.CTkFrame(prac, fg_color="transparent")
        form.grid(row=2, column=0, sticky="ew", padx=S.LG, pady=(0, S.LG))
        form.grid_columnconfigure((0, 1), weight=1)

        cfg = self._s.get("config_manager")
        etab_val = cfg.get("etablissement", "") if cfg else ""
        med_val  = cfg.get("medecin",       "") if cfg else ""

        self._etab = LabeledEntry(form, "Facility / Hospital",
                                  placeholder="St-Mary Hospital")
        self._etab.grid(row=0, column=0, sticky="ew", padx=(0, S.SM))
        self._etab.set(etab_val)

        self._med = LabeledEntry(form, "Radiologist Name",
                                 placeholder="Dr. Smith")
        self._med.grid(row=0, column=1, sticky="ew")
        self._med.set(med_val)

        PrimaryButton(
            prac, "Save", icon="✔", width=120,
            command=self._save_prac,
        ).grid(row=3, column=0, sticky="e", padx=S.LG, pady=(0, S.LG))

        self._prac_saved_lbl = ctk.CTkLabel(
            prac, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.SUCCESS,
        )
        self._prac_saved_lbl.grid(row=4, column=0, sticky="w",
                                  padx=S.LG, pady=(0, S.SM))

        # ── License card ────────────────────────────────────────────────
        lic = Card(body)
        lic.grid(row=2, column=0, sticky="ew", pady=(0, S.LG))
        lic.grid_columnconfigure(0, weight=1)

        SectionLabel(lic, "License").grid(
            row=0, column=0, sticky="w", padx=S.LG, pady=(S.LG, S.SM))
        Divider(lic).grid(row=1, column=0, sticky="ew",
                          padx=S.LG, pady=(0, S.MD))

        lm = self._s.get("lm")
        if lm:
            plan  = lm.get_plan_name().upper()
            name  = getattr(lm, "user_name", "") or ""
            email = getattr(lm, "user_email", "") or ""

            info_grid = ctk.CTkFrame(lic, fg_color="transparent")
            info_grid.grid(row=2, column=0, sticky="ew", padx=S.LG, pady=(0, S.LG))

            rows = [
                ("Plan",    plan),
                ("Name",    name  or "—"),
                ("Email",   email or "—"),
            ]
            for i, (lbl, val) in enumerate(rows):
                ctk.CTkLabel(
                    info_grid, text=lbl + ":",
                    font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3, anchor="w",
                ).grid(row=i, column=0, sticky="w", pady=4, padx=(0, S.LG))

                badge_style = {
                    "FREE": "free", "SOLO": "solo",
                    "PRO": "pro", "CLINIC": "clinic",
                }.get(plan, "muted") if lbl == "Plan" else None

                if badge_style:
                    Badge(info_grid, val, style=badge_style).grid(
                        row=i, column=1, sticky="w", pady=4)
                else:
                    ctk.CTkLabel(
                        info_grid, text=val,
                        font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_1, anchor="w",
                    ).grid(row=i, column=1, sticky="w", pady=4)
        else:
            ctk.CTkLabel(
                lic, text="License manager unavailable.",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
            ).grid(row=2, column=0, padx=S.LG, pady=(0, S.LG))

        # ── PACS Security card ─────────────────────────────────────────
        sec = Card(body)
        sec.grid(row=3, column=0, sticky="ew")
        sec.grid_columnconfigure(0, weight=1)

        SectionLabel(sec, "PACS Access Password").grid(
            row=0, column=0, sticky="w", padx=S.LG, pady=(S.LG, S.SM))
        Divider(sec).grid(row=1, column=0, sticky="ew",
                          padx=S.LG, pady=(0, S.MD))

        ctk.CTkLabel(
            sec,
            text="Set a password to restrict access to patient records.",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=S.LG, pady=(0, S.MD))

        pw_form = ctk.CTkFrame(sec, fg_color="transparent")
        pw_form.grid(row=3, column=0, sticky="ew", padx=S.LG, pady=(0, S.LG))
        pw_form.grid_columnconfigure(0, weight=1)

        self._pw1 = ctk.CTkEntry(
            pw_form, placeholder_text="New password (min 6 chars)",
            show="•", height=36,
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont(*F.BODY), corner_radius=R.MD,
        )
        self._pw1.grid(row=0, column=0, sticky="ew", pady=(0, S.SM))

        self._pw2 = ctk.CTkEntry(
            pw_form, placeholder_text="Confirm password",
            show="•", height=36,
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont(*F.BODY), corner_radius=R.MD,
        )
        self._pw2.grid(row=1, column=0, sticky="ew", pady=(0, S.SM))

        SecondaryButton(
            pw_form, "Set Password", icon="🔒", width=160,
            command=self._set_password,
        ).grid(row=2, column=0, sticky="w")

        self._pw_lbl = ctk.CTkLabel(
            pw_form, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
        )
        self._pw_lbl.grid(row=3, column=0, sticky="w", pady=(S.XS, 0))

    # ── Actions ────────────────────────────────────────────────────────────
    def _apply_language(self):
        lang = self._lang_combo.get().strip()
        if not lang:
            return
        change_fn = self._s.get("on_change_language")
        if change_fn:
            try:
                change_fn(lang)
                self._lang_saved_lbl.configure(
                    text=f"✓ Language changed to {lang}", text_color=C.SUCCESS)
                self.after(3000, lambda: self._lang_saved_lbl.configure(text=""))
            except Exception as e:
                self._lang_saved_lbl.configure(
                    text=f"Error: {e}", text_color=C.ERROR)
        else:
            self._lang_saved_lbl.configure(
                text="Language change unavailable.", text_color=C.ERROR)

    def _save_prac(self):
        cfg = self._s.get("config_manager")
        if cfg:
            try:
                cfg.set("etablissement", self._etab.get())
                cfg.set("medecin",       self._med.get())
                self._prac_saved_lbl.configure(text="✓ Saved")
                self.after(2500, lambda: self._prac_saved_lbl.configure(text=""))
            except Exception as e:
                self._prac_saved_lbl.configure(
                    text=f"Error: {e}", **{"text_color": C.ERROR})

    def _set_password(self):
        pw1 = self._pw1.get()
        pw2 = self._pw2.get()
        if pw1 != pw2:
            self._pw_lbl.configure(text="Passwords do not match.",
                                   text_color=C.ERROR)
            return
        if len(pw1) < 6:
            self._pw_lbl.configure(text="Password must be at least 6 characters.",
                                   text_color=C.ERROR)
            return
        try:
            from pacs_ris_db import get_pacs_db
            get_pacs_db().set_password(pw1)
            self._pw1.delete(0, "end")
            self._pw2.delete(0, "end")
            self._pw_lbl.configure(text="✓ Password updated.", text_color=C.SUCCESS)
            self.after(3000, lambda: self._pw_lbl.configure(text=""))
        except Exception as e:
            self._pw_lbl.configure(text=f"Error: {e}", text_color=C.ERROR)
