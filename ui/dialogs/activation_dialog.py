# -*- coding: utf-8 -*-
"""ui/dialogs/activation_dialog.py — License Activation Dialog"""
import threading
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import PrimaryButton, SecondaryButton, Divider


class ActivationDialog(ctk.CTkToplevel):
    def __init__(self, master, lm=None, on_activated=None, **kw):
        super().__init__(master, **kw)
        self.title("Activate License")
        self.geometry("460x300")
        self.resizable(False, False)
        self.configure(fg_color=C.SURFACE)
        self.lift()
        self.focus_force()

        self._lm           = lm
        self._on_activated = on_activated

        self.grid_columnconfigure(0, weight=1)

        # ── Header ────────────────────────────────────────────────────
        ctk.CTkLabel(
            self, text="Activate your license",
            font=ctk.CTkFont(*F.TITLE), text_color=C.TEXT_1,
        ).grid(row=0, column=0, pady=(S.XL, S.SM), padx=S.XL, sticky="w")

        ctk.CTkLabel(
            self, text="Enter your PISUM license key to unlock premium features.",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2,
            anchor="w", wraplength=400,
        ).grid(row=1, column=0, padx=S.XL, sticky="w")

        Divider(self).grid(row=2, column=0, sticky="ew", padx=S.XL, pady=S.LG)

        # Key entry
        ctk.CTkLabel(
            self, text="License Key",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
        ).grid(row=3, column=0, padx=S.XL, sticky="w", pady=(0, S.XS))

        self._key_entry = ctk.CTkEntry(
            self,
            placeholder_text="PISUM-XXXX-XXXX-XXXX",
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            text_color=C.TEXT_1, placeholder_text_color=C.TEXT_3,
            font=ctk.CTkFont("Cascadia Code", 14),
            height=40, corner_radius=R.MD,
        )
        self._key_entry.grid(row=4, column=0, padx=S.XL, sticky="ew")

        self._status_lbl = ctk.CTkLabel(
            self, text="",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2,
            anchor="w", wraplength=400,
        )
        self._status_lbl.grid(row=5, column=0, padx=S.XL, pady=(S.SM, 0), sticky="w")

        Divider(self).grid(row=6, column=0, sticky="ew",
                           padx=S.XL, pady=(S.LG, S.MD))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=7, column=0, padx=S.XL, pady=(0, S.XL), sticky="e")

        SecondaryButton(btn_row, "Cancel", command=self.destroy).pack(
            side="left", padx=(0, S.SM))

        self._act_btn = PrimaryButton(
            btn_row, "Activate", icon="◈",
            command=self._activate,
        )
        self._act_btn.pack(side="left")

        self._key_entry.bind("<Return>", lambda _: self._activate())

    def _activate(self):
        key = self._key_entry.get().strip().upper()
        if not key:
            self._status("Please enter a license key.", "warning")
            return

        self._act_btn.configure(state="disabled", text="Activating…")
        self._status("Verifying license…", "info")

        def do():
            try:
                lm = self._lm
                if lm:
                    ok, msg = lm.activate(key)
                else:
                    ok, msg = False, "License manager unavailable."
                self.after(0, lambda: self._done(ok, msg))
            except Exception as e:
                self.after(0, lambda: self._done(False, str(e)))

        threading.Thread(target=do, daemon=True).start()

    def _done(self, ok: bool, msg: str):
        self._act_btn.configure(state="normal", text="◈  Activate")
        if ok:
            self._status(msg, "success")
            if self._lm:
                self._lm.start_background_sync()
            if self._on_activated:
                self._on_activated(True, msg)
            self.after(1500, self.destroy)
        else:
            self._status(msg, "error")
            if self._on_activated:
                self._on_activated(False, msg)

    def _status(self, msg: str, style="info"):
        colors = {
            "info":    C.TEXT_2,
            "success": C.SUCCESS,
            "warning": C.WARNING,
            "error":   C.ERROR,
        }
        self._status_lbl.configure(text=msg, text_color=colors.get(style, C.TEXT_2))
