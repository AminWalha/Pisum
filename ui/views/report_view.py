# -*- coding: utf-8 -*-
"""
ui/views/report_view.py — Main Report Creation View
Replicates all logic from ModernFormulaFrame's report panel.
"""
import threading
import logging
import pyperclip
import pandas as pd
import customtkinter as ctk

from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, LabeledEntry, LabeledCombo,
    PrimaryButton, SecondaryButton, GhostButton,
    SectionLabel, Divider, Badge,
)
from report_editor_controller import _match_section_header, _CTRL_HEADER_MAP

logger = logging.getLogger(__name__)


# ── Section header → UI key  (all languages, lowercase, stripped) ──────────
# Built dynamically from Comptes_Rendus.TRANSLATIONS['sections'].
# Rule: index 0 → indication, index 1 → technique,
#       index -1 → conclusion, everything in between → resultat.
# Falls back to the static map below if TRANSLATIONS is unavailable.

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


# Static fallback (kept in case Comptes_Rendus is unavailable at import time)
_HEADER_MAP_STATIC: dict[str, str] = {
    # ── French ────────────────────────────────────────────────────────────
    "indication :"           : "indication",
    "technique :"            : "technique",
    "résultat :"             : "resultat",
    "résultats :"            : "resultat",
    "conclusion :"           : "conclusion",
    # ── English ───────────────────────────────────────────────────────────
    "clinical indication:"   : "indication",
    "imaging technique:"     : "technique",
    "findings:"              : "resultat",
    "impression:"            : "conclusion",
    "recommendations:"       : "conclusion",
    # ── Spanish ───────────────────────────────────────────────────────────
    "indicación clínica:"    : "indication",
    "técnica de imagen:"     : "technique",
    "hallazgos:"             : "resultat",
    "impresión:"             : "conclusion",
    "recomendaciones:"       : "conclusion",
    # ── German ────────────────────────────────────────────────────────────
    "klinische indikation:"  : "indication",
    "bildgebungstechnik:"    : "technique",
    "befunde:"               : "resultat",
    "beurteilung:"           : "conclusion",
    "empfehlungen:"          : "conclusion",
    # ── Italian ───────────────────────────────────────────────────────────
    "indicazione clinica:"   : "indication",
    "tecnica di imaging:"    : "technique",
    "reperti:"               : "resultat",
    "conclusione:"           : "conclusion",
    "raccomandazioni:"       : "conclusion",
    # ── Portuguese ────────────────────────────────────────────────────────
    "indicação clínica:"     : "indication",
    "técnica de imagem:"     : "technique",
    "achados:"               : "resultat",
    "impressão:"             : "conclusion",
    "recomendações:"         : "conclusion",
    # ── Russian ───────────────────────────────────────────────────────────
    "клинические показания:" : "indication",
    "методика:"              : "technique",
    "результаты:"            : "resultat",
    "заключение:"            : "conclusion",
    "рекомендации:"          : "conclusion",
    # ── Chinese simplified ────────────────────────────────────────────────
    "临床指征："               : "indication",
    "影像技术："               : "technique",
    "影像所见："               : "resultat",
    "影像诊断："               : "conclusion",
    "建议："                  : "conclusion",
    # ── Japanese ─────────────────────────────────────────────────────────
    "臨床適応："               : "indication",
    "検査手法："               : "technique",
    "所見："                  : "resultat",
    "診断："                  : "conclusion",
    "推奨事項："               : "conclusion",
    # ── Turkish ───────────────────────────────────────────────────────────
    "klinik endikasyon:"     : "indication",
    "görüntüleme tekniği:"   : "technique",
    "bulgular:"              : "resultat",
    "sonuç:"                 : "conclusion",
    "öneriler:"              : "conclusion",
    # ── Swedish ───────────────────────────────────────────────────────────
    "klinisk indikation:"    : "indication",
    "undersökningsmetodik:"  : "technique",
    "fynd:"                  : "resultat",
    "bedömning:"             : "conclusion",
    "rekommendationer:"      : "conclusion",
    # ── Norwegian ─────────────────────────────────────────────────────────
    "klinisk indikasjon:"    : "indication",
    "undersøkelsesteknikk:"  : "technique",
    "funn:"                  : "resultat",
    "konklusjon:"            : "conclusion",
    "anbefalinger:"          : "conclusion",
    # ── Danish ────────────────────────────────────────────────────────────
    "billedteknik:"          : "technique",
    "fund:"                  : "resultat",
    "konklusion:"            : "conclusion",
    # ── Dutch ─────────────────────────────────────────────────────────────
    "klinische indicatie:"   : "indication",
    "onderzoekstechniek:"    : "technique",
    "bevindingen:"           : "resultat",
    "conclusie:"             : "conclusion",
    "aanbevelingen:"         : "conclusion",
    # ── Korean ────────────────────────────────────────────────────────────
    "임상 적응증:"             : "indication",
    "검사 방법:"              : "technique",
    "영상 소견:"              : "resultat",
    "결론:"                  : "conclusion",
    "권고 사항:"              : "conclusion",
    # ── Indonesian ────────────────────────────────────────────────────────
    "indikasi klinis:"       : "indication",
    "teknik pencitraan:"     : "technique",
    "temuan:"                : "resultat",
    "kesimpulan:"            : "conclusion",
    "saran:"                 : "conclusion",
    # ── Thai ──────────────────────────────────────────────────────────────
    "ข้อบ่งชี้ทางคลินิก:"     : "indication",
    "เทคนิคการตรวจ:"          : "technique",
    "ผลการตรวจ:"              : "resultat",
    "สรุปผล:"                : "conclusion",
    "คำแนะนำ:"               : "conclusion",
    # ── Polish ────────────────────────────────────────────────────────────
    "wskazania kliniczne:"   : "indication",
    "technika badania:"      : "technique",
    "wyniki:"                : "resultat",
    "wnioski:"               : "conclusion",
    "zalecenia:"             : "conclusion",
    # ── Romanian ──────────────────────────────────────────────────────────
    "indicație clinică:"     : "indication",
    "tehnică imagistică:"    : "technique",
    "rezultate:"             : "resultat",
    "concluzie:"             : "conclusion",
    "recomandări:"           : "conclusion",
    # ── Malay ─────────────────────────────────────────────────────────────
    "indikasi klinikal:"     : "indication",
    "teknik imej:"           : "technique",
    "penemuan:"              : "resultat",
    "cadangan:"              : "conclusion",
    # ── Greek ─────────────────────────────────────────────────────────────
    "κλινική ένδειξη:"       : "indication",
    "τεχνική:"               : "technique",
    "ευρήματα:"              : "resultat",
    "συμπέρασμα:"            : "conclusion",
    "συστάσεις:"             : "conclusion",
    # ── Filipino ──────────────────────────────────────────────────────────
    "indikasyong klinikal:"  : "indication",
    "teknik ng imaging:"     : "technique",
    "resulta:"               : "resultat",
    "konklusyon:"            : "conclusion",
    "rekomendasyon:"         : "conclusion",
    # ── Hindi ─────────────────────────────────────────────────────────────
    "इमेजिंग तकनीक:"          : "technique",
    "निष्कर्ष:"               : "resultat",
    "प्रभाव:"                 : "conclusion",
    "सिफारिशें:"              : "conclusion",
}

# Merge: dynamic map wins; static entries fill any gaps
_HEADER_MAP: dict[str, str] = {**_HEADER_MAP_STATIC, **_build_header_map()}


def get_section_labels(lang: str) -> dict[str, str]:
    """
    Return {key: display_label} for the 4 UI sections in the given language.
    Reads directly from Comptes_Rendus.TRANSLATIONS[lang]['sections'].
    Rule: sections[0]=indication, sections[1]=technique,
          sections[-1]=conclusion, first middle=resultat.
    Falls back to English if lang not found.
    """
    defaults = {
        "indication": "Indication",
        "technique":  "Technique",
        "resultat":   "Résultats",
        "conclusion": "Conclusion",
    }
    try:
        from Comptes_Rendus import TRANSLATIONS
        t = TRANSLATIONS.get(lang) or TRANSLATIONS.get("English", {})
        sections = t.get("sections", [])
        n = len(sections)
        if n >= 4:
            # Strip trailing colon/space for display
            def clean(s):
                return s.rstrip(": ").rstrip("：")
            defaults["indication"] = clean(sections[0])
            defaults["technique"]  = clean(sections[1])
            # first "middle" header = resultat label
            defaults["resultat"]   = clean(sections[2])
            defaults["conclusion"] = clean(sections[n - 1])
    except Exception:
        pass
    return defaults


# ── Plan ordering ──────────────────────────────────────────────────────────
PLAN_ORDER = {"free": 0, "solo": 2, "pro": 2, "clinic": 3}


def _normalize_plan(plan):
    p = str(plan).lower() if plan else "free"
    for k in ("free", "solo", "pro", "clinic"):
        if k in p:
            return k
    return "free"


class ReportView(ctk.CTkFrame):
    """
    Full report-creation view.

    Params
    ------
    core_state : dict with keys:
        data, lm, config_manager, custom_formulas_db,
        translations, current_language, current_language_folder,
        pacs_state   (the module-level _PACS_STATE dict)
    on_navigate  : callable(key) — triggers sidebar nav change
    on_open_word : callable(payload) — open Word dialog
    on_print     : callable(payload)
    on_open_pacs : callable()
    """

    def __init__(self, master, core_state: dict, on_navigate=None,
                 on_open_word=None, on_print=None, on_open_pacs=None, **kw):
        kw.setdefault("fg_color", C.BG)
        super().__init__(master, **kw)

        self._s          = core_state
        self._nav        = on_navigate
        self._do_word    = on_open_word
        self._do_print   = on_print

        # Internal state
        self._custom_cache: dict = {}   # "✨ title" → content
        self._dictating    = False
        self._whisper      = None       # WhisperDictation instance

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)   # left panel
        self.grid_columnconfigure(1, weight=1)   # right panel

        self._build_left()
        self._build_right()
        self._populate_modalities()
        self._restore_config()

    # ══════════════════════════════════════════════════════════════════════
    # BUILD — LEFT SELECTION PANEL
    # ══════════════════════════════════════════════════════════════════════
    def _build_left(self):
        left = ctk.CTkFrame(
            self,
            fg_color=C.SURFACE,
            border_color=C.BORDER,
            border_width=1,
            corner_radius=0,
            width=300,
        )
        left.grid(row=0, column=0, sticky="nsew")
        left.pack_propagate(False)

        scroll = ctk.CTkScrollableFrame(
            left, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        scroll.pack(fill="both", expand=True, padx=S.LG, pady=S.LG)
        scroll.grid_columnconfigure(0, weight=1)

        # ── Section: Doctor info ───────────────────────────────────────
        SectionLabel(scroll, "Practitioner").grid(
            row=0, column=0, sticky="ew", pady=(0, S.SM))

        self._etab = LabeledEntry(
            scroll, "Facility",
            placeholder="e.g. St-Mary Hospital"
        )
        self._etab.grid(row=1, column=0, sticky="ew", pady=(0, S.MD))

        self._med = LabeledEntry(
            scroll, "Radiologist",
            placeholder="e.g. Dr. Smith"
        )
        self._med.grid(row=2, column=0, sticky="ew", pady=(0, S.LG))

        Divider(scroll).grid(row=3, column=0, sticky="ew", pady=(0, S.LG))

        # ── Section: Template selection ────────────────────────────────
        SectionLabel(scroll, "Template selection").grid(
            row=4, column=0, sticky="ew", pady=(0, S.SM))

        self._modality_combo = LabeledCombo(
            scroll, "Modality", command=self._on_modality_change
        )
        self._modality_combo.grid(row=5, column=0, sticky="ew", pady=(0, S.MD))

        self._exam_combo = LabeledCombo(
            scroll, "Exam type", command=self._on_exam_change
        )
        self._exam_combo.grid(row=6, column=0, sticky="ew", pady=(0, S.MD))

        self._title_combo = LabeledCombo(
            scroll, "Report template", command=self._on_title_change
        )
        self._title_combo.grid(row=7, column=0, sticky="ew", pady=(0, S.MD))

        # Language selector row
        lang_row = ctk.CTkFrame(scroll, fg_color="transparent")
        lang_row.grid(row=8, column=0, sticky="ew", pady=(0, S.LG))
        lang_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            lang_row, text="Language",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, S.XS))

        self._lang_combo = ctk.CTkComboBox(
            lang_row,
            values=self._get_language_list(),
            command=self._on_language_change,
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            button_color=C.SURFACE_3, button_hover_color=C.BORDER_2,
            text_color=C.TEXT_1,
            dropdown_fg_color=C.SURFACE_2, dropdown_text_color=C.TEXT_1,
            dropdown_hover_color=C.SURFACE_3,
            font=ctk.CTkFont(*F.BODY),
            dropdown_font=ctk.CTkFont(*F.BODY_SM),
            height=36, corner_radius=R.MD,
            width=180,
        )
        self._lang_combo.grid(row=1, column=0, sticky="ew", columnspan=2)
        current_lang = self._s.get("current_language", "Français")
        self._lang_combo.set(current_lang)

        Divider(scroll).grid(row=9, column=0, sticky="ew", pady=(0, S.LG))

        # ── Section: Search ────────────────────────────────────────────
        SectionLabel(scroll, "Search").grid(
            row=10, column=0, sticky="ew", pady=(0, S.SM))

        SecondaryButton(
            scroll, "Search All Templates", icon="⊕",
            command=self._open_search,
        ).grid(row=11, column=0, sticky="ew", pady=(0, S.LG))

        Divider(scroll).grid(row=12, column=0, sticky="ew", pady=(0, S.LG))

        # ── Section: Custom formulas ───────────────────────────────────
        SectionLabel(scroll, "Custom Formulas").grid(
            row=13, column=0, sticky="ew", pady=(0, S.SM))

        can_custom = self._s.get("lm") and self._s["lm"].can_use_feature("custom_templates")

        if can_custom:
            GhostButton(
                scroll, "Add Formula", icon="+",
                command=self._add_custom_formula,
            ).grid(row=18, column=0, sticky="ew", pady=(0, S.XS))
            GhostButton(
                scroll, "Manage Formulas", icon="☰",
                command=self._manage_custom_formulas,
            ).grid(row=19, column=0, sticky="ew", pady=(0, S.LG))
        else:
            ctk.CTkLabel(
                scroll,
                text="Custom formulas — SOLO plan",
                font=ctk.CTkFont(*F.CAPTION),
                text_color=C.TEXT_3, anchor="w",
            ).grid(row=18, column=0, sticky="w")
            GhostButton(
                scroll, "Upgrade plan", icon="◈", color=C.GOLD,
                command=lambda: self._nav("license") if self._nav else None,
            ).grid(row=19, column=0, sticky="ew", pady=(0, S.LG))

    # ══════════════════════════════════════════════════════════════════════
    # BUILD — RIGHT REPORT PANEL
    # ══════════════════════════════════════════════════════════════════════

    # Section keys (internal) → default French labels + textbox height
    _SECTION_CFG = [
        ("indication", "Indication",  80),
        ("technique",  "Technique",   80),
        ("resultat",   "Résultats",  380),
        ("conclusion", "Conclusion", 100),
    ]

    def _build_right(self):
        right = ctk.CTkFrame(self, fg_color=C.BG, corner_radius=0)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # ── Top bar ────────────────────────────────────────────────────
        top_bar = ctk.CTkFrame(right, fg_color="transparent", height=56)
        top_bar.grid(row=0, column=0, sticky="ew",
                     padx=S.XL, pady=(S.LG, S.MD))
        top_bar.grid_columnconfigure(0, weight=1)
        top_bar.pack_propagate(False)

        self._report_title_lbl = ctk.CTkLabel(
            top_bar,
            text="Report Content",
            font=ctk.CTkFont(*F.TITLE),
            text_color=C.TEXT_1, anchor="w",
        )
        self._report_title_lbl.grid(row=0, column=0, sticky="w")

        self._dict_btn = ctk.CTkButton(
            top_bar,
            text="  Dictation",
            fg_color=C.SURFACE_3, hover_color=C.BORDER,
            text_color=C.TEXT_2, border_color=C.BORDER, border_width=1,
            font=ctk.CTkFont(*F.BODY_SM),
            height=34, width=130, corner_radius=R.MD,
            command=self._toggle_dictation,
        )
        self._dict_btn.grid(row=0, column=1, padx=(S.SM, 0))

        self._ai_btn = ctk.CTkButton(
            top_bar,
            text="✨ AI",
            width=60, height=34,
            fg_color=C.GOLD_DIM,
            hover_color=C.SURFACE_3,
            text_color=C.GOLD,
            border_color=C.GOLD_DIM, border_width=1,
            font=ctk.CTkFont(*F.BODY_SM),
            corner_radius=R.MD,
            command=self._on_ai_enhance,
        )
        self._ai_btn.grid(row=0, column=2, padx=(S.SM, 0))
        self._ai_spin_job = None

        # ── Scrollable sections area ────────────────────────────────────
        self._section_texts: dict[str, ctk.CTkTextbox] = {}

        sections_outer = ctk.CTkScrollableFrame(
            right, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        sections_outer.grid(row=1, column=0, sticky="nsew",
                            padx=S.XL, pady=(0, S.MD))
        sections_outer.grid_columnconfigure(0, weight=1)

        # Resolve labels for the current language
        lang   = self._s.get("current_language", "Français")
        labels = get_section_labels(lang)

        # Build one label + textbox per section
        for grid_row, (key, default_label, height) in enumerate(self._SECTION_CFG):
            label_text = labels.get(key, default_label)

            # Section header row
            hdr = ctk.CTkFrame(sections_outer, fg_color="transparent")
            hdr.grid(row=grid_row * 2, column=0,
                     sticky="ew", pady=(S.MD if grid_row else 0, S.XS))
            hdr.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                hdr,
                text=label_text.upper(),
                font=ctk.CTkFont("Segoe UI", 10, "bold"),
                text_color=C.TEAL, anchor="w",
            ).grid(row=0, column=0, sticky="w")

            Divider(hdr).grid(row=1, column=0, sticky="ew", pady=(S.XS, 0))

            # Textbox
            tb = ctk.CTkTextbox(
                sections_outer,
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
            tb.grid(row=grid_row * 2 + 1, column=0,
                    sticky="ew", pady=(0, S.SM))
            tb.bind("<KeyRelease>", self._update_char_count)
            self._section_texts[key] = tb

        # Flash notification overlay (placed over sections_outer)
        self._dict_status = ctk.CTkLabel(
            right, text="",
            fg_color=C.TEAL_DIM, text_color=C.TEAL,
            font=ctk.CTkFont(*F.BODY_SM),
            corner_radius=R.MD,
        )

        # ── Action bar ─────────────────────────────────────────────────
        action = ctk.CTkFrame(right, fg_color="transparent", height=56)
        action.grid(row=2, column=0, sticky="ew",
                    padx=S.XL, pady=(0, S.LG))
        action.grid_columnconfigure(0, weight=1)
        action.pack_propagate(False)

        btn_row = ctk.CTkFrame(action, fg_color="transparent")
        btn_row.pack(side="right")

        lm = self._s.get("lm")
        can_print = not lm or lm.can_use_feature("printing")

        PrimaryButton(
            btn_row, "Copy", icon="⎘", width=110,
            command=self._copy_formula,
        ).pack(side="left", padx=(0, S.SM))

        SecondaryButton(
            btn_row, "Word", icon="◧", width=110,
            command=self._open_word,
        ).pack(side="left", padx=(0, S.SM))

        if can_print:
            SecondaryButton(
                btn_row, "Print", icon="⊞", width=110,
                command=self._print,
            ).pack(side="left", padx=(0, S.SM))
        else:
            ctk.CTkButton(
                btn_row, text="⊞  Print",
                width=110, height=38,
                fg_color=C.SURFACE_3, hover_color=C.SURFACE_3,
                text_color=C.TEXT_3,
                border_color=C.BORDER, border_width=1,
                font=ctk.CTkFont(*F.SUBHEADING),
                corner_radius=R.MD,
                command=lambda: self._nav("license") if self._nav else None,
            ).pack(side="left", padx=(0, S.SM))

        self._char_lbl = ctk.CTkLabel(
            action, text="0 chars",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
        )
        self._char_lbl.pack(side="left")

    # ══════════════════════════════════════════════════════════════════════
    # DATA POPULATION
    # ══════════════════════════════════════════════════════════════════════
    def _populate_modalities(self):
        data = self._s.get("data", {})
        t    = self._s.get("translations", {})
        mods = t.get("modalites", {})
        # show translated names
        names = [mods.get(k, k) for k in data.keys()]
        self._modality_combo.configure(values=names)
        if names:
            self._modality_combo.set(names[0])
            self._on_modality_change(names[0])

    def _on_modality_change(self, selected_display=None):
        selected_display = selected_display or self._modality_combo.get()
        t   = self._s.get("translations", {})
        rev = {v: k for k, v in t.get("modalites", {}).items()}
        key = rev.get(selected_display, selected_display)

        data   = self._s.get("data", {})
        pages  = set(data.get(key, {}).keys())

        # merge custom formula exam types
        db = self._s.get("custom_formulas_db")
        if db:
            lang_folder = self._s.get("current_language_folder", "Francais")
            for cf in db.get_formulas_by_language(lang_folder):
                cf_mod = cf.get("modality", "")
                if cf_mod in (key, t.get("modalites", {}).get(key, key)):
                    pages.add(cf.get("exam_type", ""))

        values = sorted(pages)
        self._exam_combo.configure(values=values)
        self._exam_combo.set(values[0] if values else "")
        self._title_combo.configure(values=[])
        self._title_combo.set("")
        self._clear_text()
        if values:
            self._on_exam_change(values[0])

    def _on_exam_change(self, selected=None):
        selected = selected or self._exam_combo.get()
        t        = self._s.get("translations", {})
        rev      = {v: k for k, v in t.get("modalites", {}).items()}
        mod_disp = self._modality_combo.get()
        mod_key  = rev.get(mod_disp, mod_disp)
        data     = self._s.get("data", {})
        lm       = self._s.get("lm")
        user_plan= _normalize_plan(lm.get_plan_name() if lm else "free")

        titles = []
        self._custom_cache = {}

        if mod_key in data and selected in data[mod_key]:
            page_data = data[mod_key][selected]
            df_data  = page_data.get("data",  pd.DataFrame()) if isinstance(page_data, dict) else page_data
            df_plans = page_data.get("plans", pd.DataFrame()) if isinstance(page_data, dict) else pd.DataFrame()

            def plan_order(title):
                if title in df_plans.columns and len(df_plans):
                    return PLAN_ORDER.get(_normalize_plan(df_plans[title].iloc[0]), 0)
                return 0

            std = sorted(df_data.columns.tolist(), key=lambda t: (plan_order(t), t))
            for title in std:
                order = plan_order(title)
                if order == 0:
                    titles.append(f"  {title}")
                elif PLAN_ORDER.get(user_plan, 0) >= order:
                    titles.append(title)
                else:
                    titles.append(f"[locked] {title}")

        # custom formulas
        db = self._s.get("custom_formulas_db")
        if db:
            lang_folder = self._s.get("current_language_folder", "Francais")
            for cf in db.get_formulas_by_language(lang_folder):
                cf_mod = cf.get("modality", "")
                if (cf_mod in (mod_key, t.get("modalites", {}).get(mod_key, mod_key))
                        and cf.get("exam_type") == selected):
                    tag = f"* {cf['title']}"
                    titles.append(tag)
                    self._custom_cache[tag] = cf["formula_content"]

        self._title_combo.configure(values=titles)
        self._title_combo.set(titles[0] if titles else "")
        self._clear_text()
        if titles:
            self._on_title_change(titles[0])

    def _on_title_change(self, selected=None):
        selected = selected or self._title_combo.get()
        if not selected:
            return

        # Custom formula
        if selected in self._custom_cache:
            self._set_text(self._custom_cache[selected])
            return

        # Locked formula
        if selected.startswith("[locked]"):
            real_title = selected[len("[locked] "):]
            self._set_text(
                f"[LOCKED] Upgrade your plan to access:\n{real_title}"
            )
            self._show_flash("Upgrade your plan to access this template.", "warning")
            return

        # Clean title (may have leading spaces)
        real_title = selected.strip()

        t       = self._s.get("translations", {})
        rev     = {v: k for k, v in t.get("modalites", {}).items()}
        mod_key = rev.get(self._modality_combo.get(), self._modality_combo.get())
        page    = self._exam_combo.get()
        data    = self._s.get("data", {})

        try:
            if mod_key in data and page in data[mod_key]:
                page_data = data[mod_key][page]
                df_data   = page_data.get("data", pd.DataFrame()) if isinstance(page_data, dict) else page_data
                if real_title in df_data.columns:
                    val = df_data[real_title].iloc[0]
                    if pd.isna(val):
                        self._set_text("")
                    else:
                        self._set_text(str(val))
        except Exception as e:
            logger.error(f"display_formula error: {e}")

    def _get_language_list(self):
        try:
            from Comptes_Rendus import AppConstants
            return list(AppConstants.AVAILABLE_LANGUAGES.keys())
        except Exception:
            return ["Français", "English", "Deutsch", "Español"]

    # ══════════════════════════════════════════════════════════════════════
    # TEXT AREA HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _parse_into_sections(self, content: str) -> dict:
        """
        Split a flat template string into {section_key: text}.

        Uses the module-level _HEADER_MAP which covers every language
        from Comptes_Rendus.py (exact strings, lowercase).

        A line is treated as a section header when its normalized form
        (lowercase, ▸ stripped) exists in _HEADER_MAP.
        Content lines are appended to the last matched section.
        Falls back to 'resultat' when no header is ever recognized.
        """
        keys   = [k for k, *_ in self._SECTION_CFG]
        result = {k: [] for k in keys}

        current_key: str | None = None
        combined_map = {**_HEADER_MAP, **_CTRL_HEADER_MAP}

        for raw_line in content.splitlines():
            matched_key = _match_section_header(raw_line, combined_map)
            if matched_key:
                current_key = matched_key
            elif current_key is not None:
                result[current_key].append(
                    raw_line.replace("▸ ", "").replace("▸", "").strip()
                )

        # Fallback: no section headers found at all
        if current_key is None:
            result["resultat"] = [content.replace("▸ ", "").replace("▸", "").strip()]

        return {k: "\n".join(v).strip() for k, v in result.items()}

    def _set_text(self, content: str):
        """Parse content and fill each section textbox."""
        self._clear_text()
        if not content:
            return

        parsed = self._parse_into_sections(content)
        for key, tb in self._section_texts.items():
            text = parsed.get(key, "")
            if text:
                tb.delete("1.0", "end")
                tb.insert("1.0", text)

        self._update_char_count()

    def _clear_text(self):
        for tb in self._section_texts.values():
            tb.delete("1.0", "end")
        self._update_char_count()

    def get_text(self) -> str:
        """Collect all sections into a single formatted string."""
        t       = self._s.get("translations", {})
        headers = t.get("sections", [])
        keys    = [k for k, *_ in self._SECTION_CFG]

        parts = []
        for i, key in enumerate(keys):
            label = (headers[i] if i < len(headers)
                     else self._SECTION_CFG[i][1])
            text  = self._section_texts[key].get("1.0", "end-1c").strip()
            if text:
                parts.append(f"▸ {label}\n{text}")

        return "\n".join(parts)

    def _update_char_count(self, _e=None):
        total = sum(
            len(tb.get("1.0", "end-1c"))
            for tb in self._section_texts.values()
        )
        self._char_lbl.configure(text=f"{total:,} chars")

    # ══════════════════════════════════════════════════════════════════════
    # CONFIG PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════
    def _restore_config(self):
        cfg = self._s.get("config_manager")
        if cfg:
            self._etab.set(cfg.get("etablissement", ""))
            self._med.set(cfg.get("medecin", ""))
        # bind save
        self._etab.bind("<FocusOut>", self._save_config)
        self._med.bind("<FocusOut>", self._save_config)

    def _save_config(self, _e=None):
        cfg = self._s.get("config_manager")
        if cfg:
            cfg.set("etablissement", self._etab.get())
            cfg.set("medecin", self._med.get())

    # ══════════════════════════════════════════════════════════════════════
    # ACTIONS
    # ══════════════════════════════════════════════════════════════════════
    def _copy_formula(self):
        text = self.get_text()
        if not text:
            self._show_flash("Nothing to copy", "warning")
            return
        try:
            pyperclip.copy(text)
            self._show_flash("Copied to clipboard!", "success")
        except Exception as e:
            logger.error(f"Clipboard error: {e}")
            self._show_flash("Copy failed", "error")

    def _open_word(self):
        lm = self._s.get("lm")
        if lm and not lm.can_use_feature("max_reports_per_day"):
            self._show_flash("Daily report limit reached. Upgrade your plan.", "warning")
            return

        text = self.get_text()
        if not text:
            self._show_flash("Nothing to export", "warning")
            return

        payload = self._build_payload(text)
        if self._do_word:
            threading.Thread(target=self._do_word, args=(payload,), daemon=True).start()
        if lm:
            lm.increment_usage("max_reports_per_day", 1)
        self._show_flash("Opening in Word…", "success")

    def _print(self):
        lm = self._s.get("lm")
        if lm and not lm.can_use_feature("printing"):
            self._show_flash("Printing requires SOLO plan or higher.", "warning")
            return
        text = self.get_text()
        if not text:
            self._show_flash("Nothing to print", "warning")
            return
        payload = self._build_payload(text)
        if self._do_print:
            threading.Thread(target=self._do_print, args=(payload,), daemon=True).start()

    def _build_payload(self, text: str) -> dict:
        pacs = self._s.get("pacs_state", {})
        return {
            "formula":        text,
            "etablissement":  self._etab.get().strip(),
            "medecin":        self._med.get().strip() or "Dr.",
            "modality":       self._modality_combo.get(),
            "exam_type":      self._exam_combo.get(),
            "formula_name":   self._title_combo.get().strip().lstrip("* ").strip(),
            "language":       self._s.get("current_language", "Français"),
            "patient_data":   pacs.get("current_patient"),
            "examen_data":    pacs.get("current_examen"),
        }

    def update_pacs_status(self, patient=None, examen=None):
        pass  # PACS section removed from Reports

    # ── Language change ────────────────────────────────────────────────────
    def _on_language_change(self, lang):
        if self._s.get("on_change_language"):
            self._s["on_change_language"](lang)

    # ── Template search ────────────────────────────────────────────────────
    def _open_search(self):
        from ui.dialogs.formula_search import FormulaSearchDialog
        dlg = FormulaSearchDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            on_select=self._apply_search_result,
        )
        dlg.grab_set()

    def _apply_search_result(self, result: dict):
        """Apply a formula chosen from the search dialog."""
        content = result.get("formula_content") or result.get("content", "")
        if content:
            self._set_text(content)
            self._show_flash("Template applied", "success")

    # ── Custom formulas ────────────────────────────────────────────────────
    def _add_custom_formula(self):
        from ui.dialogs.formula_editor import AddFormulaDialog
        dlg = AddFormulaDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            on_saved=self._refresh_custom,
        )
        dlg.grab_set()

    def _manage_custom_formulas(self):
        from ui.dialogs.formula_editor import ManageFormulasDialog
        dlg = ManageFormulasDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            on_saved=self._refresh_custom,
        )
        dlg.grab_set()

    def _refresh_custom(self):
        mod = self._modality_combo.get()
        if mod:
            self._on_modality_change(mod)

    # ── Dictation ─────────────────────────────────────────────────────────
    def _toggle_dictation(self):
        lm = self._s.get("lm")
        if lm and not lm.can_use_feature("ai_dictation_minutes_per_day"):
            self._show_flash("Daily dictation limit reached. Upgrade your plan.", "warning")
            return

        if not self._dictating:
            self._start_dictation()
        else:
            self._stop_dictation()

    def _start_dictation(self):
        try:
            from whisper_dictation import WhisperDictation, WHISPER_AVAILABLE, SOUNDDEVICE_AVAILABLE
            if not WHISPER_AVAILABLE or not SOUNDDEVICE_AVAILABLE:
                self._show_flash("Dictation requires: pip install openai-whisper sounddevice numpy", "warning")
                return

            lang_code = self._get_whisper_lang()
            self._whisper = WhisperDictation(language=lang_code)
            self._dictating = True
            self._dict_btn.configure(
                text="  Stop Dictation",
                fg_color=C.ERROR_DIM,
                hover_color=C.ERROR_DIM,
                text_color=C.ERROR,
                border_color=C.ERROR,
            )

            def _load_and_start():
                try:
                    self._whisper.load(on_progress=self._on_dictation_status)
                    if self._dictating:
                        self._whisper.start(self._on_dictation_text)
                except Exception as e:
                    logger.error(f"Dictation load/start error: {e}")
                    self.after(0, lambda: self._show_flash(f"Dictation error: {e}", "error"))
                    self.after(0, self._stop_dictation)

            threading.Thread(target=_load_and_start, daemon=True).start()
        except Exception as e:
            logger.error(f"Dictation start error: {e}")
            self._show_flash(f"Dictation error: {e}", "error")

    def _stop_dictation(self):
        if self._whisper:
            try:
                self._whisper.stop()
            except Exception:
                pass
            self._whisper = None
        self._dictating = False
        self._dict_btn.configure(
            text="  Dictation",
            fg_color=C.SURFACE_3,
            hover_color=C.BORDER,
            text_color=C.TEXT_2,
            border_color=C.BORDER,
        )
        self._dict_status.place_forget()

    def _on_dictation_text(self, text: str):
        self.after(0, lambda: self._insert_dictation(text))

    def _insert_dictation(self, text: str):
        try:
            from whisper_dictation import RadioCorrector
            corrected = RadioCorrector.correct(text, self._s.get("current_language", "Français"))
        except Exception:
            corrected = text
        # Insert dictated text into the Results section (largest, most relevant)
        tb = self._section_texts.get("resultat")
        if tb:
            tb.insert("end", corrected + " ")
        self._update_char_count()
        lm = self._s.get("lm")
        if lm:
            lm.increment_usage("ai_dictation_minutes_per_day", 1)

    def _on_dictation_status(self, msg: str):
        self.after(0, lambda: self._dict_status.configure(text=f"  {msg}  "))
        self.after(0, lambda: self._dict_status.place(relx=0.5, rely=0.0,
                                                       anchor="n", relwidth=0.7))

    def _get_whisper_lang(self) -> str:
        lang = self._s.get("current_language", "Français")
        mapping = {
            "Français": "fr", "English": "en", "Deutsch": "de",
            "Español": "es", "Italiano": "it", "Português": "pt",
            "Русский": "ru", "日本語": "ja", "中文": "zh",
            "한국어": "ko", "Türkçe": "tr", "Polski": "pl",
            "Nederlands": "nl", "Svenska": "sv", "Norsk": "no",
            "Dansk": "da", "हिन्दी": "hi", "ไทย": "th",
        }
        return mapping.get(lang, "fr")

    # ── Flash notification ─────────────────────────────────────────────────
    def _show_flash(self, msg: str, style="info"):
        colors = {
            "info":    (C.INFO_DIM,    C.INFO),
            "success": (C.SUCCESS_DIM, C.SUCCESS),
            "warning": (C.WARNING_DIM, C.WARNING),
            "error":   (C.ERROR_DIM,   C.ERROR),
        }
        bg, fg = colors.get(style, colors["info"])
        self._dict_status.configure(text=f"  {msg}  ", fg_color=bg, text_color=fg)
        self._dict_status.place(relx=0.5, rely=0.02, anchor="n", relwidth=0.7)
        self.after(3000, self._dict_status.place_forget)

    # ── AI enhance ────────────────────────────────────────────────────────

    _SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _ai_set_loading(self) -> None:
        if not hasattr(self, "_ai_btn") or self._ai_btn is None:
            return
        self._ai_btn.configure(state="disabled", text_color=C.BORDER_2,
                               fg_color=C.SURFACE_3)
        for tb in self._section_texts.values():
            tb.configure(state="disabled")
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
        if self._ai_spin_job:
            self.after_cancel(self._ai_spin_job)
            self._ai_spin_job = None
        if not hasattr(self, "_ai_btn") or self._ai_btn is None:
            return
        for tb in self._section_texts.values():
            tb.configure(state="normal")
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

    def _ai_reset_btn(self) -> None:
        if hasattr(self, "_ai_btn") and self._ai_btn:
            self._ai_btn.configure(text="✨ AI", fg_color=C.GOLD_DIM,
                                   text_color=C.GOLD, border_color=C.GOLD_DIM)

    def _on_ai_enhance(self) -> None:
        lm = self._s.get("lm")
        if lm and not lm.can_use_feature("ai_dictation_minutes_per_day"):
            self._show_flash("AI enhancement requires SOLO plan or higher.", "warning")
            return

        # ── STEP 1 : lire toutes les sections ────────────────────────────
        _PLACEHOLDERS = {"[à compléter]", "[leave empty]", "[a completer]"}

        def _is_placeholder(v: str) -> bool:
            return not v or v.lower() in _PLACEHOLDERS

        # Snapshot des valeurs originales
        originals: dict[str, str] = {
            key: tb.get("1.0", "end-1c").strip()
            for key, tb in self._section_texts.items()
        }

        findings = originals.get("resultat", "")
        if _is_placeholder(findings):
            self._show_flash("Nothing to enhance — fill in the Findings section.", "warning")
            return

        # Conclusion existante ? → elle sera préservée, Gemini n'y touche pas
        existing_conclusion = originals.get("conclusion", "")
        has_conclusion = not _is_placeholder(existing_conclusion)

        logger.info("AI enhance — bouton cliqué | findings: %d chars | conclusion existante: %s",
                    len(findings), has_conclusion)

        self._ai_set_loading()

        def _run():
            try:
                from report_editor_controller import (
                    _call_gemini, _RADIOLOGY_PROMPT, _parse_gemini_response,
                )
                # ── STEP 2 : envoi findings + conclusion existante à Gemini ─
                # Conclusion existante → Gemini corrige seulement l'orthographe
                # Conclusion absente  → Gemini en génère une depuis les findings
                input_text = f"Findings:\n{findings}"
                if has_conclusion:
                    input_text += f"\n\nConclusion:\n{existing_conclusion}"
                logger.info("AI enhance — envoi à Gemini (%d chars)", len(input_text))
                raw = _call_gemini(_RADIOLOGY_PROMPT + input_text)
                logger.info("AI enhance — réponse (%d chars): %s", len(raw), raw[:300])

                if not raw.strip():
                    logger.warning("AI enhance — réponse vide")
                    self.after(0, lambda: self._ai_set_ready(success=False))
                    return

                # ── STEP 3 : parsing ─────────────────────────────────────
                sections = _parse_gemini_response(raw)
                logger.info("AI enhance — sections parsées: %s", list(sections.keys()))

                if not sections:
                    logger.warning("AI enhance — parsing échoué, aucune modification")
                    self.after(0, lambda: self._ai_set_ready(success=False))
                    return

                # ── STEP 4 : mise à jour UI ──────────────────────────────
                def _apply():
                    changes = 0
                    for tb in self._section_texts.values():
                        tb.configure(state="normal")

                    # Findings corrigés → toujours appliqués
                    corrected_findings = sections.get("resultat", "").strip()
                    if corrected_findings:
                        tb_r = self._section_texts["resultat"]
                        old = tb_r.get("1.0", "end-1c").strip()
                        tb_r.delete("1.0", "end")
                        tb_r.insert("1.0", corrected_findings)
                        if corrected_findings != old:
                            changes += 1
                            logger.info("  ✓ 'resultat' modifié")
                        else:
                            logger.info("  – 'resultat' inchangé (déjà correct)")

                    # Conclusion → toujours appliquée
                    tb_c = self._section_texts["conclusion"]
                    ai_conclusion = sections.get("conclusion", "").strip()
                    if ai_conclusion:
                        old_c = tb_c.get("1.0", "end-1c").strip()
                        tb_c.delete("1.0", "end")
                        tb_c.insert("1.0", ai_conclusion)
                        if ai_conclusion != old_c:
                            changes += 1
                            action = "corrigée" if has_conclusion else "générée"
                            logger.info("  ✓ 'conclusion' %s", action)

                    self._update_char_count()
                    if changes > 0:
                        self._show_flash(f"AI : {changes} correction(s) appliquée(s).", "success")
                        logger.info("AI enhance — %d correction(s) appliquée(s)", changes)
                    else:
                        self._show_flash("AI : texte déjà correct, aucune modification.", "info")
                        logger.info("AI enhance — texte déjà correct")
                    self._ai_set_ready(success=True)

                self.after(0, _apply)

            except Exception as exc:
                logger.error("AI enhance error: %s", exc, exc_info=True)
                self.after(0, lambda: self._ai_set_ready(success=False))

        threading.Thread(target=_run, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # PUBLIC — called from app.py
    # ══════════════════════════════════════════════════════════════════════
    def reload_data(self):
        """Reload templates after language change."""
        self._custom_cache = {}
        self._populate_modalities()
        self._restore_config()

        lang = self._s.get("current_language", "Français")
        self._lang_combo.set(lang)
