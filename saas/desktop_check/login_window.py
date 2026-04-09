# -*- coding: utf-8 -*-
"""
saas/desktop_check/login_window.py — PISUM professional login window
Uses customtkinter to match the app's dark theme.
"""
import threading
import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# ── Colors (mirrors ui/theme.py) ──────────────────────────────────────────────
BG        = "#0D1117"
SURFACE   = "#161B22"
SURFACE_2 = "#1C2128"
BORDER    = "#30363D"
TEAL      = "#14B8A6"
TEAL_DARK = "#0F8A7A"
TEXT_1    = "#E6EDF3"
TEXT_2    = "#8B949E"
TEXT_3    = "#6E7681"
TEXT_INV  = "#0D1117"
ERROR     = "#F85149"
SUCCESS   = "#3FB950"


class LoginWindow(ctk.CTk):
    """
    Standalone login window shown before the main app launches.
    Calls on_login(email, password) when the user submits.
    Result is stored in self.result: True / False / None (closed)
    """

    def __init__(self, on_login_callback):
        super().__init__()
        self._callback = on_login_callback
        self.result = None

        self.title("PISUM — Connexion")
        self.geometry("420x540")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        # Center on screen
        self.update_idletasks()
        x = (self.winfo_screenwidth()  - 420) // 2
        y = (self.winfo_screenheight() - 540) // 2
        self.geometry(f"420x540+{x}+{y}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()

    def _build(self):
        # ── Card container ────────────────────────────────────────────────────
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=16,
                            border_width=1, border_color=BORDER)
        card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.88)

        # ── Logo / title ──────────────────────────────────────────────────────
        ctk.CTkLabel(card, text="PISUM", font=("Segoe UI", 32, "bold"),
                     text_color=TEAL).pack(pady=(36, 0))
        ctk.CTkLabel(card, text="Compte rendu radiologique assisté par IA",
                     font=("Segoe UI", 11), text_color=TEXT_3).pack(pady=(4, 28))

        # ── Email ─────────────────────────────────────────────────────────────
        ctk.CTkLabel(card, text="Email", font=("Segoe UI", 12),
                     text_color=TEXT_2, anchor="w").pack(fill="x", padx=32)
        self._email = ctk.CTkEntry(
            card, placeholder_text="you@example.com",
            height=42, corner_radius=8,
            fg_color=SURFACE_2, border_color=BORDER, border_width=1,
            text_color=TEXT_1, placeholder_text_color=TEXT_3,
            font=("Segoe UI", 13),
        )
        self._email.pack(fill="x", padx=32, pady=(4, 16))
        self._email.bind("<Return>", lambda e: self._password.focus())

        # ── Password ──────────────────────────────────────────────────────────
        ctk.CTkLabel(card, text="Mot de passe", font=("Segoe UI", 12),
                     text_color=TEXT_2, anchor="w").pack(fill="x", padx=32)
        self._password = ctk.CTkEntry(
            card, placeholder_text="••••••••", show="•",
            height=42, corner_radius=8,
            fg_color=SURFACE_2, border_color=BORDER, border_width=1,
            text_color=TEXT_1, placeholder_text_color=TEXT_3,
            font=("Segoe UI", 13),
        )
        self._password.pack(fill="x", padx=32, pady=(4, 24))
        self._password.bind("<Return>", lambda e: self._submit())

        # ── Error label ───────────────────────────────────────────────────────
        self._error_var = ctk.StringVar(value="")
        self._error_lbl = ctk.CTkLabel(
            card, textvariable=self._error_var,
            font=("Segoe UI", 11), text_color=ERROR,
            wraplength=320,
        )
        self._error_lbl.pack(padx=32, pady=(0, 8))

        # ── Login button ──────────────────────────────────────────────────────
        self._btn = ctk.CTkButton(
            card, text="Se connecter",
            height=44, corner_radius=8,
            fg_color=TEAL, hover_color=TEAL_DARK,
            text_color=TEXT_INV, font=("Segoe UI", 13, "bold"),
            command=self._submit,
        )
        self._btn.pack(fill="x", padx=32, pady=(0, 24))

        # ── Footer link ───────────────────────────────────────────────────────
        ctk.CTkLabel(
            card,
            text="Pas encore de compte ?  →  pisum.app",
            font=("Segoe UI", 10), text_color=TEXT_3,
        ).pack(pady=(0, 28))

    def _submit(self):
        email    = self._email.get().strip()
        password = self._password.get()

        if not email or not password:
            self._set_error("Veuillez remplir tous les champs.")
            return

        self._set_loading(True)
        self._set_error("")

        def run():
            success, message = self._callback(email, password)
            self.after(0, lambda: self._on_result(success, message))

        threading.Thread(target=run, daemon=True).start()

    def _on_result(self, success: bool, message: str):
        self._set_loading(False)
        if success:
            self.result = True
            self.destroy()
        else:
            self._set_error(message)
            self._password.delete(0, "end")
            self._password.focus()

    def _set_loading(self, loading: bool):
        if loading:
            self._btn.configure(text="Connexion…", state="disabled",
                                fg_color="#0A6B61")
            self._email.configure(state="disabled")
            self._password.configure(state="disabled")
        else:
            self._btn.configure(text="Se connecter", state="normal",
                                fg_color=TEAL)
            self._email.configure(state="normal")
            self._password.configure(state="normal")

    def _set_error(self, msg: str):
        self._error_var.set(msg)

    def _on_close(self):
        self.result = None
        self.destroy()


def show_no_access_window(plan_url: str = "https://pisum.app/dashboard.html"):
    """Show a brief 'no active subscription' message window."""
    win = ctk.CTk()
    win.title("PISUM — Accès refusé")
    win.geometry("400x260")
    win.resizable(False, False)
    win.configure(fg_color=BG)

    win.update_idletasks()
    x = (win.winfo_screenwidth()  - 400) // 2
    y = (win.winfo_screenheight() - 260) // 2
    win.geometry(f"400x260+{x}+{y}")

    card = ctk.CTkFrame(win, fg_color=SURFACE, corner_radius=16,
                        border_width=1, border_color=BORDER)
    card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.88)

    ctk.CTkLabel(card, text="⚠", font=("Segoe UI", 36),
                 text_color="#D29922").pack(pady=(28, 4))
    ctk.CTkLabel(card, text="Aucun abonnement actif",
                 font=("Segoe UI", 15, "bold"), text_color=TEXT_1).pack()
    ctk.CTkLabel(
        card,
        text=f"Visitez pisum.app pour activer votre accès.",
        font=("Segoe UI", 11), text_color=TEXT_2, wraplength=300,
    ).pack(pady=(8, 20))
    ctk.CTkButton(
        card, text="Fermer", height=38, corner_radius=8,
        fg_color=SURFACE_2, hover_color="#21262D",
        text_color=TEXT_1, font=("Segoe UI", 12),
        command=win.destroy,
    ).pack(padx=32, pady=(0, 24), fill="x")

    win.mainloop()
