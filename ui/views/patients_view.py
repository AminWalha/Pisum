# -*- coding: utf-8 -*-
"""
ui/views/patients_view.py — PACS/RIS Bridge
Opens the existing wx-based PACS window in a thread-safe way.
"""
import threading
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import (
    Card, SectionLabel, Divider,
    PrimaryButton, SecondaryButton, Badge,
)


class PatientsView(ctk.CTkFrame):
    def __init__(self, master, core_state: dict, on_navigate=None,
                 on_open_pacs=None, **kw):
        kw.setdefault("fg_color", C.BG)
        super().__init__(master, **kw)

        self._s          = core_state
        self._nav        = on_navigate
        self._open_pacs  = on_open_pacs

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        lm = self._s.get("lm")
        self._has_pacs = lm.can_use_feature("pacs_ris") if lm else False

        self._build_header()
        if self._has_pacs:
            self._build_body()
        else:
            self._build_locked()

    # ── Header ─────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=S.XXL, pady=(S.XXL, S.LG))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text="PACS / RIS",
            font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            hdr,
            text="Manage patients, exams and link reports to your PACS/RIS system.",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_2, anchor="w",
        ).grid(row=1, column=0, sticky="w")

        if self._has_pacs:
            PrimaryButton(
                hdr, "Open PACS / RIS", icon="♡", width=180,
                command=self._launch_pacs,
            ).grid(row=0, column=1, rowspan=2, padx=(S.MD, 0))

    # ── Locked ─────────────────────────────────────────────────────────────
    def _build_locked(self):
        card = Card(self)
        card.grid(row=1, column=0, padx=S.XXL, pady=S.XL, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="♡",
            font=ctk.CTkFont("Segoe UI", 48), text_color=C.LOCK_FG,
        ).grid(row=0, column=0, pady=(S.XXL, S.MD))
        ctk.CTkLabel(
            card, text="PACS/RIS integration requires SOLO plan or higher",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_2,
        ).grid(row=1, column=0, pady=(0, S.SM))
        ctk.CTkLabel(
            card,
            text="Connect your worklist, auto-fill patient data, and save reports directly.",
            font=ctk.CTkFont(*F.BODY), text_color=C.TEXT_3,
        ).grid(row=2, column=0, pady=(0, S.XL))
        PrimaryButton(
            card, "Upgrade Plan", icon="◈", width=180,
            command=lambda: self._nav("license") if self._nav else None,
        ).grid(row=3, column=0, pady=(0, S.XXL))

    # ── Body (has PACS access) ──────────────────────────────────────────────
    def _build_body(self):
        body = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=C.SURFACE_3,
            scrollbar_button_hover_color=C.BORDER_2,
        )
        body.grid(row=1, column=0, sticky="nsew", padx=S.XXL, pady=(0, S.XL))
        body.grid_columnconfigure(0, weight=1)

        # Current patient card
        pacs = self._s.get("pacs_state", {})
        patient = pacs.get("current_patient")
        examen  = pacs.get("current_examen")

        current_card = Card(body)
        current_card.grid(row=0, column=0, sticky="ew", pady=(0, S.LG))
        current_card.grid_columnconfigure(0, weight=1)

        hdr = ctk.CTkFrame(current_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=S.LG, pady=(S.LG, S.MD))
        hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            hdr, text="Current Patient",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        Divider(current_card).grid(row=1, column=0, sticky="ew",
                                   padx=S.LG, pady=(0, S.MD))

        if patient:
            name = f"{patient.get('prenom', '')} {patient.get('nom', '')}".strip()
            dob  = patient.get("date_naissance", patient.get("ddn", "—"))
            dossier = patient.get("num_dossier", "—")
            rows = [
                ("Name",           name),
                ("Date of birth",  dob),
                ("File number",    dossier),
            ]
            if examen:
                rows += [
                    ("Modality",   examen.get("modalite", "—")),
                    ("Exam type",  examen.get("type_examen", "—")),
                    ("Date",       examen.get("date_examen", "—")),
                ]
            grid = ctk.CTkFrame(current_card, fg_color="transparent")
            grid.grid(row=2, column=0, sticky="ew", padx=S.LG, pady=(0, S.LG))
            grid.grid_columnconfigure(1, weight=1)
            for i, (label, val) in enumerate(rows):
                ctk.CTkLabel(
                    grid, text=label + ":", font=ctk.CTkFont(*F.BODY_SM),
                    text_color=C.TEXT_3, anchor="w",
                ).grid(row=i, column=0, sticky="w", pady=3, padx=(0, S.LG))
                ctk.CTkLabel(
                    grid, text=str(val), font=ctk.CTkFont(*F.BODY_SM),
                    text_color=C.TEXT_1, anchor="w",
                ).grid(row=i, column=1, sticky="w", pady=3)
        else:
            ctk.CTkLabel(
                current_card,
                text="No patient loaded. Open PACS/RIS to select an exam.",
                font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
            ).grid(row=2, column=0, pady=(0, S.LG))

        # Instructions card
        info_card = Card(body)
        info_card.grid(row=1, column=0, sticky="ew")
        info_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            info_card, text="How it works",
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=S.LG, pady=(S.LG, S.MD))
        Divider(info_card).grid(row=1, column=0, sticky="ew",
                                padx=S.LG, pady=(0, S.MD))

        steps = [
            ("1", "Open PACS/RIS",         "Click the button above to open the worklist."),
            ("2", "Select an exam",        "Browse patients, select an exam from the worklist."),
            ("3", "Use in report",         "Click 'Use in Report' to auto-fill the report view."),
            ("4", "Save to PACS",          "After writing the report, save it back to the patient dossier."),
        ]
        for i, (num, title, desc) in enumerate(steps):
            row = ctk.CTkFrame(info_card, fg_color="transparent")
            row.grid(row=2+i, column=0, sticky="ew",
                     padx=S.LG, pady=(0, S.MD if i < len(steps)-1 else S.LG))
            row.grid_columnconfigure(1, weight=1)

            circle = ctk.CTkLabel(
                row, text=num,
                width=28, height=28,
                fg_color=C.TEAL_DIM, text_color=C.TEAL,
                font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
                corner_radius=R.PILL,
            )
            circle.grid(row=0, column=0, rowspan=2, padx=(0, S.MD))

            ctk.CTkLabel(
                row, text=title, font=ctk.CTkFont(*F.BODY_SM, weight="bold"),
                text_color=C.TEXT_1, anchor="w",
            ).grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(
                row, text=desc, font=ctk.CTkFont(*F.CAPTION),
                text_color=C.TEXT_3, anchor="w",
            ).grid(row=1, column=1, sticky="w")

    # ── Launch PACS ────────────────────────────────────────────────────────
    def _launch_pacs(self):
        if self._open_pacs:
            self._open_pacs()
