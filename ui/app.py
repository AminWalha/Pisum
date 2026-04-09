# -*- coding: utf-8 -*-
"""
ui/app.py — PISUM Main Application Window
Worklist-first radiology workflow.
Default page: Worklist → Report Editor → next patient.
"""
import os
import sys
import json
import logging
import threading
import subprocess
import tempfile

import customtkinter as ctk

from ui.theme import C, F, S, R, HEADER_H, STATUS_BAR_H
from ui.sidebar import Sidebar

logger = logging.getLogger(__name__)


class PisumApp(ctk.CTk):
    """
    Main PISUM application window.
    Accepts a pre-built core_state dict (from launcher) or builds one itself.
    """

    def __init__(self, startup_language: str = None, core_state: dict = None):
        super().__init__()

        # ── Frameless window ────────────────────────────────────────────
        self.overrideredirect(True)          # remove native title bar
        self._is_maximized       = False
        self._restore_geo        = None      # geometry before maximise
        self._drag_start_x       = 0
        self._drag_start_y       = 0
        self._intentional_minimize = False   # True only when WE click ⎯

        # Block ALL OS-level close signals — only our ✕ button can close the app.
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        # Alt+F4 → our controlled close (not OS default)
        self.bind("<Alt-F4>", lambda e: (self._on_close() or "break"))

        # Intercept unexpected minimize caused by Alt+Tab / Windows:
        # if overrideredirect window gets Unmapped without our minimize flow,
        # restore it immediately so it doesn't appear "closed".
        self.bind("<Unmap>", self._on_unexpected_unmap)

        # Fix Alt+Tab: tell Windows this is a real app window, not a tool window
        self.after(50, self._fix_windows_appwindow)

        self.configure(fg_color=C.BG)
        self._set_icon()
        self._set_window_size()              # start maximised

        # ── Core state ──────────────────────────────────────────────────
        if core_state:
            self._state = core_state
        else:
            self._state = self._build_core_state(startup_language)

        self._state["on_change_language"] = self._change_language

        # ── Layout: row0=titlebar, row1=content+sidebar, row2=statusbar ─
        self.grid_rowconfigure(0, weight=0)   # title bar
        self.grid_rowconfigure(1, weight=1)   # main area
        self.grid_rowconfigure(2, weight=0)   # status bar
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        # Title bar spans full width
        self._title_bar = self._build_title_bar()
        self._title_bar.grid(row=0, column=0, columnspan=2, sticky="ew")

        self._sidebar = Sidebar(
            self,
            on_navigate=self._navigate,
            lm=self._state.get("lm"),
            translations=self._state.get("translations"),
        )
        self._sidebar.grid(row=1, column=0, sticky="nsew")

        self._content = ctk.CTkFrame(self, fg_color=C.BG, corner_radius=0)
        self._content.grid(row=1, column=1, sticky="nsew")
        self._content.grid_rowconfigure(0, weight=1)
        self._content.grid_columnconfigure(0, weight=1)

        self._status_bar = self._build_status_bar()
        self._status_bar.grid(row=2, column=1, sticky="ew")

        self._current_view = None
        self._current_key  = None

        # ── Start on Dashboard ───────────────────────────────────────────
        self._navigate("dashboard")

        # ── Background tasks ─────────────────────────────────────────────
        if self._state.get("lm"):
            threading.Thread(
                target=self._state["lm"].start_background_sync,
                daemon=True,
            ).start()

        self._schedule_pacs_poll()
        self._check_updates_async()

    # ══════════════════════════════════════════════════════════════════════
    # CORE STATE BOOTSTRAP
    # ══════════════════════════════════════════════════════════════════════

    def _build_core_state(self, startup_language=None) -> dict:
        state: dict = {}

        try:
            from shared_config import get_selected_language
            lang = startup_language or get_selected_language() or "Français"
        except Exception:
            lang = startup_language or "Français"
        state["current_language"] = lang

        try:
            from Comptes_Rendus import AppConstants
            state["current_language_folder"] = AppConstants.AVAILABLE_LANGUAGES.get(
                lang, "Francais")
        except Exception:
            state["current_language_folder"] = "Francais"

        try:
            from Comptes_Rendus import TRANSLATIONS
            state["translations"] = TRANSLATIONS.get(lang, TRANSLATIONS.get("English", {}))
            state["translations_map"] = TRANSLATIONS
        except Exception:
            state["translations"] = {}
            state["translations_map"] = {}

        try:
            from pisum_license_manager import LicenseManager
            lm = LicenseManager()
            lm.start_background_sync()
            state["lm"] = lm
        except Exception as e:
            logger.warning(f"LicenseManager unavailable: {e}")
            state["lm"] = None

        try:
            from config_manager import ConfigManager
            state["config_manager"] = ConfigManager()
        except Exception:
            state["config_manager"] = None

        try:
            from custom_formulas_db import CustomFormulasDB
            state["custom_formulas_db"] = CustomFormulasDB()
        except Exception as e:
            logger.warning(f"CustomFormulasDB unavailable: {e}")
            state["custom_formulas_db"] = None

        lang_folder = state["current_language_folder"]
        user_plan   = state["lm"].get_plan_name() if state.get("lm") else "free"
        try:
            from Comptes_Rendus import ResourceManager
            state["data"] = ResourceManager.load_excel_data(lang_folder,
                                                            user_plan=user_plan)
        except Exception as e:
            logger.error(f"Data load error: {e}")
            state["data"] = {}

        try:
            from Comptes_Rendus import _PACS_STATE
            state["pacs_state"] = _PACS_STATE
        except Exception:
            state["pacs_state"] = {"current_patient": None, "current_examen": None}

        # Worklist cache (populated by WorklistView)
        state["worklist_items"] = []

        return state

    # ══════════════════════════════════════════════════════════════════════
    # NAVIGATION
    # ══════════════════════════════════════════════════════════════════════

    def _navigate(self, key: str):
        if key == self._current_key:
            return

        if self._current_view:
            self._current_view.destroy()
            self._current_view = None

        self._current_key = key
        # "report_editor" is not in sidebar nav — keep sidebar on "worklist"
        sidebar_key = key if key in ("dashboard", "worklist", "reports", "templates",
                                     "dictation", "license", "settings") else "worklist"
        self._sidebar.navigate_to(sidebar_key)

        view = self._build_view(key)
        if view:
            view.grid(row=0, column=0, sticky="nsew")
            self._current_view = view

        self._update_status(key)

    def _build_view(self, key: str) -> ctk.CTkFrame | None:
        s = self._state

        # ── Dashboard ───────────────────────────────────────────────────
        if key == "dashboard":
            from ui.views.dashboard_view import DashboardView
            return DashboardView(self._content, core_state=s,
                                 on_navigate=self._navigate)

        # ── Worklist ────────────────────────────────────────────────────
        if key == "worklist":
            from ui.views.worklist_view import WorklistView
            return WorklistView(
                self._content,
                core_state=s,
                on_open_exam=self._open_report,
            )

        # ── Report editor (opened from worklist row click) ───────────────
        if key == "report_editor":
            item = s.get("active_exam_item")
            if not item:
                return self._build_view("worklist")
            from ui.views.report_editor_view import ReportEditorView
            return ReportEditorView(
                self._content,
                core_state=s,
                item=item,
                on_back=self._back_to_worklist,
                on_get_next=self._get_next_exam,
                on_open_word=self._generate_word,
                on_print=self._do_print,
            )

        # ── Legacy Reports (template selector only) ──────────────────────
        if key == "reports":
            try:
                from ui.views.report_view import ReportView
                return ReportView(
                    self._content,
                    core_state=s,
                    on_navigate=self._navigate,
                    on_open_word=self._generate_word,
                    on_print=self._do_print,
                    on_open_pacs=None,
                )
            except Exception as e:
                logger.warning(f"ReportView unavailable: {e}")
                return None

        # ── Templates ────────────────────────────────────────────────────
        if key == "templates":
            from ui.views.templates_view import TemplatesView
            return TemplatesView(self._content, core_state=s,
                                 on_navigate=self._navigate)

        # ── Dictation ────────────────────────────────────────────────────
        if key == "dictation":
            self._navigate("reports")
            return None

        # ── License ──────────────────────────────────────────────────────
        if key == "license":
            from ui.views.license_view import LicenseView
            return LicenseView(self._content, core_state=s,
                               on_navigate=self._navigate)

        # ── Settings ─────────────────────────────────────────────────────
        if key == "settings":
            from ui.views.settings_view import SettingsView
            return SettingsView(self._content, core_state=s,
                                on_navigate=self._navigate)

        return None

    # ══════════════════════════════════════════════════════════════════════
    # WORKLIST → REPORT EDITOR FLOW
    # ══════════════════════════════════════════════════════════════════════

    def _open_report(self, item: dict):
        """Called by WorklistView when user clicks a row."""
        lm = self._state.get("lm")
        if lm and not lm.can_use_feature("pacs_ris"):
            self._navigate("settings")
            return
        self._state["active_exam_item"] = item
        self._navigate("report_editor")

    def _back_to_worklist(self):
        """Called by ReportEditorView ← back button."""
        self._state.pop("active_exam_item", None)
        self._navigate("worklist")
        # Refresh the worklist to reflect status changes
        if self._current_view and hasattr(self._current_view, "refresh"):
            self._current_view.refresh()

    def _get_next_exam(self, current_uuid: str) -> dict | None:
        """
        Returns the next pending/in-progress exam item from the worklist cache.
        Called by ReportEditorView "Next Patient →" button.
        """
        items = self._state.get("worklist_items", [])
        actionable = [x for x in items
                      if x.get("statut") in ("En attente", "En cours")]
        for i, item in enumerate(actionable):
            if item.get("examen_uuid") == current_uuid:
                if i + 1 < len(actionable):
                    next_item = actionable[i + 1]
                    self._state["active_exam_item"] = next_item
                    return next_item
        return None

    # ══════════════════════════════════════════════════════════════════════
    # STATUS BAR
    # ══════════════════════════════════════════════════════════════════════

    def _build_status_bar(self) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(
            self, fg_color=C.SIDEBAR, corner_radius=0,
            height=STATUS_BAR_H,
            border_color=C.BORDER, border_width=0,
        )
        bar.pack_propagate(False)

        self._status_lbl = ctk.CTkLabel(
            bar, text="Ready",
            font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3, anchor="w",
        )
        self._status_lbl.pack(side="left", padx=S.LG)

        lm = self._state.get("lm")
        if lm:
            plan = lm.get_plan_name().upper()
            name = lm.user_name or ""
            desc = f"{plan}  •  {name}" if name else plan
            ctk.CTkLabel(
                bar, text=desc,
                font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
            ).pack(side="right", padx=S.LG)

        try:
            vp = os.path.join(os.path.dirname(os.path.dirname(__file__)), "version.json")
            with open(vp) as f:
                ver = json.load(f).get("version", "")
            ctk.CTkLabel(
                bar, text=f"v{ver}",
                font=ctk.CTkFont(*F.CAPTION), text_color=C.TEXT_3,
            ).pack(side="right", padx=(0, S.SM))
        except Exception:
            pass

        return bar

    def _update_status(self, key: str):
        labels = {
            "worklist":      "Worklist",
            "report_editor": "Report Editor",
            "reports":       "Report Editor",
            "templates":     "Templates",
            "dictation":     "Dictation",
            "settings":      "Settings",
        }
        self._status_lbl.configure(text=labels.get(key, ""))

    # ══════════════════════════════════════════════════════════════════════
    # WORD GENERATION
    # ══════════════════════════════════════════════════════════════════════

    def _generate_word(self, payload: dict):
        try:
            from Comptes_Rendus import ProfessionalWordGenerator
            lang = payload.get("language", "Français")
            t    = self._state.get("translations", {})
            sections_to_format = t.get("sections", [])

            # create_professional_report saves the file itself and returns the path
            path = ProfessionalWordGenerator.create_professional_report(
                formula=payload.get("formula", ""),
                etablissement=payload.get("etablissement", ""),
                medecin=payload.get("medecin", "Dr."),
                modality=payload.get("modality", ""),
                exam_type=payload.get("exam_type", ""),
                formula_name=payload.get("formula_name", ""),
                sections_to_format=sections_to_format,
                language=lang,
                patient_data=payload.get("patient_data"),
                examen_data=payload.get("examen_data"),
            )

            if not path:
                raise RuntimeError("Document generation failed")

            # File is already saved and opened by create_professional_report
            self._try_save_pacs(payload, path)

        except Exception as e:
            logger.error(f"Word generation error: {e}", exc_info=True)
            self.after(0, lambda err=e: self._show_alert("Export Error", str(err), "error"))

    def _try_save_pacs(self, payload: dict, docx_path: str = None):
        item = self._state.get("active_exam_item")
        if not item:
            return
        examen_uuid = item.get("examen_uuid")
        if not examen_uuid:
            return
        try:
            from pacs_ris_db import save_cr_for_current_exam
            save_cr_for_current_exam(
                examen_uuid=examen_uuid,
                contenu=payload.get("formula", ""),
            )
        except Exception as e:
            logger.warning(f"PACS save error: {e}")

    def _do_print(self, payload: dict):
        """
        Open the Print / Save PDF dialog.
        PDF is generated directly from payload by PrintDialog (ReportLab).
        No Word document is generated or opened.
        """
        self._open_print_dialog(payload, docx_path=None)

    def _open_print_dialog(self, payload: dict, docx_path: str):
        from ui.dialogs.print_dialog import PrintDialog
        lm   = self._state.get("lm")
        plan = lm.get_plan_name() if lm else "free"
        logger.info("PrintDialog plan: %r", plan)
        PrintDialog(self, payload=payload, docx_path=docx_path, plan=plan)

    @staticmethod
    def _open_file(path: str):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            logger.error(f"Open file error: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # PACS SIGNAL POLLING
    # ══════════════════════════════════════════════════════════════════════

    def _schedule_pacs_poll(self):
        self._poll_pacs_signal()
        self.after(1000, self._schedule_pacs_poll)

    def _poll_pacs_signal(self):
        try:
            signal_file = os.path.join(
                os.path.expanduser("~"), ".pisum_data", "pacs_signal.json"
            )
            if not os.path.exists(signal_file):
                return
            with open(signal_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.remove(signal_file)

            pacs = self._state.get("pacs_state", {})
            if "patient" in data:
                pacs["current_patient"] = data["patient"]
            if "examen" in data:
                pacs["current_examen"] = data["examen"]
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════
    # LANGUAGE CHANGE
    # ══════════════════════════════════════════════════════════════════════

    def _change_language(self, lang: str):
        s = self._state
        try:
            from Comptes_Rendus import AppConstants, TRANSLATIONS
            from shared_config import save_selected_language
            save_selected_language(lang)
            lang_folder = AppConstants.AVAILABLE_LANGUAGES.get(lang, "Francais")
            s["current_language"]        = lang
            s["current_language_folder"] = lang_folder
            s["translations"]            = TRANSLATIONS.get(lang, TRANSLATIONS.get("English", {}))
            s["translations_map"]        = TRANSLATIONS
            user_plan = s["lm"].get_plan_name() if s.get("lm") else "free"
            from Comptes_Rendus import ResourceManager
            s["data"] = ResourceManager.load_excel_data(lang_folder, user_plan=user_plan)
        except Exception as e:
            logger.error(f"Language change error: {e}")
            return

        if self._current_key:
            self._navigate(self._current_key)

    # ══════════════════════════════════════════════════════════════════════
    # UPDATE CHECK
    # ══════════════════════════════════════════════════════════════════════

    def _check_updates_async(self):
        threading.Thread(target=self._do_check_updates, daemon=True).start()

    def _do_check_updates(self):
        try:
            from Comptes_Rendus import UpdateChecker, CURRENT_VERSION
            checker     = UpdateChecker()
            update_info = checker.check_for_updates()
            if update_info and update_info.get("update_available"):
                self.after(0, lambda: self._show_update_banner(update_info))
        except Exception:
            pass

    def _show_update_banner(self, info: dict):
        new_ver = info.get("version", "")
        url     = info.get("download_url", "https://pisum.app")
        import webbrowser

        banner = ctk.CTkFrame(self._content, fg_color=C.TEAL_DIM, corner_radius=0)
        banner.place(relx=0, rely=1.0, anchor="sw", relwidth=1.0)
        banner.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            banner,
            text=f"  Update available: v{new_ver}  —  Download the latest version",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEAL, anchor="w",
        ).grid(row=0, column=0, padx=S.LG, pady=S.SM, sticky="w")

        ctk.CTkButton(
            banner, text="Download",
            height=26, width=100,
            fg_color=C.TEAL, hover_color=C.TEAL_DARK,
            text_color=C.TEXT_INV, font=ctk.CTkFont(*F.CAPTION),
            corner_radius=R.MD,
            command=lambda: (webbrowser.open(url), banner.destroy()),
        ).grid(row=0, column=1, padx=S.SM, pady=S.SM)

        ctk.CTkButton(
            banner, text="×",
            width=28, height=28,
            fg_color="transparent", hover_color=C.TEAL_DIM,
            text_color=C.TEAL, font=ctk.CTkFont(*F.BODY),
            command=banner.destroy,
        ).grid(row=0, column=2, padx=(0, S.SM))

    # ══════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════

    def _set_window_size(self):
        self.minsize(1100, 700)
        self.after(0, self._maximize)

    # ══════════════════════════════════════════════════════════════════════
    # CUSTOM TITLE BAR
    # ══════════════════════════════════════════════════════════════════════

    def _build_title_bar(self) -> ctk.CTkFrame:
        BAR_H  = 38
        BG     = "#010409"        # deepest dark
        TEAL   = C.TEAL

        bar = ctk.CTkFrame(self, height=BAR_H, fg_color=BG, corner_radius=0)
        bar.pack_propagate(False)
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        # ── Logo ────────────────────────────────────────────────────────
        logo = ctk.CTkLabel(
            bar, text="✦  PISUM",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            text_color=TEAL, anchor="w",
        )
        logo.grid(row=0, column=0, padx=(14, 0), pady=0, sticky="w")

        # ── Subtitle ────────────────────────────────────────────────────
        sub = ctk.CTkLabel(
            bar, text="Radiology Worklist & Reporting",
            font=ctk.CTkFont("Segoe UI", 11),
            text_color=C.TEXT_3, anchor="w",
        )
        sub.grid(row=0, column=1, padx=(10, 0), pady=0, sticky="w")

        # ── Window control buttons ───────────────────────────────────────
        btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
        btn_frame.grid(row=0, column=2, sticky="e")

        def _win_btn(parent, text, hover, cmd):
            b = ctk.CTkButton(
                parent, text=text,
                width=46, height=BAR_H,
                fg_color="transparent",
                hover_color=hover,
                text_color=C.TEXT_2,
                font=ctk.CTkFont("Segoe UI", 14),
                corner_radius=0,
                command=cmd,
            )
            b.pack(side="left")
            return b

        _win_btn(btn_frame, "⎯",  C.SURFACE_3,  self._minimize)
        self._max_btn = _win_btn(btn_frame, "⬜",  C.SURFACE_3,  self._toggle_maximize)
        _win_btn(btn_frame, "✕",  "#C42B1C",    self._on_close)

        # ── Drag to move ────────────────────────────────────────────────
        for widget in (bar, logo, sub):
            widget.bind("<ButtonPress-1>",   self._drag_start)
            widget.bind("<B1-Motion>",        self._drag_motion)
            widget.bind("<Double-Button-1>",  lambda e: self._toggle_maximize())

        return bar

    def _drag_start(self, event):
        if self._is_maximized:
            return
        self._drag_start_x = event.x_root - self.winfo_x()
        self._drag_start_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        if self._is_maximized:
            return
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self.geometry(f"+{x}+{y}")

    def _minimize(self):
        """Minimise with overrideredirect=True workaround (Windows)."""
        self._intentional_minimize = True
        # Guard: unbind <Map> in case it's still active from a previous minimize
        try:
            self.unbind("<Map>")
        except Exception:
            pass
        # 1. Restore native frame so Windows can properly iconify
        self.overrideredirect(False)
        self.update_idletasks()
        # 2. Minimise to taskbar
        self.iconify()
        # 3. Bind restore AFTER a short delay to skip any spurious <Map>
        #    that fires when overrideredirect changes
        self.after(300, lambda: self.bind("<Map>", self._on_restore_from_taskbar))

    def _on_restore_from_taskbar(self, event=None):
        self.unbind("<Map>")
        self._intentional_minimize = False
        self.overrideredirect(True)
        self.update_idletasks()
        if self._is_maximized:
            self._maximize()
        self.after(50, self._fix_windows_appwindow)

    def _on_unexpected_unmap(self, event):
        if event.widget == self and not self._intentional_minimize:
            self.deiconify()

    def _toggle_maximize(self):
        if self._is_maximized:
            self._restore()
        else:
            self._maximize()

    def _maximize(self):
        self._restore_geo = self.geometry()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")
        self._is_maximized = True
        if hasattr(self, "_max_btn"):
            self._max_btn.configure(text="❐")

    def _restore(self):
        geo = self._restore_geo or "1400x900+60+40"
        self.geometry(geo)
        self._is_maximized = False
        if hasattr(self, "_max_btn"):
            self._max_btn.configure(text="⬜")

    def _fix_windows_appwindow(self):
        """
        With overrideredirect(True) Windows treats the window as a WS_EX_TOOLWINDOW
        (no taskbar button, not in Alt+Tab) and can close it on focus loss.
        This adds WS_EX_APPWINDOW so Windows treats it as a normal application window,
        which fixes the Alt+Tab crash.
        Must be called AFTER the window is mapped (use after(50, ...)).
        """
        try:
            import ctypes
            hwnd = self.winfo_id()
            GWL_EXSTYLE      = -20
            WS_EX_APPWINDOW  = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            # Refresh so the taskbar picks up the change
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0020  # SWP_NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
            )
        except Exception:
            pass

    def _set_icon(self):
        try:
            ico = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pisum.ico")
            if os.path.exists(ico):
                self.iconbitmap(ico)
        except Exception:
            pass

    def _show_alert(self, title: str, message: str, style="info"):
        from ui.dialogs.confirm_dialog import AlertDialog
        AlertDialog(self, title=title, message=message, style=style)

    def _on_close(self):
        try:
            cfg = self._state.get("config_manager")
            if cfg and self._current_view:
                for attr in ("_etab", "_med"):
                    widget = getattr(self._current_view, attr, None)
                    if widget:
                        key = "etablissement" if attr == "_etab" else "medecin"
                        cfg.set(key, widget.get())
        except Exception:
            pass
        self.destroy()
        sys.exit(0)


# ── Standalone entry point ─────────────────────────────────────────────────
def run_ctk_app(startup_language: str = None, core_state: dict = None):
    import ui.theme
    app = PisumApp(startup_language=startup_language, core_state=core_state)
    app.mainloop()
