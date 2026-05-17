# -*- coding: utf-8 -*-
"""
saas/desktop_check/splash_screen.py — Splash screen shown during startup network check.
"""
import os
import sys
import tkinter as tk


def _find_splash_image() -> str:
    """Return the splash image path, searching in known locations."""
    candidates = []

    # Project root — primary location (checked in at repo root)
    here = os.path.dirname(__file__)
    root = os.path.normpath(os.path.join(here, "..", ".."))
    candidates.append(os.path.join(root, "Splash Screen.png"))
    candidates.append(os.path.join(root, "Splash.png"))

    # Bundled (PyInstaller): next to the executable
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "Splash Screen.png"))
        candidates.append(os.path.join(exe_dir, "Splash.png"))
        candidates.append(os.path.join(exe_dir, "assets", "Splash Screen.png"))

    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


class SplashScreen(tk.Tk):
    """
    Borderless splash screen displayed while the subscription check runs.
    Call close() from any thread to dismiss it gracefully.
    """

    _WIDTH  = 620
    _HEIGHT = 360

    def __init__(self):
        super().__init__()

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#FFFFFF")

        # Centre on screen
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = (sw - self._WIDTH)  // 2
        y  = (sh - self._HEIGHT) // 2
        self.geometry(f"{self._WIDTH}x{self._HEIGHT}+{x}+{y}")

        self._build_ui()
        self.update()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        splash_path = _find_splash_image()
        if splash_path:
            try:
                from PIL import Image, ImageTk
                img = Image.open(splash_path).convert("RGBA")
                img = img.resize((self._WIDTH, self._HEIGHT), Image.LANCZOS)
                self._photo = ImageTk.PhotoImage(img)
                tk.Label(self, image=self._photo, bg="#FFFFFF", bd=0).pack()
                # Subtle loading label at the bottom
                tk.Label(
                    self,
                    text="Checking account…",
                    font=("Segoe UI", 9),
                    fg="#8B949E",
                    bg="#FFFFFF",
                ).place(relx=0.5, rely=0.97, anchor="s")
                return
            except Exception:
                pass
            # Fallback: tk PhotoImage (PNG only, no resize)
            try:
                self._photo = tk.PhotoImage(file=splash_path)
                tk.Label(self, image=self._photo, bg="#FFFFFF", bd=0).pack()
                return
            except Exception:
                pass

        # Final fallback: text only
        tk.Label(
            self,
            text="PISUM",
            font=("Segoe UI", 36, "bold"),
            fg="#14B8A6",
            bg="#FFFFFF",
        ).pack(expand=True)
        tk.Label(
            self,
            text="Checking account…",
            font=("Segoe UI", 12),
            fg="#8B949E",
            bg="#FFFFFF",
        ).pack()

    # ── Thread-safe close ─────────────────────────────────────────────────────

    def close(self):
        """Destroy the splash from any thread."""
        try:
            self.after(0, self.destroy)
        except Exception:
            pass
