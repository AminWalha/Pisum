# -*- coding: utf-8 -*-
"""
ui/views/report_editor_view.py — PISUM Report Editor
=====================================================
Radiology report editor with two operating modes:

STRUCTURED mode  (SOLO / PRO / CLINIC)
    • Section tabs: [Indication] [Technique] [Results] [Conclusion]
    • Language selector: [FR] [EN]  (PRO / CLINIC only)
    • ONE text area — content switches dynamically on tab / language change
    • AI-enhance button per section (stub, ready to wire)
    • Backed by ReportEditorController

CLASSIC mode  (FREE plan)
    • All four sections visible simultaneously in a scrollable view
    • Upgrade banner encouraging PRO upgrade
    • Direct DB save (unchanged behaviour from v1)

Both modes share:
    • Left panel  — practitioner info, template / formula picker
    • Patient info band
    • Action bar  — Save Draft · Word/Print · Finalize · Next Patient
    • Auto-save   — 30 s periodic + 1.5 s debounce
    • Ctrl+S / Ctrl+Enter shortcuts
"""

import json
import logging
import threading
import datetime

import pandas as pd
import customtkinter as ctk

from ui.theme import C, F, S, R
from ui.components.widgets import (
    PrimaryButton, SecondaryButton, GhostButton,
    Divider, Badge, SectionLabel, LabeledEntry, LabeledCombo,
)
from report_editor_controller import (
    ReportEditorController,
    SECTIONS        as CTRL_SECTIONS,
    SECTION_LABELS  as CTRL_SECTION_LABELS,
    _match_section_header,
    _CTRL_HEADER_MAP,
)

logger = logging.getLogger(__name__)

# ── Classic-mode section layout  (key, display label, textbox height) ─────────
_CLASSIC_SECTIONS = [
    ("indication", "Indication",  70),
    ("technique",  "Technique",   70),
    ("results",    "Results",    220),
    ("conclusion", "Conclusion", 110),
]

# Mapping: classic textbox key  →  controller buffer key
_CLASSIC_TO_CTRL = {
    "indication": "indication",
    "technique":  "technique",
    "results":    "resultat",
    "conclusion": "conclusion",
}

EMPTY_CONTENT = {k: "" for k, *_ in _CLASSIC_SECTIONS}

# ── Section header map — built dynamically from Comptes_Rendus.TRANSLATIONS ───
# Rule: sections[0]=indication, sections[1]=technique,
#       sections[-1]=conclusion, everything in between=resultat
# This auto-adapts whenever TRANSLATIONS is updated.

def _build_header_map() -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        from Comptes_Rendus import TRANSLATIONS
        for t in TRANSLATIONS.values():
            sections = t.get("sections", [])
            n = len(sections)
            if n < 2:
                continue
            for i, header in enumerate(sections):
                key = header.strip().lower()
                if i == 0:
                    result[key] = "indication"
                elif i == 1:
                    result[key] = "technique"
                elif i == n - 1:
                    result[key] = "conclusion"
                else:
                    result[key] = "resultat"
    except Exception:
        pass
    return result


_HEADER_MAP: dict[str, str] = _build_header_map()


def _parse_template(content: str) -> dict[str, str]:
    """
    Split a flat template string into {section_key: text}.
    Keys: indication / technique / resultat / conclusion.
    Uses _HEADER_MAP built from Comptes_Rendus.TRANSLATIONS.
    Falls back to 'resultat' if no headers are recognised.
    """
    result: dict[str, list[str]] = {
        "indication": [], "technique": [], "resultat": [], "conclusion": []
    }
    current: str | None = None
    combined_map = {**_HEADER_MAP, **_CTRL_HEADER_MAP}
    for raw in content.splitlines():
        matched = _match_section_header(raw, combined_map)
        if matched:
            current = matched
        elif current is not None:
            result[current].append(raw.replace("▸ ", "").replace("▸", "").strip())
    if current is None:
        result["resultat"] = [content.strip()]
    return {k: "\n".join(v).strip() for k, v in result.items()}


STATUS_COLOR = {
    "En attente": ("#D97706", "#2D1F06"),
    "En cours":   ("#14B8A6", "#0D2D29"),
    "Finalisé":   ("#3FB950", "#0A2A10"),
    "Archivé":    ("#6E7681", "#21262D"),
}
STATUS_LABEL = {
    "En attente": "⏳  Pending",
    "En cours":   "▶  In Progress",
    "Finalisé":   "✓  Done",
    "Archivé":    "⊞  Archived",
}

AUTOSAVE_INTERVAL_MS = 30_000
DEBOUNCE_MS          =  1_500

# Language display options
_LANG_OPTIONS  = [("FR", "fr"), ("EN", "en")]
_LANG_DISPLAY  = {code: label for label, code in _LANG_OPTIONS}


# ══════════════════════════════════════════════════════════════════════════════
#  ReportEditorView
# ══════════════════════════════════════════════════════════════════════════════

class ReportEditorView(ctk.CTkFrame):
    """
    Full-screen report editor launched from a worklist item dict.

    item keys used:
        examen_uuid, patient_uuid,
        nom, prenom, date_naissance, sexe,
        modalite, type_examen, date_examen, num_accession,
        medecin, etablissement, statut
    """

    def __init__(self, master, core_state: dict, item: dict,
                 on_back=None, on_get_next=None,
                 on_open_word=None, on_print=None, **kw):
        kw.setdefault("fg_color", C.BG)
        super().__init__(master, **kw)

        self._s           = core_state
        self._item        = dict(item)
        self._on_back     = on_back
        self._on_get_next = on_get_next
        self._do_word     = on_open_word
        self._do_print    = on_print

        # ── Controller (always created; drives data in both modes) ─────────
        self._ctrl = ReportEditorController(core_state, item)
        self._ctrl.on_content_ready   = self._on_ctrl_content_ready
        self._ctrl.on_save_status     = self._on_ctrl_save_status
        self._ctrl.on_item_updated    = self._on_item_updated

        # ── Feature flags ──────────────────────────────────────────────────
        self._use_structured = self._ctrl.structured_reports_enabled
        self._use_multilang  = False   # FR/EN switcher removed — language follows the combo

        # ── Shared debounce / autosave state ──────────────────────────────
        self._debounce_job = None
        self._autosave_job = None

        # ── Classic-mode state ────────────────────────────────────────────
        self._saved_hash:   str | None                    = None
        self._cr_uuid:      str | None                    = None
        self._sections:     dict[str, ctk.CTkTextbox]     = {}   # classic only

        # ── Structured-mode state ─────────────────────────────────────────
        self._active_section: str = CTRL_SECTIONS[0]
        self._active_lang:    str = self._lang_code_from_display(
            core_state.get("current_language", "Français")
        )
        self._section_btns:   dict[str, ctk.CTkButton] = {}
        self._lang_btns:      dict[str, ctk.CTkButton] = {}
        self._single_tb:      ctk.CTkTextbox | None = None

        # ── Grid ──────────────────────────────────────────────────────────
        # row 0 = top bar, row 1 = patient band,
        # row 2 = structure toolbar (structured) / empty,
        # row 3 = editor area  (weight=1),
        # row 4 = action bar
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=0)   # left panel
        self.grid_columnconfigure(1, weight=1)   # right panel

        self._build_left_panel()
        self._build_right_panel()
        self._bind_shortcuts()
        self._start_autosave()

        # Sync controller's active language with the current report language
        self._ctrl._active_lang = self._active_lang

        # Load data via controller (both modes)
        self._ctrl.load_async()

    # ══════════════════════════════════════════════════════════════════════
    # LEFT PANEL  — practitioner + template picker
    # ══════════════════════════════════════════════════════════════════════

    def _build_left_panel(self):
        left = ctk.CTkFrame(
            self,
            fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            corner_radius=0, width=264,
        )
        left.grid(row=0, column=0, sticky="nsew", rowspan=5)
        left.pack_propagate(False)

        scroll = ctk.CTkScrollableFrame(
            left, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        scroll.pack(fill="both", expand=True, padx=S.LG, pady=S.LG)
        scroll.grid_columnconfigure(0, weight=1)

        row = 0

        # ── Practitioner ────────────────────────────────────────────────
        SectionLabel(scroll, "Practitioner").grid(
            row=row, column=0, sticky="ew", pady=(0, S.SM))
        row += 1

        cfg           = self._s.get("config_manager")
        etab_default  = cfg.get("etablissement", "") if cfg else ""
        med_default   = cfg.get("medecin",       "") if cfg else ""

        self._etab = LabeledEntry(scroll, "Facility",
                                  placeholder="e.g. St-Mary Hospital")
        self._etab.grid(row=row, column=0, sticky="ew", pady=(0, S.SM))
        self._etab.set(self._item.get("etablissement", "") or etab_default)
        row += 1

        self._med = LabeledEntry(scroll, "Radiologist",
                                 placeholder="e.g. Dr. Smith")
        self._med.grid(row=row, column=0, sticky="ew", pady=(0, S.LG))
        self._med.set(self._item.get("medecin", "") or med_default)
        row += 1

        Divider(scroll).grid(row=row, column=0, sticky="ew", pady=(0, S.LG))
        row += 1

        # ── Template selection (3-level: Modality → Exam type → Template) ──
        SectionLabel(scroll, "Template Selection").grid(
            row=row, column=0, sticky="ew", pady=(0, S.SM))
        row += 1

        modalities = list(self._s.get("data", {}).keys())
        self._mod_combo = LabeledCombo(
            scroll, "Modality", values=modalities or ["—"],
            command=self._on_modality_change,
        )
        self._mod_combo.grid(row=row, column=0, sticky="ew", pady=(0, S.SM))
        row += 1

        self._exam_combo = LabeledCombo(
            scroll, "Exam type", values=["—"],
            command=self._on_exam_change,
        )
        self._exam_combo.grid(row=row, column=0, sticky="ew", pady=(0, S.SM))
        row += 1

        self._template_combo = LabeledCombo(
            scroll, "Report template", values=["—"],
            command=lambda _: self._insert_formula(),
        )
        self._template_combo.grid(row=row, column=0, sticky="ew", pady=(0, S.SM))
        row += 1

        ctk.CTkButton(
            scroll, text="↓  Insert into Results",
            height=32, font=ctk.CTkFont(*F.BODY_SM),
            fg_color=C.TEAL_DIM, hover_color=C.SURFACE_3,
            text_color=C.TEAL, corner_radius=R.MD,
            command=self._insert_formula,
        ).grid(row=row, column=0, sticky="ew", pady=(0, S.LG))
        row += 1

        # Search button
        from ui.components.widgets import SecondaryButton
        SecondaryButton(
            scroll, "Search All Templates", icon="⊕",
            command=self._open_search,
        ).grid(row=row, column=0, sticky="ew", pady=(0, S.LG))
        row += 1

        Divider(scroll).grid(row=row, column=0, sticky="ew", pady=(0, S.LG))
        row += 1

        # ── Report language ──────────────────────────────────────────────
        SectionLabel(scroll, "Report Language").grid(
            row=row, column=0, sticky="ew", pady=(0, S.SM))
        row += 1

        try:
            from Comptes_Rendus import AppConstants
            langs = list(AppConstants.AVAILABLE_LANGUAGES.keys())
        except Exception:
            langs = list(self._s.get("translations_map",
                         {"Français": {}, "English": {}}).keys()) or ["Français", "English"]

        self._lang_combo = LabeledCombo(scroll, "Language", values=langs,
                                        command=self._on_language_change)
        self._lang_combo.grid(row=row, column=0, sticky="ew", pady=(0, S.LG))
        self._lang_combo.set(self._s.get("current_language", "Français"))
        row += 1

        # Initialise cascading combos from item modality
        item_mod = self._item.get("modalite", "")
        if item_mod and item_mod in modalities:
            self._mod_combo.set(item_mod)
        self._on_modality_change(self._mod_combo.get())

    # ══════════════════════════════════════════════════════════════════════
    # RIGHT PANEL
    # ══════════════════════════════════════════════════════════════════════

    def _build_right_panel(self):
        self._build_top_bar()
        self._build_patient_band()

        if self._use_structured:
            self._build_structure_toolbar()
            self._build_structured_editor()
        else:
            self._build_classic_editor()
            self._build_upgrade_banner()

        self._build_action_bar()

    # ── Top bar ─────────────────────────────────────────────────────────────

    def _build_top_bar(self):
        top = ctk.CTkFrame(self, fg_color="transparent", height=48)
        top.grid(row=0, column=1, sticky="ew",
                 padx=(S.LG, S.XXL), pady=(S.MD, 0))
        top.pack_propagate(False)
        top.grid_columnconfigure(1, weight=1)

        GhostButton(top, "← Worklist", command=self._go_back).pack(side="left")

        self._dict_btn = ctk.CTkButton(
            top, text="🎤  Dictate",
            width=110, height=32,
            fg_color=C.SURFACE, hover_color=C.SURFACE_3,
            text_color=C.TEXT_2, font=ctk.CTkFont(*F.BODY_SM),
            border_color=C.BORDER, border_width=1,
            corner_radius=R.MD,
            command=self._toggle_dictation,
        )
        self._dict_btn.pack(side="right")

    # ── Patient band ────────────────────────────────────────────────────────

    def _build_patient_band(self):
        self._patient_band = ctk.CTkFrame(
            self,
            fg_color=C.SURFACE_2,
            border_color=C.BORDER, border_width=1,
            corner_radius=R.MD,
        )
        self._patient_band.grid(row=1, column=1, sticky="ew",
                                padx=(S.LG, S.XXL), pady=(S.SM, S.MD))
        self._patient_band.grid_columnconfigure(0, weight=1)
        self._refresh_patient_band()

    def _refresh_patient_band(self):
        for w in self._patient_band.winfo_children():
            w.destroy()

        item    = self._item
        nom     = item.get("nom", "")
        prenom  = item.get("prenom", "")
        name    = f"{nom.upper()}, {prenom}" if nom else "—"
        sexe    = item.get("sexe", "")
        age     = _calc_age(item.get("date_naissance", ""))
        sex_str = f"  {sexe}" if sexe else ""
        mod     = item.get("modalite",    "—")
        type_ex = item.get("type_examen", "")
        exam    = f"{mod}  {type_ex}".strip() if type_ex else mod
        date    = item.get("date_examen", "—")
        acc     = item.get("num_accession", "")
        statut  = item.get("statut", "En attente")
        fg, bg  = STATUS_COLOR.get(statut, STATUS_COLOR["En attente"])
        stat_lbl = STATUS_LABEL.get(statut, statut)

        info_row = ctk.CTkFrame(self._patient_band, fg_color="transparent")
        info_row.grid(row=0, column=0, sticky="ew",
                      padx=S.LG, pady=(S.MD, S.SM))
        info_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            info_row, text=name,
            font=ctk.CTkFont("Segoe UI", 15, "bold"),
            text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            info_row,
            text=f"  •  {age} y{sex_str}  •  {exam}  •  {date}",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_2, anchor="w",
        ).grid(row=0, column=1, sticky="w")

        badge_row = ctk.CTkFrame(self._patient_band, fg_color="transparent")
        badge_row.grid(row=1, column=0, sticky="ew",
                       padx=S.LG, pady=(0, S.MD))

        ctk.CTkLabel(
            badge_row, text=stat_lbl,
            fg_color=bg, text_color=fg,
            font=ctk.CTkFont(*F.CAPTION),
            corner_radius=R.PILL, padx=S.SM, pady=3,
        ).grid(row=0, column=0, sticky="w")

        if acc:
            ctk.CTkLabel(
                badge_row, text=f"  ACC: {acc}",
                font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
            ).grid(row=0, column=1, sticky="w", padx=(S.LG, 0))

    # ══════════════════════════════════════════════════════════════════════
    # STRUCTURED MODE  (SOLO / PRO / CLINIC)
    # ══════════════════════════════════════════════════════════════════════

    def _build_structure_toolbar(self):
        """Section tabs + language selector + AI button (row 2)."""
        toolbar = ctk.CTkFrame(
            self, fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            corner_radius=R.MD,
        )
        toolbar.grid(row=2, column=1, sticky="ew",
                     padx=(S.LG, S.XXL), pady=(0, S.SM))

        # ── Section tabs ────────────────────────────────────────────────
        tabs_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        tabs_frame.pack(side="left", padx=S.SM, pady=S.SM)

        from ui.views.report_view import get_section_labels
        _lang_labels = get_section_labels(self._s.get("current_language", "Français"))

        for section in CTRL_SECTIONS:
            label = _lang_labels.get(section, CTRL_SECTION_LABELS[section])
            btn = ctk.CTkButton(
                tabs_frame,
                text=label,
                width=110, height=30,
                font=ctk.CTkFont(*F.BODY_SM),
                fg_color=C.SURFACE_3,
                hover_color=C.BORDER,
                text_color=C.TEXT_2,
                border_color=C.BORDER, border_width=1,
                corner_radius=R.MD,
                command=lambda s=section: self._on_tab_click(s),
            )
            btn.pack(side="left", padx=(0, S.XS))
            self._section_btns[section] = btn

        # ── Language selector (PRO / CLINIC only) ────────────────────────
        if self._use_multilang:
            Divider(toolbar).pack(
                side="left", fill="y", padx=S.SM, pady=S.SM)

            lang_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
            lang_frame.pack(side="left", padx=S.SM, pady=S.SM)

            ctk.CTkLabel(
                lang_frame,
                text="Lang:",
                font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
            ).pack(side="left", padx=(0, S.XS))

            for label, code in _LANG_OPTIONS:
                btn = ctk.CTkButton(
                    lang_frame,
                    text=label,
                    width=42, height=28,
                    font=ctk.CTkFont(*F.BODY_SM),
                    fg_color=C.SURFACE_3,
                    hover_color=C.BORDER,
                    text_color=C.TEXT_2,
                    border_color=C.BORDER, border_width=1,
                    corner_radius=R.MD,
                    command=lambda c=code: self._on_lang_click(c),
                )
                btn.pack(side="left", padx=(0, 3))
                self._lang_btns[code] = btn

        # ── AI enhance button (stub) ─────────────────────────────────────
        Divider(toolbar).pack(
            side="left", fill="y", padx=S.SM, pady=S.SM)

        self._ai_btn = ctk.CTkButton(
            toolbar,
            text="✨ AI",
            width=60, height=28,
            font=ctk.CTkFont(*F.BODY_SM),
            fg_color=C.GOLD_DIM,
            hover_color=C.SURFACE_3,
            text_color=C.GOLD,
            border_color=C.GOLD_DIM, border_width=1,
            corner_radius=R.MD,
            command=self._on_ai_enhance,
        )
        self._ai_btn.pack(side="left", padx=(0, S.SM))
        self._ai_spin_job = None

        # Highlight defaults
        self._highlight_section_tab(self._active_section)
        if self._use_multilang:
            self._highlight_lang_btn(self._active_lang)

    def _build_structured_editor(self):
        """Single textbox in structured mode (row 3)."""
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.grid(row=3, column=1, sticky="nsew",
                   padx=(S.LG, S.XXL), pady=(0, S.SM))
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        self._single_tb = ctk.CTkTextbox(
            outer,
            fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_1,
            font=ctk.CTkFont("Segoe UI", 13),
            corner_radius=R.MD,
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
            wrap="word",
        )
        self._single_tb.grid(row=0, column=0, sticky="nsew")
        self._single_tb.bind("<KeyRelease>", self._on_text_change_structured)

    # ── Tab / language interaction ───────────────────────────────────────────

    def _on_tab_click(self, section: str) -> None:
        """Flush current text → controller, then switch active section."""
        if self._single_tb is None:
            return
        # Persist what's currently in the textbox
        self._ctrl.update_current_text(
            self._single_tb.get("1.0", "end-1c"))
        # Switch
        text = self._ctrl.set_section(section)
        self._active_section = section
        self._set_single_tb_text(text)
        self._highlight_section_tab(section)

    def _on_lang_click(self, lang_code: str) -> None:
        """Flush current text → controller, then switch language."""
        if self._single_tb is None:
            return
        self._ctrl.update_current_text(
            self._single_tb.get("1.0", "end-1c"))
        text = self._ctrl.set_language(lang_code)
        self._active_lang = lang_code
        self._set_single_tb_text(text)
        self._highlight_lang_btn(lang_code)

    # ── AI visual state ──────────────────────────────────────────────────────

    _SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _ai_set_loading(self) -> None:
        """Disable button + textbox, start spinner animation."""
        if not hasattr(self, "_ai_btn") or self._ai_btn is None:
            return
        self._ai_btn.configure(state="disabled", text_color=C.BORDER_2,
                               fg_color=C.SURFACE_3)
        if self._single_tb:
            self._single_tb.configure(state="disabled")
        self._ai_spin_idx = 0
        self._ai_tick_spinner()

    def _ai_tick_spinner(self) -> None:
        if not hasattr(self, "_ai_btn") or self._ai_btn is None:
            return
        frame = self._SPIN_FRAMES[self._ai_spin_idx % len(self._SPIN_FRAMES)]
        self._ai_btn.configure(text=f"{frame} AI")
        self._ai_spin_idx += 1
        self._ai_spin_job = self.after(80, self._ai_tick_spinner)

    def _ai_set_ready(self, success: bool = True) -> None:
        """Re-enable button + textbox, stop spinner."""
        if self._ai_spin_job:
            self.after_cancel(self._ai_spin_job)
            self._ai_spin_job = None
        if not hasattr(self, "_ai_btn") or self._ai_btn is None:
            return
        if success:
            self._ai_btn.configure(state="normal", text="✓ AI",
                                   fg_color=C.TEAL_DIM, text_color=C.TEAL,
                                   border_color=C.TEAL_DIM)
            self.after(1500, self._ai_reset_btn)
        else:
            self._ai_btn.configure(state="normal", text="✗ AI",
                                   fg_color=C.ERROR_DIM, text_color=C.ERROR,
                                   border_color=C.ERROR_DIM)
            self.after(2000, self._ai_reset_btn)
        if self._single_tb:
            self._single_tb.configure(state="normal")

    def _ai_reset_btn(self) -> None:
        if hasattr(self, "_ai_btn") and self._ai_btn:
            self._ai_btn.configure(text="✨ AI", fg_color=C.GOLD_DIM,
                                   text_color=C.GOLD, border_color=C.GOLD_DIM)

    # ── AI enhance handler ───────────────────────────────────────────────────

    def _on_ai_enhance(self) -> None:
        if self._single_tb is None:
            return
        self._ctrl.update_current_text(self._single_tb.get("1.0", "end-1c"))
        self._ai_set_loading()

        def _on_done(enhanced: str) -> None:
            def _apply():
                self._set_single_tb_text(enhanced)
                self._ai_set_ready(success=True)
            self.after(0, _apply)

        def _on_error(text: str) -> None:
            self.after(0, lambda: self._ai_set_ready(success=False))

        self._ctrl.enhance_section_ai(
            callback=_on_done,
            on_error=_on_error,
        )

    def _highlight_section_tab(self, active: str) -> None:
        for key, btn in self._section_btns.items():
            if key == active:
                btn.configure(
                    fg_color=C.TEAL_DIM, text_color=C.TEAL,
                    border_color=C.TEAL,
                )
            else:
                btn.configure(
                    fg_color=C.SURFACE_3, text_color=C.TEXT_2,
                    border_color=C.BORDER,
                )

    def _highlight_lang_btn(self, active_code: str) -> None:
        for code, btn in self._lang_btns.items():
            if code == active_code:
                btn.configure(
                    fg_color=C.TEAL_DIM, text_color=C.TEAL,
                    border_color=C.TEAL,
                )
            else:
                btn.configure(
                    fg_color=C.SURFACE_3, text_color=C.TEXT_2,
                    border_color=C.BORDER,
                )

    def _set_single_tb_text(self, text: str) -> None:
        """Replace all text in the single textbox."""
        if self._single_tb is None:
            return
        self._single_tb.delete("1.0", "end")
        if text:
            self._single_tb.insert("1.0", text)

    # ── Controller callbacks (structured mode) ───────────────────────────────

    def _on_ctrl_content_ready(self, buffer: dict) -> None:
        """
        Fired by the controller (possibly from a background thread) after
        a load completes.  Always marshals to the main thread.
        """
        self.after(0, lambda: self._apply_buffer(buffer))

    def _apply_buffer(self, buffer: dict) -> None:
        if self._use_structured:
            # Show the active section's text in the single textbox
            text = (buffer.get(self._active_section, {})
                         .get(self._active_lang, ""))
            self._set_single_tb_text(text)
        else:
            # Classic mode: fill all textboxes from the 'fr' content
            content: dict[str, str] = {}
            for classic_key, ctrl_key in _CLASSIC_TO_CTRL.items():
                content[classic_key] = buffer.get(ctrl_key, {}).get("fr", "")
            self._fill_classic_sections(content)

    def _on_ctrl_save_status(self, message: str, is_error: bool) -> None:
        self.after(0, lambda: self._show_save_status(message, is_error))

    def _on_item_updated(self, item: dict) -> None:
        self.after(0, lambda: self._apply_item_update(item))

    def _apply_item_update(self, item: dict) -> None:
        self._item = item
        self._refresh_patient_band()

    def _show_save_status(self, message: str, is_error: bool) -> None:
        if not self.winfo_exists():
            return
        color = C.ERROR if is_error else C.SUCCESS
        self._save_lbl.configure(text=message, text_color=color)
        if not is_error:
            self.after(3000, lambda: (
                self._save_lbl.configure(text="")
                if self.winfo_exists() else None
            ))

    # ── Text change debounce (structured mode) ───────────────────────────────

    def _on_text_change_structured(self, _event=None) -> None:
        if self._debounce_job:
            self.after_cancel(self._debounce_job)
        self._debounce_job = self.after(DEBOUNCE_MS, self._autosave_structured)

    def _autosave_structured(self) -> None:
        if self._single_tb and self.winfo_exists():
            self._ctrl.update_current_text(
                self._single_tb.get("1.0", "end-1c"))
            self._ctrl.save(silent=True)

    # ══════════════════════════════════════════════════════════════════════
    # CLASSIC MODE  (FREE plan)
    # ══════════════════════════════════════════════════════════════════════

    def _build_classic_editor(self):
        """Scrollable multi-section view (row 3)."""
        sections_outer = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        sections_outer.grid(row=3, column=1, sticky="nsew",
                            padx=(S.LG, S.XXL), pady=(0, S.SM))
        sections_outer.grid_columnconfigure(0, weight=1)

        from ui.views.report_view import get_section_labels
        _lang_labels = get_section_labels(self._s.get("current_language", "Français"))
        # classic key → ctrl key mapping
        _classic_ctrl = {"indication": "indication", "technique": "technique",
                         "results": "resultat", "conclusion": "conclusion"}
        for i, (key, default_label, height) in enumerate(_CLASSIC_SECTIONS):
            ctrl_key = _classic_ctrl.get(key, key)
            label = _lang_labels.get(ctrl_key, default_label)
            self._build_classic_section(
                sections_outer, i * 2, key, label, height)

    def _build_classic_section(self, parent, row: int,
                                key: str, label: str, height: int):
        hdr = ctk.CTkFrame(parent, fg_color="transparent")
        hdr.grid(row=row, column=0,
                 sticky="ew", pady=(S.SM if row else 0, S.XS))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text=label.upper(),
            font=ctk.CTkFont("Segoe UI", 10, "bold"),
            text_color=C.TEXT_3, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        Divider(hdr).grid(row=1, column=0, sticky="ew", pady=(S.XS, 0))

        tb = ctk.CTkTextbox(
            parent,
            height=height,
            fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_1,
            font=ctk.CTkFont("Segoe UI", 13),
            corner_radius=R.MD,
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
            wrap="word",
        )
        tb.grid(row=row + 1, column=0, sticky="ew", pady=(0, S.MD))
        tb.bind("<KeyRelease>", self._on_text_change_classic)
        self._sections[key] = tb

    def _build_upgrade_banner(self):
        """
        Subtle upgrade prompt displayed below the patient band (row 2)
        when user is on the FREE plan.
        """
        banner = ctk.CTkFrame(
            self,
            fg_color=C.GOLD_DIM,
            border_color=C.GOLD, border_width=1,
            corner_radius=R.MD, height=36,
        )
        banner.grid(row=2, column=1, sticky="ew",
                    padx=(S.LG, S.XXL), pady=(0, S.SM))
        banner.pack_propagate(False)
        banner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            banner,
            text="✦  Upgrade to PRO to unlock structured sections & multilingual reports",
            font=ctk.CTkFont(*F.CAPTION),
            text_color=C.GOLD,
            anchor="w",
        ).pack(side="left", padx=S.LG)

        ctk.CTkButton(
            banner,
            text="Upgrade →",
            width=90, height=24,
            font=ctk.CTkFont(*F.CAPTION),
            fg_color=C.GOLD, hover_color=C.TEAL,
            text_color=C.TEXT_INV,
            corner_radius=R.MD,
            command=self._open_license_view,
        ).pack(side="right", padx=S.LG)

    def _open_license_view(self) -> None:
        nav = self._s.get("on_navigate")
        if callable(nav):
            nav("license")

    # ── Classic-mode helpers ─────────────────────────────────────────────────

    def _fill_classic_sections(self, content: dict) -> None:
        for key, tb in self._sections.items():
            tb.delete("1.0", "end")
            text = content.get(key, "")
            if text:
                tb.insert("1.0", text)
        self._saved_hash = self._classic_content_hash()

    def _get_classic_content(self) -> dict:
        return {key: tb.get("1.0", "end-1c")
                for key, tb in self._sections.items()}

    def _classic_content_hash(self) -> str:
        return json.dumps(self._get_classic_content(), sort_keys=True)

    def _is_classic_dirty(self) -> bool:
        return self._classic_content_hash() != self._saved_hash

    def _on_text_change_classic(self, _event=None) -> None:
        if self._debounce_job:
            self.after_cancel(self._debounce_job)
        self._debounce_job = self.after(
            DEBOUNCE_MS, lambda: self._save_draft_classic(silent=True))

    def _save_draft_classic(self, silent: bool = False) -> None:
        """Classic-mode direct save (bypasses controller)."""
        if not self._is_classic_dirty():
            return
        try:
            from pacs_ris_db import get_pacs_db
            # Promote flat content to multilingual format before saving
            flat    = self._get_classic_content()
            payload = json.dumps(
                {_CLASSIC_TO_CTRL[k]: {"fr": v, "en": ""}
                 for k, v in flat.items()},
                ensure_ascii=False,
            )
            db      = get_pacs_db()
            cr_uuid = db.save_compte_rendu(self._item["examen_uuid"], payload)
            if cr_uuid:
                self._cr_uuid    = cr_uuid
                self._saved_hash = self._classic_content_hash()
                if not silent:
                    now = datetime.datetime.now().strftime("%H:%M:%S")
                    self._save_lbl.configure(
                        text=f"Saved {now}", text_color=C.SUCCESS)
                    self.after(3000, lambda: (
                        self._save_lbl.configure(text="")
                        if self.winfo_exists() else None
                    ))
        except Exception as exc:
            logger.error("_save_draft_classic: %s", exc)
            if self.winfo_exists():
                self._save_lbl.configure(
                    text="⚠ Save failed", text_color=C.ERROR)

    # ══════════════════════════════════════════════════════════════════════
    # ACTION BAR  (shared by both modes)
    # ══════════════════════════════════════════════════════════════════════

    def _build_action_bar(self):
        bar = ctk.CTkFrame(
            self, fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            corner_radius=0, height=60,
        )
        bar.grid(row=4, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)

        left_bar = ctk.CTkFrame(bar, fg_color="transparent")
        left_bar.pack(side="left", padx=S.XXL)

        self._save_lbl = ctk.CTkLabel(
            left_bar, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
        )
        self._save_lbl.pack(side="left", padx=(0, S.LG))

        SecondaryButton(
            left_bar, "Save Draft", icon="💾",
            width=130, command=self._save_draft,
        ).pack(side="left", padx=(0, S.SM))

        SecondaryButton(
            left_bar, "Word", icon="📄",
            width=110, command=self._export_word,
        ).pack(side="left", padx=(0, S.SM))

        SecondaryButton(
            left_bar, "Print", icon="🖨",
            width=110, command=self._print_pdf,
        ).pack(side="left")

        right_bar = ctk.CTkFrame(bar, fg_color="transparent")
        right_bar.pack(side="right", padx=S.XXL)

        self._finalize_btn = PrimaryButton(
            right_bar, "Finalize", icon="✓",
            width=130, command=self._finalize,
        )
        self._finalize_btn.pack(side="left", padx=(0, S.SM))

        self._next_btn = ctk.CTkButton(
            right_bar, text="Next Patient  →",
            width=160, height=38,
            fg_color=C.SURFACE_3, hover_color=C.BORDER,
            text_color=C.TEXT_1,
            font=ctk.CTkFont(*F.SUBHEADING),
            border_color=C.BORDER, border_width=1,
            corner_radius=R.MD,
            command=self._next_patient,
        )
        self._next_btn.pack(side="left")

        if self._item.get("statut") == "Finalisé":
            self._finalize_btn.configure(state="disabled",
                                         text="✓  Finalized")

    # ══════════════════════════════════════════════════════════════════════
    # AUTO-SAVE
    # ══════════════════════════════════════════════════════════════════════

    def _start_autosave(self):
        self._autosave_job = self.after(AUTOSAVE_INTERVAL_MS,
                                        self._autosave_tick)

    def _autosave_tick(self):
        if not self.winfo_exists():
            return
        if self._use_structured:
            self._autosave_structured()
        else:
            self._save_draft_classic(silent=True)
        self._autosave_job = self.after(AUTOSAVE_INTERVAL_MS,
                                        self._autosave_tick)

    # ══════════════════════════════════════════════════════════════════════
    # SAVE / FINALIZE  (public, used by action bar and shortcuts)
    # ══════════════════════════════════════════════════════════════════════

    def _save_draft(self, silent: bool = False) -> None:
        if self._use_structured:
            if self._single_tb:
                self._ctrl.update_current_text(
                    self._single_tb.get("1.0", "end-1c"))
            self._ctrl.save(silent=silent)
        else:
            self._save_draft_classic(silent=silent)

    def _finalize(self) -> None:
        if self._use_structured:
            if self._single_tb:
                self._ctrl.update_current_text(
                    self._single_tb.get("1.0", "end-1c"))
            ok = self._ctrl.finalize()
            if ok:
                self._item["statut"] = "Finalisé"
                self._refresh_patient_band()
                self._finalize_btn.configure(state="disabled",
                                             text="✓  Finalized")
                self._show_save_status("Finalized", False)
        else:
            self._save_draft_classic(silent=True)
            try:
                from pacs_ris_db import get_pacs_db
                get_pacs_db().update_examen_statut(
                    self._item["examen_uuid"], "Finalisé")
                self._item["statut"] = "Finalisé"
                self._refresh_patient_band()
                self._finalize_btn.configure(state="disabled",
                                             text="✓  Finalized")
                self._save_lbl.configure(text="Finalized",
                                          text_color=C.SUCCESS)
            except Exception as exc:
                logger.error("_finalize (classic): %s", exc)

    # ══════════════════════════════════════════════════════════════════════
    # NAVIGATION
    # ══════════════════════════════════════════════════════════════════════

    def _go_back(self):
        self._save_draft(silent=True)
        if self._on_back:
            self._on_back()

    def _next_patient(self):
        self._save_draft(silent=True)
        if self._on_get_next:
            next_item = self._on_get_next(self._item.get("examen_uuid"))
            if next_item:
                self._switch_item(next_item)
                return
        if self._on_back:
            self._on_back()

    def _switch_item(self, new_item: dict):
        """Re-use the editor for a new exam (no widget rebuild)."""
        self._item     = dict(new_item)
        self._cr_uuid  = None
        self._saved_hash = None

        self._ctrl.reset_for_new_item(new_item)
        self._refresh_patient_band()

        if self._item.get("statut") == "Finalisé":
            self._finalize_btn.configure(state="disabled", text="✓  Finalized")
        else:
            self._finalize_btn.configure(state="normal", text="✓  Finalize")

        if self._use_structured:
            self._set_single_tb_text("")
        else:
            for tb in self._sections.values():
                tb.delete("1.0", "end")

        self._ctrl.load_async()

    # ══════════════════════════════════════════════════════════════════════
    # TEMPLATE / FORMULA
    # ══════════════════════════════════════════════════════════════════════

    def _on_modality_change(self, value: str):
        """Populate Exam type combo from data[modality].keys()."""
        data      = self._s.get("data", {})
        exam_types = sorted(data.get(value, {}).keys())
        self._exam_combo.configure(values=exam_types or ["—"])
        self._exam_combo.set(exam_types[0] if exam_types else "—")
        self._template_combo.configure(values=["—"])
        self._template_combo.set("—")
        if exam_types:
            self._on_exam_change(exam_types[0])

    def _on_exam_change(self, value: str):
        """Populate Report template combo from DataFrame columns."""
        mod  = self._mod_combo.get()
        data = self._s.get("data", {})
        lm   = self._s.get("lm")

        plan_order = {"free": 0, "solo": 2, "pro": 2, "clinic": 3}
        user_plan  = (lm.get_plan_name() if lm else "free").strip().lower()
        user_level = plan_order.get(user_plan, 0)

        titles = []
        try:
            page_data = data.get(mod, {}).get(value)
            if page_data is not None:
                df_data  = page_data.get("data",  pd.DataFrame()) if isinstance(page_data, dict) else page_data
                df_plans = page_data.get("plans", pd.DataFrame()) if isinstance(page_data, dict) else pd.DataFrame()
                for title in df_data.columns.tolist():
                    req = 0
                    if title in df_plans.columns and len(df_plans):
                        req = plan_order.get(str(df_plans[title].iloc[0] or "free").strip().lower(), 0)
                    if user_level >= req:
                        titles.append(title)
                    else:
                        titles.append(f"[locked] {title}")
        except Exception as exc:
            logger.warning("_on_exam_change: %s", exc)

        self._template_combo.configure(values=titles or ["—"])
        self._template_combo.set(titles[0] if titles else "—")

    def _insert_formula(self):
        """
        Insert the selected Report template into the editor,
        distributing each section (Indication / Technique / Results / Conclusion)
        into the correct tab / textbox.
        """
        mod      = self._mod_combo.get()
        exam     = self._exam_combo.get()
        template = self._template_combo.get()

        if not template or template in ("—", "") or template.startswith("[locked]"):
            return
        try:
            data      = self._s.get("data", {})
            page_data = data.get(mod, {}).get(exam)
            if page_data is None:
                return
            df_data = (page_data.get("data", pd.DataFrame())
                       if isinstance(page_data, dict) else page_data)
            real_title = template.strip()
            if real_title not in df_data.columns:
                return
            val = df_data[real_title].iloc[0]
            content = "" if pd.isna(val) else str(val)
            if not content:
                return

            # Parse the flat template string into per-section text
            parsed = _parse_template(content)

            if self._use_structured:
                # Save current textbox before switching
                if self._single_tb:
                    self._ctrl.update_current_text(
                        self._single_tb.get("1.0", "end-1c"))

                # Write each section directly into the controller buffer
                lang = self._active_lang
                for section_key, text in parsed.items():
                    self._ctrl._buffer[section_key][lang] = text

                # Refresh the view — show resultat tab (usually has the most content)
                focus = next(
                    (k for k in ("resultat", "indication", "technique", "conclusion")
                     if parsed.get(k, "").strip()),
                    "resultat",
                )
                # Sync controller's active section to avoid overwriting on next tab click
                self._ctrl._active_section = focus
                self._active_section = focus
                self._highlight_section_tab(focus)
                self._set_single_tb_text(parsed.get(focus, ""))

            else:
                # Classic mode: fill each textbox
                classic_key_map = {
                    "indication": "indication",
                    "technique":  "technique",
                    "resultat":   "results",
                    "conclusion": "conclusion",
                }
                for ctrl_key, classic_key in classic_key_map.items():
                    tb = self._sections.get(classic_key)
                    if tb:
                        tb.delete("1.0", "end")
                        text = parsed.get(ctrl_key, "")
                        if text:
                            tb.insert("1.0", text)

        except Exception as exc:
            logger.error("_insert_formula: %s", exc)

    # ══════════════════════════════════════════════════════════════════════
    # LANGUAGE CHANGE
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _lang_code_from_display(lang_display: str) -> str:
        """Map a display language name to a 2-letter buffer key."""
        _MAP = {
            "Français": "fr", "French": "fr",
            "English":  "en", "Anglais": "en",
            "Deutsch":  "de", "Español": "es", "Italiano": "it",
            "Português": "pt", "Русский": "ru", "日本語": "ja",
            "中文": "zh", "한국어": "ko", "Türkçe": "tr",
            "Polski": "pl", "Nederlands": "nl", "Svenska": "sv",
            "Norsk": "no", "Dansk": "da", "हिन्दी": "hi",
            "ไทย": "th", "Bahasa Indonesia": "id", "Malay": "ms",
            "Ελληνικά": "el", "Filipino": "fil", "Română": "ro",
        }
        return _MAP.get(lang_display, lang_display.lower()[:2] or "fr")

    def _on_language_change(self, lang: str):
        """
        Reload template data for the new language and refresh the
        Modality / Exam type / Template combos — without losing the
        current patient or report content.
        """
        s = self._s
        try:
            from Comptes_Rendus import AppConstants, TRANSLATIONS, ResourceManager
            from shared_config import save_selected_language
            save_selected_language(lang)
            lang_folder = AppConstants.AVAILABLE_LANGUAGES.get(lang, "Francais")
            s["current_language"]        = lang
            s["current_language_folder"] = lang_folder
            s["translations"]            = TRANSLATIONS.get(lang, TRANSLATIONS.get("English", {}))
            s["translations_map"]        = TRANSLATIONS
            user_plan = s["lm"].get_plan_name() if s.get("lm") else "free"
            s["data"] = ResourceManager.load_excel_data(lang_folder, user_plan=user_plan)
        except Exception as e:
            logger.error("Language change error: %s", e)
            return

        # Sync active writing language with the chosen report language
        lang_code = self._lang_code_from_display(lang)
        self._active_lang = lang_code
        self._ctrl._active_lang = lang_code

        # Refresh the modality combo with the new data, keeping current selection
        data = s.get("data", {})
        modalities = list(data.keys())
        current_mod = self._mod_combo.get()
        self._mod_combo.configure(values=modalities or ["—"])
        if current_mod in modalities:
            self._mod_combo.set(current_mod)
            self._on_modality_change(current_mod)
        elif modalities:
            self._mod_combo.set(modalities[0])
            self._on_modality_change(modalities[0])

    # ══════════════════════════════════════════════════════════════════════
    # GLOBAL SEARCH
    # ══════════════════════════════════════════════════════════════════════

    def _open_search(self):
        from ui.dialogs.formula_search import FormulaSearchDialog
        dlg = FormulaSearchDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            on_select=self._apply_search_result,
        )
        dlg.grab_set()

    def _apply_search_result(self, result: dict):
        """
        Called when the user picks a template in the global search dialog.
        Parses the content into sections and distributes to editor tabs —
        same logic as _insert_formula.
        """
        content = result.get("formula_content") or result.get("content", "")
        if not content:
            return

        # Sync combo selectors to reflect the chosen template
        mod  = result.get("modality_key") or result.get("modality", "")
        exam = result.get("exam_type", "")
        title= result.get("title", "")
        data = self._s.get("data", {})
        if mod in data:
            self._mod_combo.set(mod)
            self._on_modality_change(mod)
            if exam:
                self._exam_combo.set(exam)
                self._on_exam_change(exam)
                if title:
                    self._template_combo.set(title)

        parsed = _parse_template(content)

        if self._use_structured:
            if self._single_tb:
                self._ctrl.update_current_text(
                    self._single_tb.get("1.0", "end-1c"))
            lang = self._active_lang
            for section_key, text in parsed.items():
                self._ctrl._buffer[section_key][lang] = text
            focus = next(
                (k for k in ("resultat", "indication", "technique", "conclusion")
                 if parsed.get(k, "").strip()),
                "resultat",
            )
            # Sync controller's active section to avoid overwriting on next tab click
            self._ctrl._active_section = focus
            self._active_section = focus
            self._highlight_section_tab(focus)
            self._set_single_tb_text(parsed.get(focus, ""))
        else:
            classic_key_map = {
                "indication": "indication",
                "technique":  "technique",
                "resultat":   "results",
                "conclusion": "conclusion",
            }
            for ctrl_key, classic_key in classic_key_map.items():
                tb = self._sections.get(classic_key)
                if tb:
                    tb.delete("1.0", "end")
                    text = parsed.get(ctrl_key, "")
                    if text:
                        tb.insert("1.0", text)

    # ══════════════════════════════════════════════════════════════════════
    # WORD / PRINT EXPORT
    # ══════════════════════════════════════════════════════════════════════

    def _export_word(self):
        if not self._do_word:
            return
        self._save_draft(silent=True)

        if self._use_structured:
            if self._single_tb:
                self._ctrl.update_current_text(
                    self._single_tb.get("1.0", "end-1c"))
            payload = self._ctrl.build_word_payload(
                medecin       = self._med.get(),
                etablissement = self._etab.get(),
                language_display = self._lang_combo.get(),
            )
        else:
            # Classic: build payload manually from textboxes
            content   = self._get_classic_content()
            full_text = "\n\n".join(
                f"{k.upper()}\n{v}" for k, v in content.items() if v.strip()
            )
            payload = {
                "formula":       full_text,
                "formula_name":  (self._template_combo.get()
                                  if hasattr(self, "_template_combo") else ""),
                "modality":      self._item.get("modalite",    ""),
                "exam_type":     self._item.get("type_examen", ""),
                "medecin":       self._med.get(),
                "etablissement": self._etab.get(),
                "language":      self._lang_combo.get(),
                "patient_data":  self._item,
                "examen_data":   self._item,
            }

        threading.Thread(
            target=self._do_word, args=(payload,), daemon=True).start()

    def _print_pdf(self):
        """Open the Print / Save PDF dialog (same payload logic as Word export)."""
        if not self._do_print:
            return
        self._save_draft(silent=True)

        if self._use_structured:
            if self._single_tb:
                self._ctrl.update_current_text(
                    self._single_tb.get("1.0", "end-1c"))
            payload = self._ctrl.build_word_payload(
                medecin          = self._med.get(),
                etablissement    = self._etab.get(),
                language_display = self._lang_combo.get(),
            )
        else:
            content   = self._get_classic_content()
            full_text = "\n\n".join(
                f"{k.upper()}\n{v}" for k, v in content.items() if v.strip()
            )
            payload = {
                "formula":       full_text,
                "formula_name":  (self._template_combo.get()
                                  if hasattr(self, "_template_combo") else ""),
                "modality":      self._item.get("modalite",    ""),
                "exam_type":     self._item.get("type_examen", ""),
                "medecin":       self._med.get(),
                "etablissement": self._etab.get(),
                "language":      self._lang_combo.get(),
                "patient_data":  self._item,
                "examen_data":   self._item,
            }

        self._do_print(payload)

    # ══════════════════════════════════════════════════════════════════════
    # DICTATION
    # ══════════════════════════════════════════════════════════════════════

    def _toggle_dictation(self):
        try:
            from whisper_dictation import WhisperDictation
            if not hasattr(self, "_whisper") or self._whisper is None:
                self._whisper = WhisperDictation(model_size="medium")
                self._whisper.start(callback=self._on_dictation_text)
                self._dict_btn.configure(
                    text="⏹  Stop Dictation",
                    fg_color=C.ERROR_DIM, text_color=C.ERROR,
                    border_color=C.ERROR,
                )
            else:
                self._whisper.stop()
                self._whisper = None
                self._dict_btn.configure(
                    text="🎤  Dictate",
                    fg_color=C.SURFACE, text_color=C.TEXT_2,
                    border_color=C.BORDER,
                )
        except ImportError:
            logger.warning("whisper_dictation not available")
        except Exception as exc:
            logger.error("Dictation error: %s", exc)

    def _on_dictation_text(self, text: str) -> None:
        """
        Handle dictated text.

        Structured mode: routes through the controller so the buffer stays
        in sync, then refreshes the single textbox.
        Classic mode: appends directly into the results textbox (legacy).
        """
        if self._use_structured:
            # Controller inserts into active section and fires on_content_ready
            self._ctrl.on_dictation_text(text)
            # Refresh the single textbox on the main thread
            self.after(0, lambda: self._set_single_tb_text(
                self._ctrl.get_current_text()))
        else:
            self.after(0, lambda: self._insert_text_at_cursor("results", text))

    def _insert_text_at_cursor(self, section: str, text: str) -> None:
        tb = self._sections.get(section)
        if tb:
            try:
                idx = tb.index("insert")
                tb.insert(idx, text + " ")
            except Exception:
                tb.insert("end", text + " ")

    # ══════════════════════════════════════════════════════════════════════
    # KEYBOARD SHORTCUTS
    # ══════════════════════════════════════════════════════════════════════

    def _bind_shortcuts(self):
        root = self.winfo_toplevel()
        root.bind("<Control-s>",      self._on_ctrl_s,     add="+")
        root.bind("<Control-Return>", self._on_ctrl_enter, add="+")

    def _unbind_shortcuts(self):
        try:
            root = self.winfo_toplevel()
            root.unbind("<Control-s>")
            root.unbind("<Control-Return>")
        except Exception:
            pass

    def _on_ctrl_s(self, _event=None):
        self._save_draft()
        return "break"

    def _on_ctrl_enter(self, _event=None):
        self._next_patient()
        return "break"

    # ══════════════════════════════════════════════════════════════════════
    # CLEANUP
    # ══════════════════════════════════════════════════════════════════════

    def destroy(self):
        self._unbind_shortcuts()
        for job in (self._debounce_job, self._autosave_job):
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        super().destroy()


# ── Module-level helpers ───────────────────────────────────────────────────────

def _parse_content(raw: str) -> dict:
    """
    Parse stored contenu into a flat section dict for classic mode.
    Supports JSON (multilingual or legacy flat) and plain text.
    """
    empty = {k: "" for k, *_ in _CLASSIC_SECTIONS}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            first_val = next(iter(data.values()), None)
            if isinstance(first_val, dict):
                # Multilingual format — extract 'fr' for classic display
                return {
                    "indication": data.get("indication", {}).get("fr", ""),
                    "technique":  data.get("technique",  {}).get("fr", ""),
                    "results":    data.get("resultat",   {}).get("fr", ""),
                    "conclusion": data.get("conclusion", {}).get("fr", ""),
                }
            # Legacy flat
            return {
                "indication": data.get("indication", ""),
                "technique":  data.get("technique",  ""),
                "results":    data.get("results",    data.get("resultat", "")),
                "conclusion": data.get("conclusion", ""),
            }
    except (json.JSONDecodeError, TypeError):
        pass
    return {**empty, "results": raw or ""}


def _calc_age(ddn: str) -> str:
    if not ddn:
        return "—"
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.datetime.strptime(ddn, fmt)
            return str((datetime.date.today() - dt.date()).days // 365)
        except ValueError:
            continue
    return "—"
