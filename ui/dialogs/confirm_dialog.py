# -*- coding: utf-8 -*-
"""ui/dialogs/confirm_dialog.py — Generic Confirm/Alert Dialog"""
import customtkinter as ctk
from ui.theme import C, F, S, R
from ui.components.widgets import PrimaryButton, SecondaryButton, Divider


class ConfirmDialog(ctk.CTkToplevel):
    def __init__(self, master, title="Confirm", message="",
                 on_confirm=None, confirm_label="Confirm",
                 confirm_color=C.ERROR, **kw):
        super().__init__(master, **kw)
        self.title(title)
        self.geometry("420x200")
        self.resizable(False, False)
        self.configure(fg_color=C.SURFACE)
        self.lift()
        self.focus_force()

        self._on_confirm = on_confirm

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=title,
            font=ctk.CTkFont(*F.HEADING), text_color=C.TEXT_1, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=S.XL, pady=(S.XL, S.SM))

        ctk.CTkLabel(
            self, text=message,
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2,
            anchor="w", justify="left", wraplength=360,
        ).grid(row=1, column=0, sticky="w", padx=S.XL)

        Divider(self).grid(row=2, column=0, sticky="ew",
                           padx=S.XL, pady=(S.LG, S.MD))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="e", padx=S.XL, pady=(0, S.XL))

        SecondaryButton(btn_row, "Cancel", command=self.destroy).pack(
            side="left", padx=(0, S.SM))

        ctk.CTkButton(
            btn_row, text=confirm_label,
            height=38, corner_radius=R.MD,
            fg_color=confirm_color, hover_color=confirm_color,
            text_color=C.TEXT_1, font=ctk.CTkFont(*F.SUBHEADING),
            command=self._confirm,
        ).pack(side="left")

    def _confirm(self):
        self.destroy()
        if self._on_confirm:
            self._on_confirm()


class AlertDialog(ctk.CTkToplevel):
    def __init__(self, master, title="", message="", style="info", **kw):
        super().__init__(master, **kw)
        self.title(title)
        self.geometry("400x180")
        self.resizable(False, False)
        self.configure(fg_color=C.SURFACE)
        self.lift()
        self.focus_force()

        colors = {
            "info":    C.INFO,
            "success": C.SUCCESS,
            "warning": C.WARNING,
            "error":   C.ERROR,
        }
        icons = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✕"}
        color = colors.get(style, C.INFO)
        icon  = icons.get(style, "ℹ")

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=f"{icon}  {title}",
            font=ctk.CTkFont(*F.HEADING), text_color=color, anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=S.XL, pady=(S.XL, S.SM))

        ctk.CTkLabel(
            self, text=message,
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2,
            anchor="w", justify="left", wraplength=340,
        ).grid(row=1, column=0, sticky="w", padx=S.XL)

        PrimaryButton(
            self, "OK",
            command=self.destroy,
        ).grid(row=2, column=0, sticky="e", padx=S.XL, pady=S.XL)
