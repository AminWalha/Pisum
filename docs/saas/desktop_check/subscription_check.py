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
import urllib.request
import urllib.error
from typing import Optional

# ── Config — set these to your actual values ─────────────────────────────────
API_BASE_URL   = "https://pisum-backend.onrender.com"   # FastAPI backend URL
SUPABASE_URL   = "https://lepqbnhrdgfetoysedbq.supabase.co"
SUPABASE_ANON  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxlcHFibmhyZGdmZXRveXNlZGJxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUwNjQ1MDAsImV4cCI6MjA5MDY0MDUwMH0.St12IwG0_RRKzxrbH1QRolCC2OOAaSIsK4PDmlR2Loo"
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_STORE  = os.path.join(tempfile.gettempdir(), "pisum_saas_token.json")
AVATAR_CACHE = os.path.join(tempfile.gettempdir(), "pisum_avatar.jpg")


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

def _check_access_api(access_token: str, max_retries: int = 3, timeout: int = 60) -> Optional[dict]:
    """Call the FastAPI backend and return the full response dict, or None on network error.
    Returns dict with keys: access (bool), plan (str), features (dict).
    Retries up to max_retries times to handle Render.com free-tier cold starts (30-60 s wake-up).
    """
    url = f"{API_BASE_URL}/check-access"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return None  # token invalid/expired — don't retry
            print(f"[PISUM] Access check failed: HTTP {e.code}")
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"[PISUM] Server waking up, retrying ({attempt + 1}/{max_retries})… ({e})")
                time.sleep(20)
            else:
                print(f"[PISUM] Network error during access check: {e}")
    return None


# ── Main public function ──────────────────────────────────────────────────────

PLAN_STORE = os.path.join(tempfile.gettempdir(), "pisum_saas_plan.json")


def _save_plan(plan_info: dict):
    """Persist the plan/features dict so PisumApp can read it without a second API call."""
    with open(PLAN_STORE, "w") as f:
        json.dump(plan_info, f)


def _decode_jwt_email(token: str) -> str:
    """Extract email from a JWT payload without verifying signature."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return ""
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode())
        return data.get("email", "")
    except Exception:
        return ""


def _decode_jwt_sub(token: str) -> str:
    """Extract user_id (sub) from a JWT payload."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return ""
        payload = parts[1]
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode())
        return data.get("sub", "")
    except Exception:
        return ""


def _fetch_profile(access_token: str) -> dict:
    """Fetch the user's profile row from Supabase (avatar_url, specialty, etc.)."""
    user_id = _decode_jwt_sub(access_token)
    if not user_id:
        return {}
    url = (f"{SUPABASE_URL}/rest/v1/profiles"
           f"?user_id=eq.{user_id}"
           f"&select=full_name,specialty,organization,city,country,avatar_url"
           f"&limit=1")
    headers = {
        "apikey":        SUPABASE_ANON,
        "Authorization": f"Bearer {access_token}",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            return data[0] if data else {}
    except Exception:
        return {}


def _fetch_and_save_profile(access_token: str):
    """Fetch profile from Supabase and merge into the local plan store."""
    profile = _fetch_profile(access_token)
    if not profile:
        return
    info = load_plan_info()
    info["avatar_url"]   = profile.get("avatar_url",   "") or ""
    info["specialty"]    = profile.get("specialty",    "") or ""
    info["organization"] = profile.get("organization", "") or ""
    if profile.get("full_name"):
        info["name"] = profile["full_name"]
    _save_plan(info)

    # Pre-download avatar so it's available offline
    avatar_url = info["avatar_url"]
    if avatar_url:
        _download_avatar(avatar_url)


def _fetch_and_save_ai_enhancer_usage(access_token: str) -> None:
    """Fetch current AI enhancer usage from the backend and persist to PLAN_STORE.
    This makes Supabase the authoritative source so the local counter stays in sync
    across devices and after reinstalls."""
    url = f"{API_BASE_URL}/check-feature?feature=ai_enhancer"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        info = load_plan_info()
        info["ai_enhancer_uses"]     = data.get("used", 0)
        info["ai_enhancer_limit"]    = data.get("limit", 0)
        info["ai_enhancer_reset_at"] = data.get("reset_at")
        _save_plan(info)
    except Exception:
        pass  # best-effort; local cache remains as fallback


def _fetch_and_save_reports_usage(access_token: str) -> None:
    """Fetch current monthly report usage from the backend and persist to PLAN_STORE."""
    url = f"{API_BASE_URL}/check-feature?feature=cr"
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        info = load_plan_info()
        info["reports_uses"]     = data.get("used", 0)
        info["reports_limit"]    = data.get("limit", 0)
        info["reports_reset_at"] = data.get("reset_at")
        _save_plan(info)
    except Exception:
        pass  # best-effort; local cache remains as fallback


def _download_avatar(url: str) -> str:
    """Download avatar image to disk cache. Returns local path or ''."""
    if not url:
        return ""
    try:
        clean_url = url.split("?")[0]   # strip cache-buster for request
        req = urllib.request.Request(clean_url, headers={"User-Agent": "PISUM/2"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        with open(AVATAR_CACHE, "wb") as f:
            f.write(data)
        # Store which URL is cached so we can detect changes
        meta = {"url": url}
        with open(AVATAR_CACHE + ".meta", "w") as f:
            json.dump(meta, f)
        return AVATAR_CACHE
    except Exception:
        return ""


def get_avatar_ctk_image(size: int = 40):
    """
    Return a circular CTkImage for the user's avatar, or None if unavailable.
    Requires Pillow.  Safe to call even when PIL is not installed.
    """
    try:
        from PIL import Image, ImageDraw
        import customtkinter as ctk
    except ImportError:
        return None

    plan = load_plan_info()
    url  = plan.get("avatar_url", "")
    if not url:
        return None

    # Validate disk cache matches current URL
    cached_url = ""
    meta_path  = AVATAR_CACHE + ".meta"
    try:
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                cached_url = json.load(f).get("url", "")
    except Exception:
        pass

    if not (os.path.exists(AVATAR_CACHE) and cached_url == url):
        path = _download_avatar(url)
        if not path:
            return None

    try:
        img = Image.open(AVATAR_CACHE).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)

        # Apply circular mask
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        img.putalpha(mask)

        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return None


def update_user_name(name: str) -> tuple:
    """Save display name locally and patch Supabase user_metadata."""
    info = load_plan_info()
    info["name"] = name.strip()
    _save_plan(info)

    stored = _load_token()
    if not stored or not stored.get("access_token"):
        return True, ""

    url = f"{SUPABASE_URL}/auth/v1/user"
    payload = json.dumps({"data": {"full_name": name.strip()}}).encode()
    headers = {
        "apikey": SUPABASE_ANON,
        "Authorization": f"Bearer {stored['access_token']}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass  # local save succeeded; Supabase update is best-effort
    return True, ""


def load_plan_info() -> dict:
    """Return the last saved plan info. Falls back to free plan defaults."""
    try:
        if os.path.exists(PLAN_STORE):
            with open(PLAN_STORE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"plan": "free", "features": {}, "email": "", "name": ""}


def check_cached_token_access() -> dict:
    """
    Silent (no-GUI) token check. Called from a background thread during splash.

    Returns a dict with keys:
        status : "ok" | "no_access" | "expired" | "no_token" | "network_error"
        plan   : plan info dict (present when status == "ok")
        token  : valid access token (present when status == "ok")
    """
    stored = _load_token()
    if not stored:
        return {"status": "no_token"}

    access_token = stored.get("access_token")
    expires_at   = stored.get("expires_at", 0)

    if time.time() >= expires_at - 60:
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
            return {"status": "expired"}

    result = _check_access_api(access_token, max_retries=1, timeout=12)
    if result is None:
        return {"status": "network_error"}
    if not result.get("access"):
        return {"status": "no_access"}

    plan_info = {
        "plan":     result.get("plan", "free"),
        "features": result.get("features", {}),
        "email":    _decode_jwt_email(access_token),
        "name":     result.get("name", ""),
    }
    _save_plan(plan_info)
    _fetch_and_save_profile(access_token)
    _fetch_and_save_ai_enhancer_usage(access_token)
    _fetch_and_save_reports_usage(access_token)
    return {"status": "ok", "plan": plan_info, "token": access_token}


def check_subscription_access() -> bool:
    """
    Verify the user has an active subscription.
    Shows a professional GUI login window if credentials are needed.

    Side-effect: saves plan + features to PLAN_STORE so the app can read them
    via load_plan_info() without an extra network call.

    Returns:
        True  → subscription is active, allow software launch
        False → no active subscription or user closed the window
    """
    # ── 1. Try cached token ──────────────────────────────────────────────────
    stored = _load_token()
    if stored:
        access_token = stored.get("access_token")
        expires_at   = stored.get("expires_at", 0)

        if time.time() >= expires_at - 60:
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
            if result is not None:
                if result.get("access"):
                    _save_plan({"plan": result.get("plan", "free"),
                                "features": result.get("features", {}),
                                "email": _decode_jwt_email(access_token),
                                "name": result.get("name", "")})
                    _fetch_and_save_profile(access_token)
                    _fetch_and_save_ai_enhancer_usage(access_token)
                    _fetch_and_save_reports_usage(access_token)
                    return True
                else:
                    from saas.desktop_check.login_window import show_no_access_window
                    show_no_access_window()
                    return False
            # None = network error → show login window

    # ── 2. Show GUI login window ─────────────────────────────────────────────
    from saas.desktop_check.login_window import LoginWindow, show_no_access_window

    outcome = {"value": None, "plan_info": {}}

    def on_login(email: str, password: str):
        session = _supabase_login(email, password)
        if not session or "access_token" not in session:
            return False, "Incorrect email or password."

        _save_token(
            session["access_token"],
            session.get("refresh_token", ""),
            time.time() + session.get("expires_in", 3600),
        )

        user_email = session.get("user", {}).get("email", email)
        user_name  = session.get("user", {}).get("user_metadata", {}).get("full_name", "")

        result = _check_access_api(session["access_token"])
        if result is not None and result.get("access"):
            outcome["value"] = True
            outcome["plan_info"] = {"plan": result.get("plan", "free"),
                                    "features": result.get("features", {}),
                                    "email": user_email,
                                    "name": user_name}
            _fetch_and_save_profile(session["access_token"])
            _fetch_and_save_ai_enhancer_usage(session["access_token"])
            _fetch_and_save_reports_usage(session["access_token"])
            return True, ""
        else:
            outcome["value"] = False
            return False, "No active subscription for this account."

    win = LoginWindow(on_login_callback=on_login)
    win.mainloop()

    if win.result is True:
        _save_plan(outcome.get("plan_info", {}))
        return True

    if outcome["value"] is False:
        show_no_access_window()

    return False


# ── Atomic Supabase RPC helper ────────────────────────────────────────────────

def _get_valid_token() -> str:
    """
    Load and, if needed, refresh the stored JWT.
    Returns a valid access_token string, or '' on failure.
    """
    stored = _load_token()
    if not stored:
        return ""
    access_token = stored.get("access_token", "")
    expires_at   = stored.get("expires_at", 0)
    if not access_token or time.time() >= expires_at - 60:
        refreshed = _supabase_refresh(stored.get("refresh_token", ""))
        if refreshed and "access_token" in refreshed:
            access_token = refreshed["access_token"]
            _save_token(
                access_token,
                refreshed.get("refresh_token", stored.get("refresh_token", "")),
                time.time() + refreshed.get("expires_in", 3600),
            )
        else:
            return ""
    return access_token


def _supabase_rpc(rpc_name: str, access_token: str, params: dict) -> bool:
    """
    Call a Supabase SECURITY DEFINER function via the PostgREST RPC endpoint.
    This is atomic and does not go through the Render.com backend, so it works
    even when the backend is sleeping and has no cold-start latency.
    Returns True on success (HTTP 200/204), False otherwise.
    """
    user_id = _decode_jwt_sub(access_token)
    if not user_id:
        return False
    payload = json.dumps({**params, "p_user_id": user_id}).encode("utf-8")
    headers = {
        "apikey":        SUPABASE_ANON,
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }
    url = f"{SUPABASE_URL}/rest/v1/rpc/{rpc_name}"
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


# ── Remote usage counters (called from UI threads — silent fail) ──────────────

def call_use_ai_enhancer() -> bool:
    """
    Atomically increment the AI Enhancer counter in Supabase via RPC.
    Uses the user's own JWT so no service-role key is exposed.
    Falls back to the FastAPI backend if the RPC call fails.
    Silent fail — never blocks the UI.  Returns True on success.
    """
    token = _get_valid_token()
    if not token:
        return False

    # Primary: direct atomic Supabase RPC (no cold-start, no race condition)
    if _supabase_rpc("increment_ai_enhancer_uses", token, {}):
        return True

    # Fallback: FastAPI backend (kept for server-side limit enforcement)
    url = f"{API_BASE_URL}/use-ai-enhancer"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception:
        return False


def call_use_report() -> bool:
    """
    Atomically increment the report counter in Supabase via RPC.
    Falls back to the FastAPI backend on failure.
    Silent fail — never blocks the UI.  Returns True on success.
    """
    token = _get_valid_token()
    if not token:
        return False

    # Primary: direct atomic Supabase RPC
    if _supabase_rpc("increment_cr_uses", token, {}):
        return True

    # Fallback: FastAPI backend
    url = f"{API_BASE_URL}/use-report"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception:
        return False


def call_use_translation() -> bool:
    """
    Atomically increment the translation counter in Supabase via RPC.
    Called after each successful AI translation in the report editor.
    Silent fail — never blocks the UI.  Returns True on success.
    """
    token = _get_valid_token()
    if not token:
        return False
    return _supabase_rpc("increment_translation_uses", token, {})


# ── CLI test ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    allowed = check_subscription_access()
    sys.exit(0 if allowed else 1)
