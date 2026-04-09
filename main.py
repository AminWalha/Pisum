# -*- coding: utf-8 -*-
"""
main.py — PISUM entry point
"""
import sys
import os

# When bundled by PyInstaller, _MEIPASS is the temp dir containing all files.
# Add it to sys.path so that ui/, Comptes_Rendus, etc. are importable.
if getattr(sys, "frozen", False):
    base = sys._MEIPASS
    if base not in sys.path:
        sys.path.insert(0, base)
    # PyArmor runtime lives next to the exe
    exe_dir = os.path.dirname(sys.executable)
    for entry in os.listdir(exe_dir):
        if entry.startswith("pyarmor_runtime"):
            rt = os.path.join(exe_dir, entry)
            if rt not in sys.path:
                sys.path.insert(0, rt)
            break

from saas.desktop_check.subscription_check import check_subscription_access
from ui.app import PisumApp

if __name__ == "__main__":
    if not check_subscription_access():
        sys.exit(0)
    app = PisumApp()
    app.mainloop()
