# -*- coding: utf-8 -*-
"""
ui/views/worklist_view.py — PISUM Radiology Worklist
Main entry point: exam queue + quick patient search to add new exams.
Default view: Today's exams only (fast even with 100k+ patients).
"""
import datetime
import logging
import threading
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from ui.theme import C, F, S, R
from ui.components.widgets import PrimaryButton, SecondaryButton, GhostButton

logger = logging.getLogger(__name__)

# ── Status config ──────────────────────────────────────────────────────────
STATUS_CFG = {
    "En attente": ("⏳  Pending",      "#D97706"),
    "En cours":   ("▶  In Progress",   "#14B8A6"),
    "Finalisé":   ("✓  Done",          "#3FB950"),
    "Archivé":    ("⊞  Archived",      "#6E7681"),
}

DATE_RANGES = [
    ("today", "Today"),
    ("week",  "7 Days"),
    ("month", "30 Days"),
    ("all",   "All"),
]

FILTER_TO_STATUT = {
    "all":         None,
    "pending":     "En attente",
    "in_progress": "En cours",
    "done":        "Finalisé",
}


def _calc_age(ddn: str) -> str:
    for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.datetime.strptime(ddn, fmt)
            return str((datetime.date.today() - dt.date()).days // 365)
        except ValueError:
            continue
    return "—"


def _apply_treeview_style():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Worklist.Treeview",
        background=C.SURFACE, foreground=C.TEXT_1,
        fieldbackground=C.SURFACE, rowheight=46,
        font=("Segoe UI", 12), borderwidth=0, relief="flat",
    )
    style.configure("Worklist.Treeview.Heading",
        background=C.SURFACE_2, foreground=C.TEXT_3,
        font=("Segoe UI", 11), relief="flat", borderwidth=0, padding=(10, 10),
    )
    style.map("Worklist.Treeview",
        background=[("selected", C.TEAL_DIM)],
        foreground=[("selected", C.TEAL)],
    )
    style.map("Worklist.Treeview.Heading",
        relief=[("active", "flat")],
        background=[("active", C.SURFACE_3)],
    )
    style.layout("Worklist.Treeview",
                 [("Worklist.Treeview.treearea", {"sticky": "nswe"})])


class WorklistView(ctk.CTkFrame):

    COLUMNS     = ("patient", "age", "exam",   "date",  "status")
    COL_HEADERS = ("Patient", "Age", "Exam",   "Date",  "Status")
    COL_WIDTHS  = (230,        55,   260,       110,     155)
    COL_ANCHORS = ("w",       "center", "w",   "center", "w")

    def __init__(self, master, core_state: dict, on_open_exam=None, **kw):
        kw.setdefault("fg_color", C.BG)
        super().__init__(master, **kw)

        self._s           = core_state
        self._on_open     = on_open_exam
        self._items: list = []
        self._status_filter  = "all"
        self._date_range     = "today"
        self._search_text    = ""
        self._status_btns:  dict[str, ctk.CTkButton] = {}
        self._date_btns:    dict[str, ctk.CTkButton] = {}
        self._pat_results_frame = None    # patient search dropdown
        self._selected_patient  = None   # {"patient_uuid":…, "name":…}
        self._search_job        = None   # debounce timer

        self.grid_rowconfigure(3, weight=1)   # table
        self.grid_columnconfigure(0, weight=1)

        _apply_treeview_style()

        self._build_title_bar()
        self._build_quick_add()
        self._build_filters()
        self._build_table()
        self._build_footer()

        threading.Thread(target=self._load_data, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # BUILD
    # ══════════════════════════════════════════════════════════════════════

    def _build_title_bar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=S.XXL, pady=(S.XL, S.SM))
        bar.grid_columnconfigure(0, weight=1)

        # Left: title + today's date
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            left, text="Worklist",
            font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1,
        ).pack(side="left")

        today_str = datetime.date.today().strftime("%d %b %Y")
        ctk.CTkLabel(
            left, text=f"  —  {today_str}",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_3,
        ).pack(side="left", pady=(4, 0))

        # Right: date-range buttons
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")

        for key, label in DATE_RANGES:
            active = (key == self._date_range)
            btn = ctk.CTkButton(
                right, text=label,
                width=72, height=28,
                font=ctk.CTkFont(*F.CAPTION),
                corner_radius=R.PILL,
                fg_color=C.TEAL_DIM  if active else C.SURFACE,
                hover_color=C.SURFACE_3,
                text_color=C.TEAL    if active else C.TEXT_3,
                border_color=C.TEAL  if active else C.BORDER,
                border_width=1,
                command=lambda k=key: self._set_date_range(k),
            )
            btn.pack(side="left", padx=(0, S.XS))
            self._date_btns[key] = btn

    def _build_quick_add(self):
        """
        Patient search bar → instant dropdown → Add Exam button.
        This is the primary action for 100k+ patient databases.
        """
        outer = ctk.CTkFrame(
            self,
            fg_color=C.SURFACE,
            border_color=C.BORDER, border_width=1,
            corner_radius=R.LG,
        )
        outer.grid(row=1, column=0, sticky="ew", padx=S.XXL, pady=(0, S.SM))
        outer.grid_columnconfigure(1, weight=1)

        # Label
        ctk.CTkLabel(
            outer, text="Quick Add Exam",
            font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
            text_color=C.TEXT_2,
        ).grid(row=0, column=0, padx=(S.LG, S.MD), pady=(S.MD, S.SM), sticky="w")

        # Search entry
        search_wrap = ctk.CTkFrame(outer, fg_color="transparent")
        search_wrap.grid(row=0, column=1, sticky="ew", pady=(S.MD, S.SM),
                         padx=(0, S.SM))
        search_wrap.grid_columnconfigure(0, weight=1)

        self._pat_entry = ctk.CTkEntry(
            search_wrap,
            placeholder_text="🔍  Patient name or file number…",
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont(*F.BODY), corner_radius=R.MD, height=38,
        )
        self._pat_entry.grid(row=0, column=0, sticky="ew")
        self._pat_entry.bind("<KeyRelease>", self._on_pat_keyrelease)
        self._pat_entry.bind("<Escape>",     self._close_dropdown)
        self._pat_entry.bind("<Down>",       self._focus_dropdown)

        # Patient name badge (shown after selection)
        self._pat_badge = ctk.CTkLabel(
            search_wrap, text="",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.SUCCESS,
            fg_color=C.SUCCESS_DIM, corner_radius=R.PILL,
            padx=S.SM, pady=2,
        )
        # Hidden by default; shown after patient selected

        # Add Exam button
        self._add_exam_btn = PrimaryButton(
            outer, "Add Exam  ▶", width=140,
            command=self._quick_add_exam,
            state="disabled",
        )
        self._add_exam_btn.grid(row=0, column=2, padx=(0, S.MD), pady=(S.MD, S.SM))

        # New Patient link
        GhostButton(
            outer, "New Patient",
            icon="👤",
            command=self._on_new_patient,
        ).grid(row=0, column=3, padx=(0, S.SM), pady=(S.MD, S.SM))

        # Dropdown frame (spawned below search bar)
        self._dropdown_anchor = search_wrap

    def _build_filters(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=S.XXL, pady=(0, S.SM))

        filters = [
            ("all",         "All"),
            ("pending",     "⏳  Pending"),
            ("in_progress", "▶  In Progress"),
            ("done",        "✓  Done"),
        ]
        for col, (key, label) in enumerate(filters):
            active = (key == self._status_filter)
            btn = ctk.CTkButton(
                bar, text=label,
                width=130, height=28,
                font=ctk.CTkFont(*F.BODY_SM), corner_radius=R.PILL,
                fg_color=C.TEAL_DIM  if active else C.SURFACE,
                hover_color=C.SURFACE_3,
                text_color=C.TEAL    if active else C.TEXT_2,
                border_color=C.TEAL  if active else C.BORDER,
                border_width=1,
                command=lambda k=key: self._set_status_filter(k),
            )
            btn.grid(row=0, column=col, padx=(0, S.SM))
            self._status_btns[key] = btn

        self._shown_lbl = ctk.CTkLabel(
            bar, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
        )
        self._shown_lbl.grid(row=0, column=len(filters) + 1, padx=(S.LG, 0))

    def _build_table(self):
        outer = tk.Frame(self, bg=C.BG, bd=0, highlightthickness=0)
        outer.grid(row=3, column=0, sticky="nsew", padx=S.XXL, pady=(0, S.SM))
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        vsb = ttk.Scrollbar(outer, orient="vertical")
        vsb.grid(row=0, column=1, sticky="ns")

        self._tree = ttk.Treeview(
            outer, columns=self.COLUMNS, show="headings",
            style="Worklist.Treeview", yscrollcommand=vsb.set,
            selectmode="browse",
        )
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.configure(command=self._tree.yview)

        for col, header, width, anchor in zip(
                self.COLUMNS, self.COL_HEADERS, self.COL_WIDTHS, self.COL_ANCHORS):
            self._tree.heading(col, text=header)
            self._tree.column(col, width=width, minwidth=50,
                              anchor=anchor, stretch=(col == "exam"))

        self._tree.tag_configure("pending",     foreground="#D97706")
        self._tree.tag_configure("in_progress", foreground="#14B8A6")
        self._tree.tag_configure("done",        foreground="#6E7681")
        self._tree.tag_configure("archived",    foreground="#484F58")
        self._tree.tag_configure("row_odd",     background=C.SURFACE)
        self._tree.tag_configure("row_even",    background="#12171E")

        self._tree.bind("<ButtonRelease-1>", self._on_row_click)
        self._tree.bind("<Return>",          self._on_row_enter)

        self._empty_lbl = ctk.CTkLabel(
            self,
            text="No exams for this period.\n"
                 "Search a patient above and click  ▶ Add Exam.",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_3, justify="center",
        )

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color=C.SIDEBAR, corner_radius=0, height=30)
        footer.grid(row=4, column=0, sticky="ew")
        footer.pack_propagate(False)

        self._footer_lbl = ctk.CTkLabel(
            footer, text="Loading…",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
        )
        self._footer_lbl.pack(side="left", padx=S.LG)

        ctk.CTkButton(
            footer, text="↻  Refresh", width=80, height=22,
            fg_color="transparent", hover_color=C.SURFACE_3,
            text_color=C.TEXT_3, font=ctk.CTkFont(*F.CAPTION),
            corner_radius=R.SM, command=self.refresh,
        ).pack(side="right", padx=S.MD)

    # ══════════════════════════════════════════════════════════════════════
    # PATIENT QUICK-SEARCH
    # ══════════════════════════════════════════════════════════════════════

    def _on_pat_keyrelease(self, event=None):
        """Debounced search: 300 ms after last keystroke."""
        if event and getattr(event, "keysym", "") in ("Down", "Up", "Escape", "Return"):
            return
        if self._search_job:
            self.after_cancel(self._search_job)
        q = self._pat_entry.get().strip()
        if not q:
            self._close_dropdown()
            self._deselect_patient()
            return
        self._search_job = self.after(300, lambda: self._run_search(q))

    def _run_search(self, query: str):
        threading.Thread(
            target=self._search_patients_bg,
            args=(query,), daemon=True,
        ).start()

    def _search_patients_bg(self, query: str):
        try:
            from pacs_ris_db import get_pacs_db
            results = get_pacs_db().search_patients(query, limit=10)
            self.after(0, lambda: self._show_dropdown(results) if self.winfo_exists() else None)
        except Exception as e:
            logger.error(f"Patient search error: {e}")

    def _show_dropdown(self, patients: list):
        self._close_dropdown()
        if not patients:
            return

        # Place dropdown below the search entry
        frame = ctk.CTkFrame(
            self,
            fg_color=C.SURFACE_2,
            border_color=C.BORDER, border_width=1,
            corner_radius=R.MD,
        )

        # Position it under the search bar
        self._pat_entry.update_idletasks()
        x = self._pat_entry.winfo_rootx() - self.winfo_rootx()
        y = (self._pat_entry.winfo_rooty() - self.winfo_rooty()
             + self._pat_entry.winfo_height() + 2)
        w = self._pat_entry.winfo_width()

        frame.place(x=x, y=y, width=w)
        self._pat_results_frame = frame

        btns = []
        for p in patients:
            nom    = p.get("nom", "")
            prenom = p.get("prenom", "")
            ddn    = p.get("date_naissance", "")
            ndos   = p.get("num_dossier", "")
            age    = _calc_age(ddn)
            sexe   = p.get("sexe", "")

            line1 = f"{nom.upper()}, {prenom}"
            line2_parts = [f"{age} ans" if age != "—" else "",
                           sexe, ndos]
            line2 = "  •  ".join(x for x in line2_parts if x)

            cmd = lambda uuid=p["patient_uuid"], n=f"{nom} {prenom}": self._select_patient(uuid, n.strip())
            row_btn = ctk.CTkButton(
                frame,
                text=f"{line1}    {line2}",
                anchor="w",
                fg_color="transparent", hover_color=C.SURFACE_3,
                text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY_SM),
                height=36, corner_radius=0,
                command=cmd,
            )
            row_btn.pack(fill="x", padx=2, pady=1)
            row_btn.bind("<Return>", lambda e, c=cmd: c())
            btns.append(row_btn)

        # "New patient" at bottom
        ctk.CTkFrame(frame, height=1, fg_color=C.BORDER).pack(fill="x", pady=2)
        new_pat_cmd = self._on_new_patient
        new_pat_btn = ctk.CTkButton(
            frame,
            text="+ Create new patient",
            anchor="w",
            fg_color="transparent", hover_color=C.SUCCESS_DIM,
            text_color=C.SUCCESS, font=ctk.CTkFont(*F.BODY_SM),
            height=32, corner_radius=0,
            command=new_pat_cmd,
        )
        new_pat_btn.pack(fill="x", padx=2, pady=(0, 2))
        new_pat_btn.bind("<Return>", lambda e, c=new_pat_cmd: c())
        btns.append(new_pat_btn)

        # Keyboard navigation bindings
        for i, btn in enumerate(btns):
            btn.bind("<Up>", lambda e, idx=i: self._nav_dropdown(idx - 1))
            btn.bind("<Down>", lambda e, idx=i: self._nav_dropdown(idx + 1))
            btn.bind("<FocusIn>", lambda e, b=btn: b.configure(fg_color=b.cget("hover_color")))
            btn.bind("<FocusOut>", lambda e, b=btn: b.configure(fg_color="transparent"))

    def _close_dropdown(self, _event=None):
        if self._pat_results_frame and self._pat_results_frame.winfo_exists():
            self._pat_results_frame.place_forget()
            self._pat_results_frame.destroy()
        self._pat_results_frame = None

    def _focus_dropdown(self, _event=None):
        if self._pat_results_frame:
            children = [w for w in self._pat_results_frame.winfo_children() if isinstance(w, ctk.CTkButton)]
            if children:
                children[0].focus_set()

    def _nav_dropdown(self, index: int):
        if not self._pat_results_frame:
            return
        children = [w for w in self._pat_results_frame.winfo_children() if isinstance(w, ctk.CTkButton)]
        if index < 0:
            self._pat_entry.focus_set()
        elif index < len(children):
            children[index].focus_set()

    def _select_patient(self, patient_uuid: str, name: str):
        self._selected_patient = {"patient_uuid": patient_uuid, "name": name}
        self._close_dropdown()
        # Show badge
        self._pat_badge.configure(text=f"✓  {name}")
        self._pat_badge.grid(row=1, column=0, sticky="w", pady=(0, S.SM))
        self._pat_entry.delete(0, "end")
        self._pat_entry.configure(
            placeholder_text=f"✓  {name}  — or type to search another",
            placeholder_text_color=C.SUCCESS,
        )
        self._add_exam_btn.configure(state="normal")

    def _deselect_patient(self):
        self._selected_patient = None
        self._pat_badge.grid_forget()
        self._pat_entry.configure(
            placeholder_text="🔍  Patient name or file number…",
            placeholder_text_color=C.TEXT_3,
        )
        self._add_exam_btn.configure(state="disabled")

    # ══════════════════════════════════════════════════════════════════════
    # QUICK ADD EXAM
    # ══════════════════════════════════════════════════════════════════════

    def _quick_add_exam(self):
        if not self._selected_patient:
            self._show_banner("Please select a patient first.", "warning")
            return

        # Save patient name before the state gets cleared
        patient_name = self._selected_patient.get('name', 'Unknown Patient')

        from ui.dialogs.exam_dialog import ExamDialog
        dlg = ExamDialog(
            self.winfo_toplevel(),
            core_state=self._s,
            pre_patient=self._selected_patient,
        )
        self.wait_window(dlg)

        if getattr(dlg, "result", None):
            try:
                from pacs_ris_db import get_pacs_db
                get_pacs_db().add_examen(**dlg.result)
                self._deselect_patient()
                self.refresh()
                self._show_banner(f"✓  Exam successfully added for {patient_name}", "success")
            except Exception as e:
                logger.error(f"Add exam error: {e}")
                self._show_banner(f"Error adding exam: {e}", "error")

    # ══════════════════════════════════════════════════════════════════════
    # DATA LOADING
    # ══════════════════════════════════════════════════════════════════════

    def _load_data(self):
        try:
            from pacs_ris_db import get_pacs_db
            items = get_pacs_db().get_worklist(
                date_range=self._date_range,
                statut_filter=FILTER_TO_STATUT.get(self._status_filter),
            )
            self._s["worklist_items"] = items
            self._items = items
            self.after(0, self._populate_table)
        except Exception as e:
            logger.error(f"WorklistView load error: {e}", exc_info=True)
            self.after(0, lambda err=e: (
                self._footer_lbl.configure(text=f"⚠  Load error: {err}")
                if self.winfo_exists() else None
            ))

    def refresh(self):
        self._footer_lbl.configure(text="Refreshing…")
        threading.Thread(target=self._load_data, daemon=True).start()

    def _populate_table(self):
        if not self.winfo_exists():
            return
        for row in self._tree.get_children():
            self._tree.delete(row)

        if not self._items:
            self._empty_lbl.place(relx=0.5, rely=0.6, anchor="center")
        else:
            self._empty_lbl.place_forget()

        for i, item in enumerate(self._items):
            nom    = item.get("nom", "")
            prenom = item.get("prenom", "")
            name   = f"{nom.upper()}, {prenom}".strip(", ") or "—"
            age    = _calc_age(item.get("date_naissance", ""))
            exam   = " — ".join(filter(None, [
                item.get("modalite", ""),
                item.get("type_examen", ""),
            ])) or "—"
            date   = item.get("date_examen", "—")
            statut = item.get("statut", "En attente")
            label, _ = STATUS_CFG.get(statut, STATUS_CFG["En attente"])

            tag = {
                "En attente": "pending",
                "En cours":   "in_progress",
                "Finalisé":   "done",
                "Archivé":    "archived",
            }.get(statut, "pending")

            self._tree.insert(
                "", "end",
                iid=item.get("examen_uuid", f"_r{i}"),
                values=(name, age, exam, date, label),
                tags=(tag, "row_odd" if i % 2 else "row_even"),
            )

        total    = len(self._items)
        pending  = sum(1 for x in self._items if x.get("statut") == "En attente")
        in_prog  = sum(1 for x in self._items if x.get("statut") == "En cours")
        done     = sum(1 for x in self._items if x.get("statut") == "Finalisé")
        range_lbl = dict(DATE_RANGES).get(self._date_range, self._date_range)

        self._footer_lbl.configure(
            text=f"{range_lbl}  —  {total} exams  •  "
                 f"{pending} pending  •  {in_prog} in progress  •  {done} done"
        )
        self._shown_lbl.configure(text=f"{total} shown")

    # ══════════════════════════════════════════════════════════════════════
    # FILTERS
    # ══════════════════════════════════════════════════════════════════════

    def _set_date_range(self, key: str):
        self._date_range = key
        for k, btn in self._date_btns.items():
            active = (k == key)
            btn.configure(
                fg_color=C.TEAL_DIM  if active else C.SURFACE,
                text_color=C.TEAL    if active else C.TEXT_3,
                border_color=C.TEAL  if active else C.BORDER,
            )
        self.refresh()

    def _set_status_filter(self, key: str):
        self._status_filter = key
        for k, btn in self._status_btns.items():
            active = (k == key)
            btn.configure(
                fg_color=C.TEAL_DIM  if active else C.SURFACE,
                text_color=C.TEAL    if active else C.TEXT_2,
                border_color=C.TEAL  if active else C.BORDER,
            )
        self.refresh()

    # ══════════════════════════════════════════════════════════════════════
    # ROW CLICK
    # ══════════════════════════════════════════════════════════════════════

    def _on_row_click(self, _event=None):
        self._close_dropdown()
        sel = self._tree.selection()
        if sel:
            self._open_exam(sel[0])

    def _on_row_enter(self, _event=None):
        sel = self._tree.selection()
        if sel:
            self._open_exam(sel[0])

    def _open_exam(self, exam_uuid: str):
        item = next((x for x in self._items
                     if x.get("examen_uuid") == exam_uuid), None)
        if item and self._on_open:
            self._on_open(item)

    # ══════════════════════════════════════════════════════════════════════
    # NEW PATIENT
    # ══════════════════════════════════════════════════════════════════════

    def _on_new_patient(self):
        self._close_dropdown()
        from ui.dialogs.patient_dialog import PatientDialog
        dlg = PatientDialog(self.winfo_toplevel())
        self.wait_window(dlg)
        result = getattr(dlg, "result", None)
        if not result:
            return
        try:
            from pacs_ris_db import get_pacs_db
            patient_uuid = get_pacs_db().add_patient(**result)
        except Exception as e:
            logger.error(f"Add patient error: {e}")
            self._show_banner(f"Error: {e}", "error")
            return

        if not patient_uuid:
            self._show_banner(
                "A patient with the same name / DOB already exists.", "warning")
            return

        name = f"{result.get('nom', '')} {result.get('prenom', '')}".strip()
        # Auto-select the new patient in the quick-add bar
        self._select_patient(patient_uuid, name)
        self._show_banner(
            f"✓  Patient '{name}' created — fill the exam form and click  ▶ Add Exam.",
            "success")

    # ══════════════════════════════════════════════════════════════════════
    # BANNER
    # ══════════════════════════════════════════════════════════════════════

    def _show_banner(self, message: str, style: str = "info"):
        colors = {
            "success": (C.SUCCESS_DIM, C.SUCCESS),
            "warning": (C.WARNING_DIM, C.WARNING),
            "error":   (C.ERROR_DIM,   C.ERROR),
            "info":    (C.TEAL_DIM,    C.TEAL),
        }
        bg, fg = colors.get(style, colors["info"])

        # Remove previous banner if any
        for w in self.winfo_children():
            if getattr(w, "_is_banner", False):
                w.destroy()

        banner = ctk.CTkFrame(self, fg_color=bg, corner_radius=R.MD)
        banner._is_banner = True
        banner.grid(row=5, column=0, sticky="ew", padx=S.XXL, pady=(0, S.SM))
        banner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            banner, text=message,
            font=ctk.CTkFont(*F.BODY_SM), text_color=fg, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=S.LG, pady=S.SM)

        ctk.CTkButton(
            banner, text="×", width=26, height=26,
            fg_color="transparent", hover_color=bg,
            text_color=fg, font=ctk.CTkFont(*F.BODY),
            command=banner.destroy,
        ).grid(row=0, column=1, padx=(0, S.SM))

        self.after(6000, lambda: banner.destroy() if banner.winfo_exists() else None)
