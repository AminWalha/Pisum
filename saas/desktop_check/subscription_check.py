"""
PISUM Desktop — Subscription Check Module
==========================================
Drop this into your existing desktop app and call check_subscription_access()
before launching the main UI.

Flow:
  1. Try to load a saved JWT token from disk
  2. If not found (or expired), prompt the user for email/password
  3. Authenticate against Supabase to get a fresh JWT
  4. Call the backend /check-access endpoint
  5. Return True/False

Usage in main.py:
    from saas.desktop_check.subscription_check import check_subscription_access
    if not check_subscription_access():
        sys.exit(0)
"""

import os
import json
import time
import tempfile
import getpass
import urllib.request
import urllib.error
from typing import Optional

# ── Config — set these to your actual values ─────────────────────────────────
API_BASE_URL   = "https://pisum-backend.onrender.com"   # FastAPI backend URL
SUPABASE_URL   = "https://lepqbnhrdgfetoysedbq.supabase.co"
SUPABASE_ANON  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxlcHFibmhyZGdmZXRveXNlZGJxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUwNjQ1MDAsImV4cCI6MjA5MDY0MDUwMH0.St12IwG0_RRKzxrbH1QRolCC2OOAaSIsK4PDmlR2Loo"
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_STORE = os.path.join(tempfile.gettempdir(), "pisum_saas_token.json")


# ── Token storage ─────────────────────────────────────────────────────────────

def _save_token(access_token: str, refresh_token: str, expires_at: float):
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }
    with open(TOKEN_STORE, "w") as f:
        json.dump(data, f)


def _load_token() -> Optional[dict]:
    try:
        if os.path.exists(TOKEN_STORE):
            with open(TOKEN_STORE) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _delete_token():
    try:
        if os.path.exists(TOKEN_STORE):
            os.remove(TOKEN_STORE)
    except Exception:
        pass


# ── Supabase Auth ─────────────────────────────────────────────────────────────

def _supabase_login(email: str, password: str) -> Optional[dict]:
    """Authenticate with Supabase, return session dict or None."""
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=password"
    payload = json.dumps({"email": email, "password": password}).encode()
    headers = {
        "apikey": SUPABASE_ANON,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data  # contains access_token, refresh_token, expires_in
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            err = json.loads(body)
            print(f"[PISUM] Login failed: {err.get('error_description', err.get('msg', body))}")
        except Exception:
            print(f"[PISUM] Login failed: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[PISUM] Network error during login: {e}")
        return None


def _supabase_refresh(refresh_token: str) -> Optional[dict]:
    """Refresh an expired access token using the refresh token."""
    url = f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
    payload = json.dumps({"refresh_token": refresh_token}).encode()
    headers = {
        "apikey": SUPABASE_ANON,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


# ── Backend /check-access ─────────────────────────────────────────────────────

def _check_access_api(access_token: str) -> Optional[bool]:
    """Call the FastAPI backend and return True/False, or None on network error."""
    url = f"{API_BASE_URL}/check-access"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("access", False)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None  # token invalid/expired
        print(f"[PISUM] Access check failed: HTTP {e.code}")
        return None
    except Exception as e:
        print(f"[PISUM] Network error during access check: {e}")
        return None


# ── Main public function ──────────────────────────────────────────────────────

def check_subscription_access(
    prompt_fn=None,
    max_attempts: int = 3,
) -> bool:
    """
    Verify the user has an active subscription.

    Args:
        prompt_fn: Optional callable(prompt_str) → str for getting user input
                   (useful for GUI dialogs). Falls back to input()/getpass.
        max_attempts: Number of login retries before giving up.

    Returns:
        True  → subscription is active, allow software launch
        False → no active subscription, block software
    """
    ask = prompt_fn or _default_prompt

    # ── 1. Try cached token ──────────────────────────────────────────────────
    stored = _load_token()
    if stored:
        access_token = stored.get("access_token")
        expires_at   = stored.get("expires_at", 0)

        # Refresh if token is expired (with 60s margin)
        if time.time() >= expires_at - 60:
            print("[PISUM] Token expired, refreshing…")
            refreshed = _supabase_refresh(stored.get("refresh_token", ""))
            if refreshed and "access_token" in refreshed:
                access_token = refreshed["access_token"]
                _save_token(
                    access_token,
                    refreshed.get("refresh_token", stored["refresh_token"]),
                    time.time() + refreshed.get("expires_in", 3600),
                )
            else:
                _delete_token()
                access_token = None

        if access_token:
            result = _check_access_api(access_token)
            if result is True:
                return True
            elif result is False:
                _show_no_subscription_message()
                return False
            # None = network error → fall through to fresh login

    # ── 2. Prompt for credentials ────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("  PISUM — Subscription required")
    print("─" * 50)

    for attempt in range(1, max_attempts + 1):
        email    = ask("Email: ")
        password = ask("Password: ", secret=True)

        if not email or not password:
            continue

        print("[PISUM] Authenticating…")
        session = _supabase_login(email, password)

        if not session or "access_token" not in session:
            remaining = max_attempts - attempt
            if remaining > 0:
                print(f"[PISUM] Login failed. {remaining} attempt(s) remaining.\n")
            continue

        # Save token for next launch
        _save_token(
            session["access_token"],
            session.get("refresh_token", ""),
            time.time() + session.get("expires_in", 3600),
        )

        # Check subscription
        result = _check_access_api(session["access_token"])
        if result is True:
            print("[PISUM] ✓ Access granted. Launching PISUM…\n")
            return True
        else:
            _show_no_subscription_message()
            return False

    print("[PISUM] Too many failed login attempts. Exiting.")
    return False


def _show_no_subscription_message():
    print("\n" + "─" * 50)
    print("  ⚠  No active subscription")
    print("─" * 50)
    print("  Your account does not have an active PISUM subscription.")
    print("  Visit https://your-site.com/dashboard.html to subscribe.")
    print("─" * 50 + "\n")


def _default_prompt(prompt: str, secret: bool = False) -> str:
    if secret:
        return getpass.getpass(prompt)
    return input(prompt)


# ── GUI-friendly version using tkinter ───────────────────────────────────────
# Uncomment and use check_subscription_access(prompt_fn=tk_prompt) if you want
# a dialog box instead of a terminal prompt.

# def tk_prompt(prompt: str, secret: bool = False) -> str:
#     import tkinter as tk
#     from tkinter import simpledialog
#     root = tk.Tk(); root.withdraw()
#     if secret:
#         return simpledialog.askstring("PISUM", prompt, show="*", parent=root) or ""
#     return simpledialog.askstring("PISUM", prompt, parent=root) or ""


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    allowed = check_subscription_access()
    sys.exit(0 if allowed else 1)
