# -*- coding: utf-8 -*-
"""
ui/dialogs/formula_search.py — Global Formula Search Dialog
"""
import pandas as pd
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, PrimaryButton, SecondaryButton, Divider,
)

MAX_RESULTS = 2000


class FormulaSearchDialog(ctk.CTkToplevel):
    def __init__(self, master, core_state: dict, on_select=None, **kw):
        super().__init__(master, **kw)
        self.title("Search Templates")
        self.geometry("900x640")
        self.resizable(True, True)
        self.configure(fg_color=C.BG)
        self.lift()
        self.focus_force()

        self._s         = core_state
        self._on_select = on_select
        self._index: list[dict] = []
        self._filtered: list[dict] = []
        self._selected: dict | None = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._build_index()

    # ── Header ──────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=C.SURFACE,
                           border_color=C.BORDER, border_width=0,
                           corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            hdr, text="Search All Templates",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, padx=S.XL, pady=S.LG)

        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", self._on_search)

        self._search_entry = ctk.CTkEntry(
            hdr,
            textvariable=self._search_var,
            placeholder_text="Type to search modality, exam type, title…",
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont(*F.BODY), height=38, corner_radius=R.MD,
        )
        self._search_entry.grid(row=0, column=1, sticky="ew",
                                padx=(0, S.XL), pady=S.LG)
        self._search_entry.focus_set()

        self._count_lbl = ctk.CTkLabel(
            hdr, text="Building index…",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
        )
        self._count_lbl.grid(row=0, column=2, padx=(0, S.XL))

    # ── Body ────────────────────────────────────────────────────────────────
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew",
                  padx=S.XL, pady=(S.MD, S.MD))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        # ── Results list ──────────────────────────────────────────────
        list_frame = ctk.CTkFrame(body, fg_color=C.SURFACE,
                                  border_color=C.BORDER, border_width=1,
                                  corner_radius=R.LG)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, S.MD))
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        # Column headers
        col_hdr = ctk.CTkFrame(list_frame, fg_color=C.SURFACE_2, corner_radius=0)
        col_hdr.grid(row=0, column=0, sticky="ew")
        col_hdr.grid_columnconfigure((0,1,2), weight=1)
        for c, lbl in enumerate(("Modality", "Exam Type", "Template Title")):
            ctk.CTkLabel(
                col_hdr, text=lbl,
                font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
            ).grid(row=0, column=c, padx=S.MD, pady=S.SM)

        self._result_frame = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        self._result_frame.grid(row=1, column=0, sticky="nsew")
        self._result_frame.grid_columnconfigure((0,1,2), weight=1)

        # ── Preview panel ────────────────────────────────────────────
        preview = ctk.CTkFrame(body, fg_color=C.SURFACE,
                               border_color=C.BORDER, border_width=1,
                               corner_radius=R.LG)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.grid_rowconfigure(2, weight=1)
        preview.grid_columnconfigure(0, weight=1)

        self._preview_title = ctk.CTkLabel(
            preview, text="Preview",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        )
        self._preview_title.grid(row=0, column=0, sticky="w",
                                 padx=S.LG, pady=(S.LG, S.SM))

        Divider(preview).grid(row=1, column=0, sticky="ew",
                              padx=S.LG, pady=(0, S.SM))

        self._preview_text = ctk.CTkTextbox(
            preview, fg_color="transparent",
            text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY),
            wrap="word", state="disabled",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        self._preview_text.grid(row=2, column=0, sticky="nsew",
                                padx=S.MD, pady=(0, S.MD))

    # ── Footer ──────────────────────────────────────────────────────────────
    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=0,
                              border_color=C.BORDER, border_width=0)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        btn_row = ctk.CTkFrame(footer, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="e", padx=S.XL, pady=S.MD)

        SecondaryButton(
            btn_row, "Close",
            command=self.destroy,
        ).pack(side="left", padx=(0, S.SM))

        self._use_btn = PrimaryButton(
            btn_row, "Use Template", icon="✓",
            command=self._use_selected,
            state="disabled",
        )
        self._use_btn.pack(side="left")

    # ── Index building ──────────────────────────────────────────────────────
    def _build_index(self):
        import threading
        threading.Thread(target=self._do_build_index, daemon=True).start()

    def _do_build_index(self):
        try:
            self._do_build_index_inner()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"FormulaSearch index error: {e}", exc_info=True)
            self.after(0, lambda: self._count_lbl.configure(
                text=f"Error building index: {e}", text_color=C.ERROR))
            self.after(0, self._render_results)

    def _do_build_index_inner(self):
        data     = self._s.get("data", {})
        db       = self._s.get("custom_formulas_db")
        lang_f   = self._s.get("current_language_folder", "Francais")
        t        = self._s.get("translations", {})
        mod_map  = t.get("modalites", {})
        lm       = self._s.get("lm")

        # If data is empty (e.g. network failure at startup), try a fresh load
        if not data:
            try:
                from supabase_templates_loader import load_templates, clear_cache
                user_plan_raw = lm.get_plan_name() if lm else "free"
                clear_cache()
                data = load_templates(lang_f, user_plan=user_plan_raw)
                if data:
                    self._s["data"] = data  # update shared state
            except Exception:
                pass

        def normalize_plan(p):
            p = str(p).lower() if p else "free"
            for k in ("free", "solo", "pro", "clinic"):
                if k in p:
                    return k
            return "free"

        PLAN_ORDER = {"free": 0, "solo": 2, "pro": 2, "clinic": 3}
        user_plan  = normalize_plan(lm.get_plan_name() if lm else "free")
        user_order = PLAN_ORDER.get(user_plan, 0)

        index = []

        for mod_key, exam_types in data.items():
            mod_display = mod_map.get(mod_key, mod_key)
            for exam_type, page_data in exam_types.items():
                df_data  = page_data.get("data",  pd.DataFrame()) if isinstance(page_data, dict) else page_data
                df_plans = page_data.get("plans", pd.DataFrame()) if isinstance(page_data, dict) else pd.DataFrame()

                for title in df_data.columns:
                    formula_plan = "free"
                    if title in df_plans.columns and len(df_plans):
                        formula_plan = normalize_plan(df_plans[title].iloc[0])
                    locked = PLAN_ORDER.get(formula_plan, 0) > user_order

                    val = df_data[title].iloc[0] if len(df_data) else None
                    content = "" if (val is None or pd.isna(val)) else str(val)

                    index.append({
                        "modality":        mod_display,
                        "modality_key":    mod_key,
                        "exam_type":       exam_type,
                        "title":           title,
                        "formula_content": content,
                        "plan":            formula_plan,
                        "locked":          locked,
                        "source":          "standard",
                    })

        if db:
            for cf in db.get_formulas_by_language(lang_f):
                index.append({
                    "modality":        cf.get("modality", ""),
                    "modality_key":    cf.get("modality", ""),
                    "exam_type":       cf.get("exam_type", ""),
                    "title":           cf.get("title", ""),
                    "formula_content": cf.get("formula_content", ""),
                    "plan":            "free",
                    "locked":          False,
                    "source":          "custom",
                })

        free_count   = sum(1 for i in index if not i["locked"])
        locked_count = sum(1 for i in index if i["locked"])
        if index:
            status = f"{free_count:,} free  •  {locked_count:,} locked"
        else:
            status = "No templates — check network connection"

        self._index = index
        self.after(0, lambda: self._count_lbl.configure(text=status))
        self.after(0, self._render_results)

    # ── Search & Render ─────────────────────────────────────────────────────
    def _on_search(self, *_):
        self.after(80, self._render_results)

    def _render_results(self):
        q = self._search_var.get().lower().strip()

        if q:
            results = [
                r for r in self._index
                if q in (r["modality"] + r["exam_type"] + r["title"] +
                         r["formula_content"]).lower()
            ]
        else:
            results = self._index[:]

        self._filtered = results[:MAX_RESULTS]

        for w in self._result_frame.winfo_children():
            w.destroy()

        if not self._filtered:
            if not self._index:
                msg = "No templates loaded — check network connection and reopen."
            else:
                msg = "No templates match your search."
            ctk.CTkLabel(
                self._result_frame, text=msg,
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
            ).grid(row=0, column=0, columnspan=3, pady=S.XXL)
            self._count_lbl.configure(text="0 results")
            return

        self._count_lbl.configure(
            text=f"{len(self._filtered):,} results"
            + (" (capped)" if len(results) > MAX_RESULTS else "")
        )

        def group_templates(templates):
            free, pro, clinic = [], [], []
            for t in templates:
                plan = t.get("plan", "").lower()
                if plan == "free":
                    free.append(t)
                elif plan == "pro":
                    pro.append(t)
                else:
                    clinic.append(t)
            return free, pro, clinic

        free, pro, clinic = group_templates(self._filtered)
        print("FREE:", len(free), "PRO:", len(pro), "CLINIC:", len(clinic))

        current_row_idx = 0

        for sec_name, sec_items in [("FREE TEMPLATES", free), ("PRO TEMPLATES", pro), ("CLINIC TEMPLATES", clinic)]:
            if not sec_items:
                continue

            hdr = ctk.CTkLabel(
                self._result_frame, text=sec_name,
                font=ctk.CTkFont(*F.BODY, weight="bold"), text_color=C.TEXT_1
            )
            hdr.grid(row=current_row_idx, column=0, columnspan=3, sticky="w", padx=S.MD, pady=(S.MD, S.XS))
            current_row_idx += 1

            for idx, item in enumerate(sec_items):
                bg = C.SURFACE_2 if idx % 2 == 0 else "transparent"
                row = ctk.CTkFrame(self._result_frame, fg_color=bg,
                                   corner_radius=0, cursor="hand2")
                row._default_bg = bg
                row.grid(row=current_row_idx, column=0, columnspan=3, sticky="ew")
                row.grid_columnconfigure((0, 1, 2), weight=1)

                col_c = C.LOCK_FG if item["locked"] else C.TEXT_1
                cus_badge = "* " if item["source"] == "custom" else ""

                for c, val in enumerate([
                    item["modality"], item["exam_type"],
                    cus_badge + item["title"],
                ]):
                    ctk.CTkLabel(
                        row, text=val,
                        font=ctk.CTkFont(*F.BODY_SM),
                        text_color=col_c, anchor="w",
                    ).grid(row=0, column=c, sticky="w",
                           padx=S.MD, pady=S.SM)

                def click(_e, it=item, r=row):
                    self._select_result(it)
                    for child in self._result_frame.winfo_children():
                        if isinstance(child, ctk.CTkFrame) and hasattr(child, "_default_bg"):
                            try:
                                child.configure(fg_color=child._default_bg)
                            except Exception:
                                pass
                    r.configure(fg_color=C.TEAL_DIM)

                row.bind("<Button-1>", click)
                for child in row.winfo_children():
                    child.bind("<Button-1>", click)

                current_row_idx += 1

    def _select_result(self, item: dict):
        self._selected = item
        self._preview_title.configure(
            text=f"{item['modality']} · {item['exam_type']}\n{item['title']}"
        )
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        if item["locked"]:
            self._preview_text.insert("end",
                "[LOCKED] Upgrade your plan to access this template.")
        else:
            self._preview_text.insert("end", item.get("formula_content", ""))
        self._preview_text.configure(state="disabled")
        self._use_btn.configure(state="normal" if not item["locked"] else "disabled")

    def _use_selected(self):
        if self._selected and self._on_select:
            self._on_select(self._selected)
        self.destroy()
