# -*- coding: utf-8 -*-
"""
ui/views/templates_view.py — Custom Formula Manager
"""
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, SectionLabel, Divider, PrimaryButton,
    SecondaryButton, GhostButton, Badge, LabeledEntry,
)


class TemplatesView(ctk.CTkFrame):
    def __init__(self, master, core_state: dict, on_navigate=None, **kw):
        kw.setdefault("fg_color", C.BG)
        super().__init__(master, **kw)

        self._s   = core_state
        self._nav = on_navigate
        self._selected_id = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        lm = self._s.get("lm")
        self._can_edit = lm.can_use_feature("custom_templates") if lm else False

        self._build_header()
        if self._can_edit:
            self._build_body()
        else:
            self._build_locked()

    # ── Header ─────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=S.XXL, pady=(S.XXL, S.LG))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text="Custom Templates",
            font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            hdr, text="Create and manage your own reusable report templates.",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_2, anchor="w",
        ).grid(row=1, column=0, sticky="w")

        if self._can_edit:
            PrimaryButton(
                hdr, "New Template", icon="+", width=160,
                command=self._add_formula,
            ).grid(row=0, column=1, rowspan=2, padx=(S.MD, 0))

    # ── Locked state ───────────────────────────────────────────────────────
    def _build_locked(self):
        card = Card(self)
        card.grid(row=1, column=0, padx=S.XXL, pady=S.XL, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="⊞",
            font=ctk.CTkFont("Segoe UI", 48), text_color=C.LOCK_FG,
        ).grid(row=0, column=0, pady=(S.XXL, S.MD))
        ctk.CTkLabel(
            card, text="Custom templates require SOLO plan or higher",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_2,
        ).grid(row=1, column=0, pady=(0, S.SM))
        ctk.CTkLabel(
            card, text="Create unlimited personal templates adapted to your practice.",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_3,
        ).grid(row=2, column=0, pady=(0, S.XL))

        PrimaryButton(
            card, "Upgrade Plan", icon="◈", width=180,
            command=lambda: self._nav("license") if self._nav else None,
        ).grid(row=3, column=0, pady=(0, S.XXL))

    # ── Body ───────────────────────────────────────────────────────────────
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=S.XXL, pady=(0, S.XL))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=2)

        # ── Left: formula list ──────────────────────────────────────────
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, S.MD))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Search bar
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", self._filter_list)
        search = ctk.CTkEntry(
            left, textvariable=self._search_var,
            placeholder_text="Search formulas…",
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont(*F.BODY), height=34, corner_radius=R.MD,
        )
        search.grid(row=0, column=0, sticky="ew", padx=S.MD, pady=S.MD)

        Divider(left).grid(row=1, column=0, sticky="ew")

        self._list_frame = ctk.CTkScrollableFrame(
            left, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        self._list_frame.grid(row=2, column=0, sticky="nsew")
        self._list_frame.grid_columnconfigure(0, weight=1)

        # ── Right: preview / editor ─────────────────────────────────────
        right = Card(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self._preview_header = ctk.CTkFrame(right, fg_color="transparent")
        self._preview_header.grid(row=0, column=0, sticky="ew",
                                  padx=S.LG, pady=(S.LG, 0))
        self._preview_header.grid_columnconfigure(0, weight=1)

        self._preview_title = ctk.CTkLabel(
            self._preview_header, text="Select a template",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        )
        self._preview_title.grid(row=0, column=0, sticky="w")

        self._preview_meta = ctk.CTkLabel(
            self._preview_header, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
        )
        self._preview_meta.grid(row=1, column=0, sticky="w")

        btn_row = ctk.CTkFrame(self._preview_header, fg_color="transparent")
        btn_row.grid(row=0, column=1, rowspan=2)

        self._edit_btn = SecondaryButton(
            btn_row, "Edit", icon="✏", width=90,
            command=self._edit_selected,
            state="disabled",
        )
        self._edit_btn.pack(side="left", padx=(S.SM, S.XS))

        self._del_btn = ctk.CTkButton(
            btn_row, text="Delete",
            width=90, height=38,
            fg_color=C.ERROR_DIM, hover_color=C.ERROR_DIM,
            text_color=C.ERROR, border_color=C.ERROR, border_width=1,
            font=ctk.CTkFont(*F.SUBHEADING), corner_radius=R.MD,
            command=self._delete_selected,
            state="disabled",
        )
        self._del_btn.pack(side="left")

        Divider(right).grid(row=1, column=0, sticky="ew",
                            padx=S.LG, pady=S.SM)

        self._preview_text = ctk.CTkTextbox(
            right,
            fg_color="transparent", text_color=C.TEXT_1,
            font=ctk.CTkFont(*F.BODY_LG), wrap="word",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
            state="disabled",
        )
        self._preview_text.grid(row=2, column=0, sticky="nsew",
                                padx=S.MD, pady=(0, S.MD))

        self._load_list()

    # ── List management ────────────────────────────────────────────────────
    def _load_list(self):
        for w in self._list_frame.winfo_children():
            w.destroy()
        self._items = []

        db = self._s.get("custom_formulas_db")
        if not db:
            return

        # Show ALL templates regardless of language so nothing gets hidden
        formulas = db.get_all_formulas()

        if not formulas:
            ctk.CTkLabel(
                self._list_frame, text="No templates yet.\nClick 'New Template' to start.",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
                justify="center",
            ).pack(pady=S.XXL)
            return

        q = self._search_var.get().lower() if hasattr(self, "_search_var") else ""
        for f in formulas:
            if q and q not in (f.get("title","") + f.get("exam_type","") + f.get("modality","")).lower():
                continue
            self._items.append(f)
            self._add_list_item(f)

    def _add_list_item(self, formula: dict):
        btn = ctk.CTkFrame(
            self._list_frame,
            fg_color="transparent",
            corner_radius=R.MD,
            cursor="hand2",
        )
        btn.pack(fill="x", padx=S.SM, pady=2)
        btn.grid_columnconfigure(0, weight=1)

        title_lbl = ctk.CTkLabel(
            btn, text=formula.get("title", ""),
            font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
            text_color=C.TEXT_1, anchor="w",
        )
        title_lbl.grid(row=0, column=0, sticky="w", padx=S.MD, pady=(S.SM, 0))

        meta = ctk.CTkLabel(
            btn,
            text=f"{formula.get('modality','')} · {formula.get('exam_type','')}",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
        )
        meta.grid(row=1, column=0, sticky="w", padx=S.MD, pady=(0, S.SM))

        # Language badge
        lang_badge = ctk.CTkLabel(
            btn, text=formula.get("language", "")[:3].upper(),
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEAL,
            fg_color=C.TEAL_DIM, corner_radius=4, width=36,
        )
        lang_badge.grid(row=0, column=1, rowspan=2, padx=(0, S.MD), sticky="e")

        def click(_e, f=formula):
            self._select_formula(f)
            for child in self._list_frame.winfo_children():
                try:
                    child.configure(fg_color="transparent")
                except Exception:
                    pass
            btn.configure(fg_color=C.TEAL_DIM)

        for w in (btn, title_lbl, meta):
            w.bind("<Button-1>", click)
            w.bind("<Enter>",    lambda e, b=btn: b.configure(fg_color=C.SURFACE_3)
                                 if b.cget("fg_color") == "transparent" else None)
            w.bind("<Leave>",    lambda e, b=btn: b.configure(fg_color="transparent")
                                 if b.cget("fg_color") == C.SURFACE_3 else None)

    def _select_formula(self, formula: dict):
        self._selected_id = formula.get("id")
        self._preview_title.configure(text=formula.get("title", ""))
        self._preview_meta.configure(
            text=f"{formula.get('modality','')} · {formula.get('exam_type','')} · {formula.get('language','')}"
        )
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.insert("end", formula.get("formula_content", ""))
        self._preview_text.configure(state="disabled")
        self._edit_btn.configure(state="normal")
        self._del_btn.configure(state="normal")

    def _filter_list(self, *_):
        self._load_list()

    # ── CRUD ───────────────────────────────────────────────────────────────
    def _add_formula(self):
        from ui.dialogs.formula_editor import AddFormulaDialog
        dlg = AddFormulaDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            on_saved=self._load_list,
        )
        dlg.grab_set()

    def _edit_selected(self):
        if not self._selected_id:
            return
        db = self._s.get("custom_formulas_db")
        if not db:
            return
        formula = next(
            (f for f in db.get_formulas_by_language(
                self._s.get("current_language_folder", "Francais"))
             if f.get("id") == self._selected_id),
            None,
        )
        if not formula:
            return
        from ui.dialogs.formula_editor import EditFormulaDialog
        dlg = EditFormulaDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            formula=formula,
            on_saved=self._load_list,
        )
        dlg.grab_set()

    def _delete_selected(self):
        if not self._selected_id:
            return
        from ui.dialogs.confirm_dialog import ConfirmDialog
        dlg = ConfirmDialog(
            self.winfo_toplevel(),
            title="Delete template",
            message="Are you sure you want to delete this template?\nThis cannot be undone.",
            on_confirm=self._do_delete,
        )
        dlg.grab_set()

    def _do_delete(self):
        db = self._s.get("custom_formulas_db")
        if db and self._selected_id:
            db.delete_formula(self._selected_id)
            self._selected_id = None
            self._preview_title.configure(text="Select a template")
            self._preview_meta.configure(text="")
            self._preview_text.configure(state="normal")
            self._preview_text.delete("1.0", "end")
            self._preview_text.configure(state="disabled")
            self._edit_btn.configure(state="disabled")
            self._del_btn.configure(state="disabled")
            self._load_list()
