# -*- coding: utf-8 -*-
"""
ui/dialogs/formula_editor.py — Add / Edit Custom Formula Dialogs
"""
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Divider, PrimaryButton, SecondaryButton,
    LabeledEntry, LabeledCombo,
)


class _BaseFormulaDialog(ctk.CTkToplevel):
    def __init__(self, master, core_state, on_saved=None, **kw):
        super().__init__(master, **kw)
        self.resizable(False, False)
        self.configure(fg_color=C.SURFACE)
        try:
            self.transient("")
        except Exception:
            pass
        self.lift()
        self.focus_force()
        self.after(100, self._safe_grab)

        self._s       = core_state
        self._on_saved = on_saved

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_form()
        self._build_footer()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=C.SURFACE_2, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text=self._title_text(),
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, padx=S.XL, pady=S.LG)
        Divider(hdr).grid(row=1, column=0, sticky="ew")

    def _title_text(self):
        return "Formula"

    def _build_form(self):
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=1, column=0, sticky="nsew", padx=S.XL, pady=S.LG)
        form.grid_columnconfigure(0, weight=1)

        # ── Language ───────────────────────────────────────────────────
        langs = self._get_languages()
        cur   = self._s.get("current_language", langs[0] if langs else "Français")
        self._lang_combo = LabeledCombo(form, "Language", values=langs)
        self._lang_combo.set(cur)
        self._lang_combo.grid(row=0, column=0, sticky="ew", pady=(0, S.MD))
        # When language changes reload modalities
        try:
            self._lang_combo.combo.configure(command=self._on_lang_change)
        except Exception:
            pass

        # ── Modality ───────────────────────────────────────────────────
        mods = list(self._get_modalities())
        self._mod_combo = LabeledCombo(form, "Modality *", values=mods)
        self._mod_combo.grid(row=1, column=0, sticky="ew", pady=(0, S.MD))

        # ── Exam type ──────────────────────────────────────────────────
        self._exam_entry = LabeledEntry(
            form, "Exam Type *",
            placeholder="e.g. Brain MRI, Chest CT…"
        )
        self._exam_entry.grid(row=2, column=0, sticky="ew", pady=(0, S.MD))

        # ── Template Title ─────────────────────────────────────────────
        self._title_entry = LabeledEntry(
            form, "Template Title *",
            placeholder="e.g. Normal Brain MRI"
        )
        self._title_entry.grid(row=3, column=0, sticky="ew", pady=(0, S.MD))

        # ── Report Content ─────────────────────────────────────────────
        content_hdr = ctk.CTkFrame(form, fg_color="transparent")
        content_hdr.grid(row=4, column=0, sticky="ew", pady=(0, S.XS))
        content_hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            content_hdr, text="Report Content *",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkButton(
            content_hdr, text="⊕ Import from templates",
            width=160, height=24,
            fg_color="transparent", hover_color=C.SURFACE_3,
            text_color=C.TEAL, font=ctk.CTkFont(*F.CAPTION),
            corner_radius=R.MD, border_width=1, border_color=C.TEAL,
            command=self._open_template_picker,
        ).grid(row=0, column=1, sticky="e")

        self._content_text = ctk.CTkTextbox(
            form, height=200,
            fg_color=C.SURFACE_3, border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY),
            corner_radius=R.MD,
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
            wrap="word",
        )
        self._content_text.grid(row=5, column=0, sticky="nsew", pady=(0, S.MD))

        self._error_lbl = ctk.CTkLabel(
            form, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.ERROR, anchor="w",
        )
        self._error_lbl.grid(row=6, column=0, sticky="w")

        self._populate_fields()

    def _get_languages(self) -> list[str]:
        try:
            from Comptes_Rendus import AppConstants
            return list(AppConstants.AVAILABLE_LANGUAGES.keys())
        except Exception:
            return ["Français", "English", "Español", "Deutsch", "Italiano"]

    def _on_lang_change(self, lang: str):
        """Reload modalities when language changes."""
        try:
            from Comptes_Rendus import AppConstants, TRANSLATIONS
            lang_folder = AppConstants.AVAILABLE_LANGUAGES.get(lang, "Francais")
            from encrypted_excel_loader import load_all_formulas
            new_data = load_all_formulas(lang_folder)
            self._s["data"] = new_data
            self._s["current_language"] = lang
            self._s["current_language_folder"] = lang_folder
            self._s["translations"] = TRANSLATIONS.get(lang, {})
            # Re-apply translation map on the internal (Romanian) data keys
            mods = list(self._get_modalities())
            self._mod_combo.combo.configure(values=mods)
            if mods:
                self._mod_combo.set(mods[0])
        except Exception:
            pass

    def _open_template_picker(self):
        """Open a dialog to pick an existing template and import its content."""
        picker = _TemplatePickerDialog(self, self._s)
        self.wait_window(picker)
        if picker.result:
            self._content_text.delete("1.0", "end")
            self._content_text.insert("1.0", picker.result)

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=C.SURFACE_2, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        Divider(footer).pack(fill="x")
        btn_row = ctk.CTkFrame(footer, fg_color="transparent")
        btn_row.pack(side="right", padx=S.XL, pady=S.MD)

        SecondaryButton(btn_row, "Cancel", command=self.destroy).pack(
            side="left", padx=(0, S.SM))
        PrimaryButton(btn_row, "Save", icon="✓", command=self._save).pack(
            side="left")

    def _get_modalities(self):
        data = self._s.get("data", {})
        t    = self._s.get("translations", {})
        mods = t.get("modalites", {})
        # Map internal Romanian file-keys → translated display names
        return [mods.get(k, k) for k in data.keys()] or \
               ["MRI", "CT-Scan", "X-Ray", "Ultrasound"]

    def _populate_fields(self):
        """Override in subclasses to pre-fill fields."""
        pass

    def _save(self):
        mod     = self._mod_combo.get().strip()
        exam    = self._exam_entry.get().strip()
        title   = self._title_entry.get().strip()
        content = self._content_text.get("1.0", "end").strip()
        # Use language folder matching the selected language in the combo
        try:
            from Comptes_Rendus import AppConstants
            lang = AppConstants.AVAILABLE_LANGUAGES.get(
                self._lang_combo.get(),
                self._s.get("current_language_folder", "Francais")
            )
        except Exception:
            lang = self._s.get("current_language_folder", "Francais")

        if not all([mod, exam, title, content]):
            self._error_lbl.configure(text="Please fill in all required fields (*)")
            return

        db = self._s.get("custom_formulas_db")
        if not db:
            self._error_lbl.configure(text="Database unavailable")
            return

        ok = self._do_save(db, mod, exam, title, content, lang)
        if ok:
            if self._on_saved:
                self._on_saved()
            self.destroy()
        else:
            self._error_lbl.configure(text="Error saving formula. Try a different title.")

    def _do_save(self, db, mod, exam, title, content, lang) -> bool:
        return db.add_formula(mod, exam, title, content, lang)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass


class AddFormulaDialog(_BaseFormulaDialog):
    def __init__(self, master, core_state, on_saved=None, **kw):
        super().__init__(master, core_state, on_saved, **kw)
        self.title("Add Custom Template")
        self.geometry("540x640")

    def _title_text(self):
        return "New Custom Template"


class EditFormulaDialog(_BaseFormulaDialog):
    def __init__(self, master, core_state, formula: dict, on_saved=None, **kw):
        self._formula = formula
        super().__init__(master, core_state, on_saved, **kw)
        self.title("Edit Template")
        self.geometry("540x640")

    def _title_text(self):
        return "Edit Custom Template"

    def _populate_fields(self):
        f = self._formula
        # Language
        try:
            from Comptes_Rendus import AppConstants
            lang_folder = f.get("language", self._s.get("current_language_folder", "Francais"))
            reverse = {v: k for k, v in AppConstants.AVAILABLE_LANGUAGES.items()}
            lang_name = reverse.get(lang_folder, self._s.get("current_language", "Français"))
            self._lang_combo.set(lang_name)
        except Exception:
            pass
        self._mod_combo.set(f.get("modality", ""))
        self._exam_entry.set(f.get("exam_type", ""))
        self._title_entry.set(f.get("title", ""))
        self._content_text.insert("1.0", f.get("formula_content", ""))

    def _do_save(self, db, mod, exam, title, content, lang) -> bool:
        fid = self._formula.get("id")
        if fid:
            return db.update_formula(fid, mod, exam, title, content, lang)
        return db.add_formula(mod, exam, title, content, lang)


class ManageFormulasDialog(ctk.CTkToplevel):
    """Quick manage dialog — redirects to TemplatesView in sidebar."""
    def __init__(self, master, core_state, on_saved=None, **kw):
        super().__init__(master, **kw)
        self.title("Manage Templates")
        self.geometry("800x560")
        self.configure(fg_color=C.BG)
        try:
            self.transient("")
        except Exception:
            pass
        self.lift()
        self.focus_force()
        self.after(100, self._safe_grab_manage)

        self._s       = core_state
        self._on_saved = on_saved
        self._selected_id = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build()

    def _build(self):
        hdr = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text="Manage Custom Templates",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, padx=S.XL, pady=S.LG)
        Divider(hdr).grid(row=1, column=0, columnspan=3, sticky="ew")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=S.XL, pady=S.LG)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)

        # List
        list_frame = ctk.CTkScrollableFrame(
            body, fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            corner_radius=R.LG,
            scrollbar_button_color=C.SURFACE_3,
        )
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, S.MD))
        list_frame.grid_columnconfigure(0, weight=1)

        db       = self._s.get("custom_formulas_db")
        lang_f   = self._s.get("current_language_folder", "Francais")
        formulas = db.get_formulas_by_language(lang_f) if db else []

        self._preview = ctk.CTkTextbox(
            body, fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY),
            corner_radius=R.LG, state="disabled",
            scrollbar_button_color=C.SURFACE_3,
        )
        self._preview.grid(row=0, column=1, sticky="nsew")

        for f in formulas:
            row = ctk.CTkFrame(list_frame, fg_color="transparent",
                               corner_radius=R.MD, cursor="hand2")
            row.pack(fill="x", padx=S.SM, pady=2)
            row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                row, text=f.get("title", ""),
                font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
                text_color=C.TEXT_1, anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=S.MD, pady=(S.SM, 0))
            ctk.CTkLabel(
                row, text=f"{f.get('modality','')} · {f.get('exam_type','')}",
                font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=S.MD, pady=(0, S.SM))

            btn_col = ctk.CTkFrame(row, fg_color="transparent")
            btn_col.grid(row=0, column=1, rowspan=2, padx=S.SM)

            def edit(f=f):
                EditFormulaDialog(
                    self, self._s, formula=f,
                    on_saved=lambda: (self._on_saved() if self._on_saved else None,
                                      self.destroy()))

            def delete(f=f):
                if db:
                    db.delete_formula(f.get("id"))
                    if self._on_saved:
                        self._on_saved()
                    self.destroy()

            SecondaryButton(btn_col, "Edit", width=60,
                            command=edit).pack(pady=2)
            ctk.CTkButton(btn_col, text="Del", width=60, height=28,
                          fg_color=C.ERROR_DIM, hover_color=C.ERROR_DIM,
                          text_color=C.ERROR, border_color=C.ERROR,
                          border_width=1, corner_radius=R.MD,
                          font=ctk.CTkFont(*F.CAPTION),
                          command=delete).pack(pady=2)

            def show(f=f, r=row):
                self._preview.configure(state="normal")
                self._preview.delete("1.0", "end")
                self._preview.insert("end", f.get("formula_content", ""))
                self._preview.configure(state="disabled")
                for child in list_frame.winfo_children():
                    try:
                        child.configure(fg_color="transparent")
                    except Exception:
                        pass
                r.configure(fg_color=C.TEAL_DIM)

            row.bind("<Button-1>", lambda e, f=f, r=row: show(f, r))

        # Footer
        footer = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        Divider(footer).pack(fill="x")
        SecondaryButton(footer, "Close", command=self.destroy).pack(
            side="right", padx=S.XL, pady=S.MD)

    def _safe_grab_manage(self):
        try:
            self.grab_set()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  Template Picker — browse existing Supabase templates to import content
# ══════════════════════════════════════════════════════════════════════════════

class _TemplatePickerDialog(ctk.CTkToplevel):
    """
    Let the user browse Modality → Exam Type → Template from the loaded data
    and import the content into the custom template editor.
    """
    def __init__(self, master, core_state: dict, **kw):
        super().__init__(master, **kw)
        self.title("Import from Templates")
        self.geometry("620x460")
        self.configure(fg_color=C.SURFACE)
        self.resizable(False, False)
        try:
            self.transient("")
        except Exception:
            pass
        self.lift()
        self.focus_force()
        self.after(100, self._safe_grab)

        self._s      = core_state
        self.result  = None          # set to content string on confirm

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build()

    def _build(self):
        import pandas as pd

        # Header
        hdr = ctk.CTkFrame(self, fg_color=C.SURFACE_2, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            hdr, text="Import from existing templates",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, padx=S.XL, pady=S.LG)
        Divider(hdr).grid(row=1, column=0, sticky="ew")

        # Body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=S.XL, pady=S.LG)
        body.grid_columnconfigure(0, weight=1)

        data = self._s.get("data", {})
        modalities = list(data.keys())

        # Modality
        LabeledCombo_ = LabeledCombo
        self._mod_cb = LabeledCombo_(body, "Modality", values=modalities)
        self._mod_cb.grid(row=0, column=0, sticky="ew", pady=(0, S.MD))

        # Exam type
        self._exam_cb = LabeledCombo(body, "Exam Type", values=[])
        self._exam_cb.grid(row=1, column=0, sticky="ew", pady=(0, S.MD))

        # Template
        self._tpl_cb = LabeledCombo(body, "Template", values=[])
        self._tpl_cb.grid(row=2, column=0, sticky="ew", pady=(0, S.MD))

        # Preview
        ctk.CTkLabel(
            body, text="Preview",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=3, column=0, sticky="w", pady=(0, S.XS))

        self._preview = ctk.CTkTextbox(
            body, height=140,
            fg_color=C.SURFACE_3, border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_2, font=ctk.CTkFont(*F.BODY),
            corner_radius=R.MD, state="disabled",
            scrollbar_button_color=C.SURFACE_3,
            wrap="word",
        )
        self._preview.grid(row=4, column=0, sticky="ew")

        # Wire cascades
        def on_mod(mod):
            page = data.get(mod, {})
            exams = list(page.keys()) if isinstance(page, dict) else []
            self._exam_cb.combo.configure(values=exams)
            self._exam_cb.set(exams[0] if exams else "")
            on_exam(exams[0] if exams else "")

        def on_exam(exam):
            mod   = self._mod_cb.get()
            page  = data.get(mod, {})
            if isinstance(page, dict):
                df_data = page.get(exam, {})
                df = df_data.get("data", pd.DataFrame()) if isinstance(df_data, dict) else df_data
            else:
                df = pd.DataFrame()
            cols = [c for c in (df.columns.tolist() if isinstance(df, pd.DataFrame) else [])
                    if not str(c).startswith("_")]
            self._tpl_cb.combo.configure(values=cols)
            self._tpl_cb.set(cols[0] if cols else "")
            on_tpl(cols[0] if cols else "")

        def on_tpl(tpl):
            if not tpl:
                return
            mod  = self._mod_cb.get()
            exam = self._exam_cb.get()
            page = data.get(mod, {})
            if isinstance(page, dict):
                df_data = page.get(exam, {})
                df = df_data.get("data", pd.DataFrame()) if isinstance(df_data, dict) else df_data
            else:
                df = pd.DataFrame()
            try:
                val = df[tpl].iloc[0]
                content = "" if pd.isna(val) else str(val)
            except Exception:
                content = ""
            self._preview.configure(state="normal")
            self._preview.delete("1.0", "end")
            self._preview.insert("1.0", content)
            self._preview.configure(state="disabled")
            self._pending_content = content

        self._mod_cb.combo.configure(command=on_mod)
        self._exam_cb.combo.configure(command=on_exam)
        self._tpl_cb.combo.configure(command=on_tpl)

        # Init cascade
        if modalities:
            self._mod_cb.set(modalities[0])
            on_mod(modalities[0])

        # Footer
        footer = ctk.CTkFrame(self, fg_color=C.SURFACE_2, corner_radius=0)
        footer.grid(row=2, column=0, sticky="ew")
        Divider(footer).pack(fill="x")
        btn_row = ctk.CTkFrame(footer, fg_color="transparent")
        btn_row.pack(side="right", padx=S.XL, pady=S.MD)

        SecondaryButton(btn_row, "Cancel", command=self.destroy).pack(
            side="left", padx=(0, S.SM))
        PrimaryButton(btn_row, "Import", icon="⊕", command=self._confirm).pack(
            side="left")

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _confirm(self):
        self.result = getattr(self, "_pending_content", "")
        self.destroy()
