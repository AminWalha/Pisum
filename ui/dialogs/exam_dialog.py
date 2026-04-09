# -*- coding: utf-8 -*-
"""
ui/dialogs/exam_dialog.py — Add / Edit exam (CTk modal).
Includes an inline patient search to pick the target patient.
Sets self.result = dict on save, None on cancel.
"""
import datetime
import threading
import customtkinter as ctk

from ui.theme import C, F, S, R
from ui.components.widgets import (
    LabeledEntry, LabeledCombo, PrimaryButton, GhostButton, Divider,
)

MODALITES_DEFAULT = [
    "IRM", "Scanner", "Echographie",
    "Radiographie conventionnelle",
    "Radiologie Interventionnelle",
    "Sénologie", "Consultations", "Autre",
]


class ExamDialog(ctk.CTkToplevel):
    """
    Add a new exam.
    If pre_patient is provided (dict with patient_uuid + name) it skips patient search.
    """

    def __init__(self, master, core_state: dict = None,
                 pre_patient: dict = None, exam: dict = None, **kw):
        super().__init__(master, **kw)
        self.result       = None
        self._s           = core_state or {}
        self._pre_patient = pre_patient
        self._edit_exam   = exam
        self._patient_uuid = pre_patient.get("patient_uuid") if pre_patient else None

        title = "Edit Exam" if exam else "New Exam"
        self.title(title)
        self.geometry("540x680")
        self.resizable(False, False)
        self.configure(fg_color=C.BG)
        try:
            self.transient("")
        except Exception:
            pass
        self.lift()
        self.focus_force()
        self.after(100, self._safe_grab)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header(title)
        self._build_form()
        self._build_footer()

        if exam:
            self._populate(exam)

        self.bind("<Escape>", lambda _: self._cancel())

    # ── Header ─────────────────────────────────────────────────────────────
    def _build_header(self, title: str):
        hdr = ctk.CTkFrame(self, fg_color=C.SURFACE,
                           border_color=C.BORDER, border_width=1,
                           corner_radius=0, height=56)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.pack_propagate(False)

        ctk.CTkLabel(
            hdr, text="📋  " + title,
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).pack(side="left", padx=S.LG)

    # ── Form ───────────────────────────────────────────────────────────────
    def _build_form(self):
        scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
        )
        scroll.grid(row=1, column=0, sticky="nsew", padx=S.LG, pady=S.MD)
        scroll.grid_columnconfigure((0, 1), weight=1)

        row = 0

        # ── Patient picker ───────────────────────────────────────────────
        if not self._pre_patient:
            ctk.CTkLabel(
                scroll, text="Patient *",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
            ).grid(row=row, column=0, columnspan=2, sticky="w",
                   pady=(0, S.XS))
            row += 1

            search_frame = ctk.CTkFrame(scroll, fg_color="transparent")
            search_frame.grid(row=row, column=0, columnspan=2, sticky="ew",
                              pady=(0, S.SM))
            search_frame.grid_columnconfigure(0, weight=1)

            self._pat_search = ctk.CTkEntry(
                search_frame,
                placeholder_text="🔍  Type last name to search…",
                fg_color=C.SURFACE_3, border_color=C.BORDER,
                text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
                font=ctk.CTkFont(*F.BODY), corner_radius=R.MD, height=36,
            )
            self._pat_search.grid(row=0, column=0, sticky="ew", padx=(0, S.SM))
            self._pat_search.bind("<KeyRelease>", self._on_patient_search)

            self._pat_results = ctk.CTkFrame(
                scroll, fg_color=C.SURFACE_2,
                border_color=C.BORDER, border_width=1,
                corner_radius=R.MD,
            )
            self._pat_results.grid(row=row + 1, column=0, columnspan=2,
                                   sticky="ew", pady=(0, S.SM))
            self._pat_results.grid_columnconfigure(0, weight=1)

            self._pat_lbl = ctk.CTkLabel(
                self._pat_results,
                text="No patient selected",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
                anchor="w",
            )
            self._pat_lbl.pack(fill="x", padx=S.MD, pady=S.SM)
            row += 2

            Divider(scroll).grid(row=row, column=0, columnspan=2,
                                 sticky="ew", pady=(0, S.LG))
            row += 1
        else:
            self._pat_lbl = None  # not used

        # ── Date + Modality ──────────────────────────────────────────────
        today = datetime.date.today().strftime("%d-%m-%Y")
        self._date = LabeledEntry(scroll, "Exam Date *", placeholder=today)
        self._date.grid(row=row, column=0, sticky="ew",
                        padx=(0, S.SM), pady=(0, S.MD))
        self._date.set(today)

        modalites = MODALITES_DEFAULT
        try:
            from pacs_ris_db import PACS_TRANSLATIONS
            lang = self._s.get("current_language", "Français")
            mods = PACS_TRANSLATIONS.get(lang, {}).get("modalites", [])
            if mods:
                modalites = mods
        except Exception:
            pass

        self._mod = LabeledCombo(scroll, "Modality *", values=modalites)
        self._mod.grid(row=row, column=1, sticky="ew", pady=(0, S.MD))
        row += 1

        # ── Type + Formula name ──────────────────────────────────────────
        self._type = LabeledEntry(scroll, "Exam Type",
                                  placeholder="e.g. Brain without contrast")
        self._type.grid(row=row, column=0, columnspan=2, sticky="ew",
                        pady=(0, S.MD))
        row += 1

        # ── Prescriber + Radiologist ─────────────────────────────────────
        self._presc = LabeledEntry(scroll, "Referring Physician",
                                   placeholder="Dr. Martin")
        self._presc.grid(row=row, column=0, sticky="ew",
                         padx=(0, S.SM), pady=(0, S.MD))

        self._med = LabeledEntry(scroll, "Radiologist", placeholder="Dr. Smith")
        self._med.grid(row=row, column=1, sticky="ew", pady=(0, S.MD))
        cfg = self._s.get("config_manager")
        if cfg:
            try:
                self._med.set(cfg.get("medecin", ""))
            except Exception:
                pass
        row += 1

        # ── Facility + Language ──────────────────────────────────────────
        self._etab = LabeledEntry(scroll, "Facility", placeholder="St-Mary Hospital")
        self._etab.grid(row=row, column=0, sticky="ew",
                        padx=(0, S.SM), pady=(0, S.MD))
        if cfg:
            try:
                self._etab.set(cfg.get("etablissement", ""))
            except Exception:
                pass

        try:
            from Comptes_Rendus import AppConstants
            langs = list(AppConstants.AVAILABLE_LANGUAGES.keys())
        except Exception:
            langs = ["Français", "English", "Español", "Deutsch",
                     "Italiano", "Português", "Русский"]
        self._lang = LabeledCombo(scroll, "Report Language", values=langs)
        self._lang.grid(row=row, column=1, sticky="ew", pady=(0, S.MD))
        self._lang.set(self._s.get("current_language", "Français"))
        row += 1

        # ── Indication ───────────────────────────────────────────────────
        ctk.CTkLabel(
            scroll, text="Indication",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, S.XS))
        row += 1

        self._ind = ctk.CTkTextbox(
            scroll, height=70,
            fg_color=C.SURFACE_3, border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY), corner_radius=R.MD,
        )
        self._ind.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        self._error_lbl = ctk.CTkLabel(
            scroll, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.ERROR, anchor="w",
        )
        self._error_lbl.grid(row=row, column=0, columnspan=2, sticky="w",
                              pady=(S.SM, 0))

    # ── Footer ─────────────────────────────────────────────────────────────
    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=C.SURFACE,
                              border_color=C.BORDER, border_width=1,
                              corner_radius=0, height=60)
        footer.grid(row=2, column=0, sticky="ew")
        footer.pack_propagate(False)

        GhostButton(footer, "Cancel", command=self._cancel).pack(
            side="left", padx=S.LG)

        lbl = "Save Changes" if self._edit_exam else "Create Exam"
        PrimaryButton(footer, lbl, icon="✔", width=180,
                      command=self._save).pack(side="right", padx=S.LG)

    # ── Patient search ─────────────────────────────────────────────────────
    def _on_patient_search(self, _event=None):
        q = self._pat_search.get().strip()
        if len(q) < 2:
            return
        threading.Thread(
            target=self._search_patients, args=(q,), daemon=True
        ).start()

    def _search_patients(self, query: str):
        try:
            from pacs_ris_db import get_pacs_db
            results = get_pacs_db().search_patients(query, limit=8)
            self.after(0, lambda: self._show_patient_results(results))
        except Exception:
            pass

    def _show_patient_results(self, patients: list):
        for w in self._pat_results.winfo_children():
            w.destroy()

        if not patients:
            ctk.CTkLabel(
                self._pat_results, text="No match found",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3, anchor="w",
            ).pack(fill="x", padx=S.MD, pady=S.SM)
            return

        for p in patients:
            name = f"{p.get('nom', '')} {p.get('prenom', '')}".strip()
            ddn  = p.get("date_naissance", "")
            btn  = ctk.CTkButton(
                self._pat_results,
                text=f"{name}   {ddn}",
                anchor="w",
                fg_color="transparent", hover_color=C.SURFACE_3,
                text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY_SM),
                height=32, corner_radius=0,
                command=lambda pid=p["patient_uuid"], pname=name:
                    self._select_patient(pid, pname),
            )
            btn.pack(fill="x")

    def _select_patient(self, patient_uuid: str, name: str):
        self._patient_uuid = patient_uuid
        for w in self._pat_results.winfo_children():
            w.destroy()
        ctk.CTkLabel(
            self._pat_results,
            text=f"✓  {name}",
            font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
            text_color=C.SUCCESS, anchor="w",
        ).pack(fill="x", padx=S.MD, pady=S.SM)

    # ── Populate (edit mode) ───────────────────────────────────────────────
    def _populate(self, e: dict):
        self._date.set(e.get("date_examen", ""))
        self._mod.set(e.get("modalite", ""))
        self._type.set(e.get("type_examen", ""))
        self._presc.set(e.get("medecin_prescripteur", ""))
        self._med.set(e.get("medecin", ""))
        self._etab.set(e.get("etablissement", ""))
        self._lang.set(e.get("langue", ""))
        ind = e.get("indication", "")
        if ind:
            self._ind.insert("1.0", ind)
        if e.get("patient_uuid"):
            self._patient_uuid = e["patient_uuid"]

    # ── Save ───────────────────────────────────────────────────────────────
    def _save(self):
        # Resolve patient
        pid = self._patient_uuid or (
            self._pre_patient.get("patient_uuid") if self._pre_patient else None
        )
        if not pid:
            self._error_lbl.configure(text="Please select a patient.")
            return

        date = self._date.get().strip()
        mod  = self._mod.get().strip()
        if not date or not mod:
            self._error_lbl.configure(text="Exam date and modality are required.")
            return

        self.result = {
            "patient_uuid":         pid,
            "date_examen":          date,
            "modalite":             mod,
            "type_examen":          self._type.get().strip(),
            "indication":           self._ind.get("1.0", "end-1c").strip(),
            "medecin_prescripteur": self._presc.get().strip(),
            "medecin":              self._med.get().strip(),
            "etablissement":        self._etab.get().strip(),
            "langue":               self._lang.get().strip(),
        }
        self.destroy()

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _cancel(self):
        self.result = None
        self.destroy()
