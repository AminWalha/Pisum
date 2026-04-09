# -*- coding: utf-8 -*-
"""
ui/dialogs/patient_dialog.py — Add / Edit patient (CTk modal).
Sets self.result = dict of patient fields on save, None on cancel.
"""
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import LabeledEntry, LabeledCombo, PrimaryButton, GhostButton, Divider


class PatientDialog(ctk.CTkToplevel):
    def __init__(self, master, patient: dict = None, **kw):
        super().__init__(master, **kw)
        self.result  = None
        self._edit   = patient  # None = new, dict = edit mode

        title = "Edit Patient" if patient else "New Patient"
        self.title(title)
        self.geometry("500x620")
        self.resizable(False, False)
        self.configure(fg_color=C.BG)
        # Detach from overrideredirect parent to avoid Windows crash
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

        if patient:
            self._populate(patient)

        self.bind("<Escape>", lambda _: self._cancel())

    # ── Header ─────────────────────────────────────────────────────────────
    def _build_header(self, title: str):
        hdr = ctk.CTkFrame(self, fg_color=C.SURFACE,
                           border_color=C.BORDER, border_width=1,
                           corner_radius=0, height=56)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.pack_propagate(False)

        ctk.CTkLabel(
            hdr, text=("✏️  " if self._edit else "👤  ") + title,
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

        # Row 0: Nom + Prénom (required)
        self._nom = LabeledEntry(scroll, "Last Name *", placeholder="Smith")
        self._nom.grid(row=0, column=0, sticky="ew", padx=(0, S.SM), pady=(0, S.MD))

        self._prenom = LabeledEntry(scroll, "First Name *", placeholder="John")
        self._prenom.grid(row=0, column=1, sticky="ew", pady=(0, S.MD))

        # Row 1: Date naissance + Sexe
        self._ddn = LabeledEntry(scroll, "Date of Birth", placeholder="DD-MM-YYYY")
        self._ddn.grid(row=1, column=0, sticky="ew", padx=(0, S.SM), pady=(0, S.MD))

        self._sexe = LabeledCombo(scroll, "Sex", values=["", "M", "F", "Autre"])
        self._sexe.grid(row=1, column=1, sticky="ew", pady=(0, S.MD))

        # Row 2: CIN + Téléphone
        self._cin = LabeledEntry(scroll, "ID / Passport", placeholder="ABC123")
        self._cin.grid(row=2, column=0, sticky="ew", padx=(0, S.SM), pady=(0, S.MD))

        self._tel = LabeledEntry(scroll, "Phone", placeholder="+212 6xx xxx xxx")
        self._tel.grid(row=2, column=1, sticky="ew", pady=(0, S.MD))

        # Row 3: Pays (full width)
        self._pays = LabeledEntry(scroll, "Country", placeholder="Morocco")
        self._pays.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, S.MD))

        # Row 4: Adresse
        self._adresse = LabeledEntry(scroll, "Address", placeholder="12 Rue des Fleurs…")
        self._adresse.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, S.MD))

        # Row 5: Remarques (multiline via textbox)
        ctk.CTkLabel(
            scroll, text="Notes",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, S.XS))

        self._rem = ctk.CTkTextbox(
            scroll, height=80,
            fg_color=C.SURFACE_3, border_color=C.BORDER, border_width=1,
            text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY),
            corner_radius=R.MD,
        )
        self._rem.grid(row=6, column=0, columnspan=2, sticky="ew")

        self._error_lbl = ctk.CTkLabel(
            scroll, text="",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.ERROR, anchor="w",
        )
        self._error_lbl.grid(row=7, column=0, columnspan=2, sticky="w",
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

        lbl = "Save Changes" if self._edit else "Create Patient"
        PrimaryButton(footer, lbl, icon="✔", width=180,
                      command=self._save).pack(side="right", padx=S.LG)

    # ── Logic ──────────────────────────────────────────────────────────────
    def _populate(self, p: dict):
        self._nom.set(p.get("nom", ""))
        self._prenom.set(p.get("prenom", ""))
        self._ddn.set(p.get("date_naissance", ""))
        self._sexe.set(p.get("sexe", ""))
        self._cin.set(p.get("cin", ""))
        self._tel.set(p.get("telephone", ""))
        self._pays.set(p.get("pays", ""))
        self._adresse.set(p.get("adresse", ""))
        notes = p.get("remarques", "")
        if notes:
            self._rem.insert("1.0", notes)

    def _save(self):
        nom    = self._nom.get().strip()
        prenom = self._prenom.get().strip()
        if not nom or not prenom:
            self._error_lbl.configure(text="Last name and first name are required.")
            return

        self.result = {
            "nom":            nom,
            "prenom":         prenom,
            "date_naissance": self._ddn.get().strip(),
            "sexe":           self._sexe.get().strip(),
            "cin":            self._cin.get().strip(),
            "telephone":      self._tel.get().strip(),
            "pays":           self._pays.get().strip(),
            "adresse":        self._adresse.get().strip(),
            "remarques":      self._rem.get("1.0", "end-1c").strip(),
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
