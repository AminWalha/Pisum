# -*- coding: utf-8 -*-
"""
PISUM — LicenseManager v3.0
Firebase-backed, Feature-gating engine, offline-capable, local usage tracking.

Integration in your app:
    from pisum_license_manager import LicenseManager
    lm = LicenseManager()
    if not lm.load_or_activate():
        # show activation dialog
        pass
"""

import os
import sys
import json
import uuid
import time
import hmac
import hashlib
import platform
import datetime
import tempfile
import threading
import getpass
import logging
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# ❶  SUPABASE CONFIGURATION
# ─────────────────────────────────────────────────────────
SUPABASE_URL = "https://lepqbnhrdgfetoysedbq.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxlcHFibmhyZGdmZXRveXNlZGJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTA2NDUwMCwiZXhwIjoyMDkwNjQwNTAwfQ.uUnRftUM7XBbm-JBBSHD2Qjivx3ElAGGL8EgWtdWlgo"

# HMAC signing key for the local license cache (obfuscated)
_CACHE_SIGN_KEY = bytes([
    0x50, 0x49, 0x53, 0x55, 0x4d, 0x5f, 0x4c, 0x49,
    0x43, 0x5f, 0x53, 0x49, 0x47, 0x4e, 0x5f, 0x76,
    0x32, 0x5f, 0x73, 0x33, 0x63, 0x75, 0x72, 0x33,
])

# ─────────────────────────────────────────────────────────
# ❷  TRACKED FEATURES
# ─────────────────────────────────────────────────────────
TRACKED_FEATURES = [
    "ai_dictation_minutes_per_day",
    "max_patients_per_day",
    "max_reports_per_day"
]

# ─────────────────────────────────────────────────────────
# ❸  STORAGE PATHS
# ─────────────────────────────────────────────────────────
_TMP = tempfile.gettempdir()
LICENSE_STORE   = os.path.join(_TMP, "pisum_lic_v3.json")
TIMEGUARD_STORE = os.path.join(_TMP, "pisum_timeguard.json")

OFFLINE_GRACE_SECONDS = 48 * 3600   # 48 h offline tolerance
SYNC_INTERVAL_SECONDS = 600         # background re-sync every 10 mins
MAX_ACTIVATION_ATTEMPTS = 5         # per day per machine

class LicenseManager:
    """
    Central licensing engine for PISUM.
    Now powered by Supabase with strict Feature Gating rules.

    Usage:
        lm = LicenseManager()
        ok, msg = lm.activate("PISUM-PRO-XXXX-YYYY")
        if ok:
            lm.start_background_sync()
    """

    # ── constructor ──────────────────────────────────────
    def __init__(self):
        self._license_key:   str  | None = None
        self._user_name:     str  | None = None
        self._plan_name:     str         = "free"
        self._max_seats:     int         = 1
        self._is_active:     bool        = False
        self._expires_at:    datetime.datetime | None = None
        self._machine_id:    str         = self._build_machine_id()

        self._features = {
            "custom_templates": False,
            "ai_dictation_minutes_per_day": 0,
            "max_patients_per_day": 50,
            "max_reports_per_day": -1,       # daily cap (-1 = unlimited)
            "max_reports_per_month": 20,     # monthly cap (Free only)
            "pacs_ris": False,
            "export_word_quality": "none",   # "none" | "basic" | "premium"
            "export_word": False,            # Word export enabled
            "printing": True,               # PDF always available
            "history_days": 7,
            "advanced_editing": False,
            "structured_reports": False,
            "multilang": False,
            "export": True,                 # PDF export always
            # ── New plan features ──
            "worklist": False,              # False | "basic" | "full" | "advanced" | "multisite"
            "stats": False,                 # False | "basic" | True | "advanced"
            "ai_enhancer_monthly_limit": 0, # 0=off, -1=unlimited, N=monthly cap
            "templates_limit": 10,          # number of accessible templates
            "languages_limit": 2,           # number of accessible languages
        }
        self._usage = {feat: 0 for feat in TRACKED_FEATURES}
        self._last_reset_date = str(datetime.date.today())
        self._failed_attempts = 0

        # ── Monthly tracking (AI Enhancer + Free plan CR) ──
        self._ai_enhancer_uses_month = 0
        self._reports_uses_month = 0
        self._monthly_reset_key = datetime.date.today().strftime("%Y-%m")

        self._last_server_sync: float = 0.0
        self._sync_lock = threading.RLock()
        self._sync_thread: threading.Thread | None = None

        # Load cached license immediately (offline-first)
        self.load_local_license()

    # ══════════════════════════════════════════════════════
    #  MACHINE IDENTIFICATION
    # ══════════════════════════════════════════════════════
    def _build_machine_id(self) -> str:
        """SHA-256 fingerprint: MAC + OS + username."""
        raw_parts = [
            str(uuid.getnode()),
            platform.system(),
            platform.node(),
            getpass.getuser(),
        ]
        raw = "|".join(raw_parts).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _get_legacy_hardware_id(self) -> str | None:
        try:
            import subprocess
            result = subprocess.check_output(
                'powershell -Command "Get-CimInstance -ClassName Win32_ComputerSystemProduct | Select-Object -ExpandProperty UUID"',
                shell=True
            ).decode(errors="ignore").strip()
            if result and len(result) > 10:
                return result
        except Exception:
            pass
        try:
            import subprocess
            result = subprocess.check_output(
                "wmic csproduct get uuid", shell=True
            ).decode(errors="ignore").splitlines()
            hwid = [l.strip() for l in result if l.strip() and "UUID" not in l][0]
            return hwid
        except Exception:
            pass
        return f"FALLBACK-{os.getenv('COMPUTERNAME', 'UNKNOWN')}"

    @property
    def machine_id(self) -> str:
        return self._machine_id

    # ══════════════════════════════════════════════════════
    #  FETCH & REGISTER HELPERS
    # ══════════════════════════════════════════════════════
    def fetch_license(self, key: str) -> dict | None:
        """Fetch license document directly from Supabase Database."""
        try:
            url = f"{SUPABASE_URL}/rest/v1/licenses"
            headers = {
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            params = {"license_key": f"eq.{key}", "select": "*"}
            r = requests.get(url, headers=headers, params=params, timeout=8)
            r.raise_for_status()
            data = r.json()
            return data[0] if data else None
        except Exception as e:
            logger.warning("fetch_license error: %s", e)
            return None

    def fetch_active_devices(self, license_id: str) -> list:
        try:
            url = f"{SUPABASE_URL}/rest/v1/devices"
            headers = {
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                "Content-Type": "application/json"
            }
            params = {"license_id": f"eq.{license_id}", "select": "id,machine_id,is_active"}
            r = requests.get(url, headers=headers, params=params, timeout=8)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("fetch_active_devices error: %s", e)
            return []

    def register_device(self, license_id: str, existing_id: str = None, new_machine_id: str = None) -> bool:
        try:
            url = f"{SUPABASE_URL}/rest/v1/devices"
            headers = {
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            }
            payload = {
                "device_name": platform.node(),
                "is_active": True,
                "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            }
            if new_machine_id:
                payload["machine_id"] = new_machine_id

            if existing_id:
                params = {"id": f"eq.{existing_id}"}
                r = requests.patch(url, headers=headers, params=params, json=payload, timeout=8)
            else:
                payload["license_id"] = license_id
                payload["machine_id"] = new_machine_id or self._machine_id
                r = requests.post(url, headers=headers, json=payload, timeout=8)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error("register_device error: %s", e)
            return False

    def update_device_last_seen(self) -> None:
        pass

    def _record_attempt(self, key: str, success: bool) -> None:
        if not success:
            self._failed_attempts += 1

    def _count_failed_attempts_today(self) -> int:
        return self._failed_attempts

    # ══════════════════════════════════════════════════════
    #  ACTIVATION FLOW
    # ══════════════════════════════════════════════════════
    def activate(self, license_key: str) -> tuple[bool, str]:
        """
        Full activation flow:
        1. Anti-brute-force check
        2. Fetch license
        3. Seat check / device registration
        4. Save locally

        Returns (success: bool, message: str)
        """
        key = license_key.strip().upper()

        # ── Anti-brute-force ────────────────────────────
        if self._count_failed_attempts_today() >= MAX_ACTIVATION_ATTEMPTS:
            return False, "Trop de tentatives échouées aujourd'hui. Réessayez demain."

        # ── Fetch license ────────────────────────────────
        lic = self.fetch_license(key)
        if not lic:
            self._record_attempt(key, False)
            return False, "Clé de licence introuvable."

        if not lic.get("is_active", False):
            self._record_attempt(key, False)
            return False, "Licence désactivée. Contactez le support."

        # ── Expiry check ─────────────────────────────────
        expires_raw = lic.get("expires_at")
        if expires_raw:
            expires_dt = datetime.datetime.fromisoformat(
                expires_raw.replace("Z", "+00:00")
            )
            if expires_dt < datetime.datetime.now(datetime.timezone.utc):
                self._record_attempt(key, False)
                return False, "Licence expirée. Veuillez renouveler votre abonnement."

        # ── Seat check ───────────────────────────────────
        ok, msg = self._check_and_register_seat(lic)
        if not ok:
            self._record_attempt(key, False)
            return False, msg

        # ── Apply & save ─────────────────────────────────
        self._apply_license_row(lic)
        self.save_local_license()
        self._record_attempt(key, True)
        self._last_server_sync = time.time()

        return True, f"Licence activée — Bienvenue, {self._user_name} (Plan: {self._plan_name.upper()})"

    def _check_and_register_seat(self, lic: dict) -> tuple[bool, str]:
        license_id = lic.get("id")
        max_seats  = lic.get("max_seats", 1)
        all_devices = self.fetch_active_devices(license_id)
        active_devices = [d for d in all_devices if d.get("is_active")]
        machine_ids = [d.get("machine_id") for d in active_devices]
        
        if self._machine_id in machine_ids:
            return True, "Appareil déjà enregistré."

        # Migration automatique de l'ancien HWID vers le nouveau
        legacy_hwid = self._get_legacy_hardware_id()
        if legacy_hwid and legacy_hwid in machine_ids:
            existing_legacy = next((d for d in active_devices if d.get("machine_id") == legacy_hwid), None)
            if existing_legacy:
                ok = self.register_device(license_id, existing_id=existing_legacy.get("id"), new_machine_id=self._machine_id)
                return ok, "Appareil mis à jour avec le nouvel identifiant." if ok else "Erreur réseau lors de la mise à jour."
            
        if len(machine_ids) >= max_seats:
            return False, f"Tous les postes de cette licence sont utilisés ({len(machine_ids)}/{max_seats})."
            
        existing_device = next((d for d in all_devices if d.get("machine_id") == self._machine_id), None)
        ok = self.register_device(license_id, existing_id=existing_device.get("id") if existing_device else None)
        if not ok:
            return False, "Erreur réseau lors de l'enregistrement de l'appareil."
        return True, "Appareil enregistré avec succès."

    def _get_features_for_plan(self, plan: str) -> dict:
        plan = plan.lower()

        # ── Base = Free plan ─────────────────────────────────────────────────
        features = {
            "custom_templates": False,
            "ai_dictation_minutes_per_day": 0,   # no dictation
            "max_patients_per_day": 50,
            "max_reports_per_day": -1,            # no daily cap
            "max_reports_per_month": 20,          # 20 CR/month cap
            "pacs_ris": False,
            "export_word_quality": "none",
            "export_word": False,
            "printing": True,
            "history_days": 7,
            "advanced_editing": False,
            "structured_reports": False,
            "multilang": False,
            "export": True,                       # PDF always
            "worklist": False,
            "stats": False,
            "ai_enhancer_monthly_limit": 0,
            "templates_limit": 10,
            "languages_limit": 2,
        }

        if plan == "starter":
            features.update({
                "ai_dictation_minutes_per_day": 0,
                "max_patients_per_day": -1,
                "max_reports_per_day": -1,
                "max_reports_per_month": -1,      # unlimited
                "export_word_quality": "premium",
                "export_word": True,
                "printing": True,
                "history_days": 90,
                "structured_reports": True,
                "multilang": False,
                "export": True,
                "worklist": "basic",
                "stats": False,
                "ai_enhancer_monthly_limit": 0,
                "templates_limit": 20,
                "languages_limit": 5,
            })

        elif plan == "pro":
            features.update({
                "custom_templates": False,
                "ai_dictation_minutes_per_day": -1,   # unlimited
                "max_patients_per_day": -1,
                "max_reports_per_day": -1,
                "max_reports_per_month": -1,
                "pacs_ris": True,
                "export_word_quality": "premium",
                "export_word": True,
                "printing": True,
                "history_days": -1,
                "advanced_editing": True,
                "structured_reports": True,
                "multilang": True,
                "export": True,
                "worklist": "full",
                "stats": "basic",
                "ai_enhancer_monthly_limit": 100,
                "templates_limit": -1,                # 112+
                "languages_limit": 23,
            })

        elif plan == "expert":
            features.update({
                "custom_templates": True,
                "ai_dictation_minutes_per_day": -1,
                "max_patients_per_day": -1,
                "max_reports_per_day": -1,
                "max_reports_per_month": -1,
                "pacs_ris": True,
                "export_word_quality": "premium",
                "export_word": True,
                "printing": True,
                "history_days": -1,
                "advanced_editing": True,
                "structured_reports": True,
                "multilang": True,
                "export": True,
                "worklist": "advanced",
                "stats": True,
                "ai_enhancer_monthly_limit": -1,      # unlimited
                "templates_limit": -1,
                "languages_limit": 23,
            })

        elif plan == "clinic":
            features.update({
                "custom_templates": True,
                "ai_dictation_minutes_per_day": -1,
                "max_patients_per_day": -1,
                "max_reports_per_day": -1,
                "max_reports_per_month": -1,
                "pacs_ris": True,
                "export_word_quality": "premium",
                "export_word": True,
                "printing": True,
                "history_days": -1,
                "advanced_editing": True,
                "structured_reports": True,
                "multilang": True,
                "export": True,
                "worklist": "multisite",
                "stats": "advanced",
                "ai_enhancer_monthly_limit": -1,
                "templates_limit": -1,
                "languages_limit": 23,
            })

        elif plan == "solo":
            # Legacy plan — equivalent to Starter
            features.update({
                "ai_dictation_minutes_per_day": 60,
                "max_patients_per_day": 200,
                "max_reports_per_day": -1,
                "max_reports_per_month": -1,
                "pacs_ris": True,
                "export_word_quality": "premium",
                "export_word": True,
                "printing": True,
                "structured_reports": True,
                "multilang": False,
                "export": True,
                "worklist": "basic",
                "stats": False,
                "ai_enhancer_monthly_limit": 0,
                "templates_limit": 20,
                "languages_limit": 5,
            })

        return features

    def _apply_license_row(self, lic: dict) -> None:
        self._license_key  = lic.get("license_key", self._license_key)
        self._user_name    = lic.get("user_name", "Utilisateur")
        self._plan_name    = lic.get("plan", "free")
        self._max_seats    = lic.get("max_seats", 1)
        self._is_active    = lic.get("is_active", False)
        
        fetched_features = lic.get("features", {})
        if fetched_features:
            self._features = fetched_features
        else:
            self._features = self._get_features_for_plan(self._plan_name)

        expires_raw = lic.get("expires_at")
        if expires_raw:
            self._expires_at = datetime.datetime.fromisoformat(
                expires_raw.replace("Z", "+00:00")
            )
        else:
            self._expires_at = None

        self._check_daily_reset()

    # ══════════════════════════════════════════════════════
    #  LOCAL LICENSE CACHE  (offline-first)
    # ══════════════════════════════════════════════════════
    def _sign_payload(self, payload: dict) -> str:
        """HMAC-SHA256 signature of the JSON payload."""
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hmac.new(_CACHE_SIGN_KEY, data, hashlib.sha256).hexdigest()

    def save_local_license(self) -> None:
        with self._sync_lock:
            payload = {
                "license_key":              self._license_key,
                "user_name":                self._user_name,
                "plan":                     self._plan_name,
                "features":                 self._features,
                "usage":                    self._usage,
                "last_reset_date":          self._last_reset_date,
                "is_active":                self._is_active,
                "expires_at":               self._expires_at.isoformat() if self._expires_at else None,
                "machine_id":               self._machine_id,
                "saved_at":                 datetime.datetime.utcnow().isoformat(),
                # monthly tracking
                "ai_enhancer_uses_month":   self._ai_enhancer_uses_month,
                "reports_uses_month":       self._reports_uses_month,
                "monthly_reset_key":        self._monthly_reset_key,
            }
            payload["signature"] = self._sign_payload(
                {k: v for k, v in payload.items() if k != "signature"}
            )
            try:
                with open(LICENSE_STORE, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("save_local_license: %s", e)

    def load_local_license(self) -> bool:
        """
        Load cached license.
        Returns True if valid and not too stale.
        """
        if not os.path.exists(LICENSE_STORE):
            return False
        try:
            with open(LICENSE_STORE, "r", encoding="utf-8") as f:
                data = json.load(f)

            # ── Tamper detection ─────────────────────────
            stored_sig = data.pop("signature", "")
            expected   = self._sign_payload(data)
            if not hmac.compare_digest(stored_sig, expected):
                logger.warning("Local license tampered — ignoring cache.")
                self._nuke_cache()
                return False
            data["signature"] = stored_sig  # restore

            # ── Machine-binding check ────────────────────
            if data.get("machine_id") != self._machine_id:
                logger.warning("License cache belongs to a different machine.")
                self._nuke_cache()
                return False

            # ── Time-rollback detection ──────────────────
            if not self._check_time_guard(data.get("saved_at", "")):
                logger.warning("Time rollback detected — invalidating cache.")
                self._nuke_cache()
                return False

            # ── Offline grace period ─────────────────────
            saved_at = datetime.datetime.fromisoformat(data["saved_at"])
            age_sec  = (datetime.datetime.utcnow() - saved_at).total_seconds()
            if age_sec > OFFLINE_GRACE_SECONDS:
                logger.info("License cache too old — will require re-sync.")
                # Still load it but mark as needing sync
                self._last_server_sync = 0.0
            else:
                self._last_server_sync = time.time() - age_sec

            with self._sync_lock:
                # ── Apply cached data ────────────────────────
                self._license_key           = data.get("license_key")
                self._user_name             = data.get("user_name", "Utilisateur")
                self._plan_name             = data.get("plan", "free")
                self._features              = data.get("features", {})
                self._usage                 = data.get("usage", {feat: 0 for feat in TRACKED_FEATURES})
                self._last_reset_date       = data.get("last_reset_date", str(datetime.date.today()))
                self._is_active             = data.get("is_active", False)
                # monthly tracking
                self._ai_enhancer_uses_month = data.get("ai_enhancer_uses_month", 0)
                self._reports_uses_month     = data.get("reports_uses_month", 0)
                self._monthly_reset_key      = data.get("monthly_reset_key", datetime.date.today().strftime("%Y-%m"))

                expires_raw = data.get("expires_at")
                self._expires_at = (
                    datetime.datetime.fromisoformat(expires_raw) if expires_raw else None
                )

                self._check_daily_reset()
            return True

        except Exception as e:
            logger.error("load_local_license: %s", e)
            return False

    def _nuke_cache(self) -> None:
        try:
            os.remove(LICENSE_STORE)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    #  TIME-ROLLBACK DETECTION
    # ══════════════════════════════════════════════════════
    def _check_time_guard(self, saved_at_iso: str) -> bool:
        """
        Store the last known system time.
        If current time is significantly BEFORE the stored time, flag rollback.
        """
        try:
            now = datetime.datetime.utcnow()
            guard_data: dict = {}

            if os.path.exists(TIMEGUARD_STORE):
                with open(TIMEGUARD_STORE, "r") as f:
                    guard_data = json.load(f)

            last_known_str = guard_data.get("last_known_utc", "")
            if last_known_str:
                last_known = datetime.datetime.fromisoformat(last_known_str)
                # Allow up to 5 min backwards tolerance (NTP corrections)
                if (last_known - now).total_seconds() > 300:
                    return False  # rollback detected

            # Update timeguard
            guard_data["last_known_utc"] = now.isoformat()
            with open(TIMEGUARD_STORE, "w") as f:
                json.dump(guard_data, f)
            return True

        except Exception:
            return True  # Fail open on guard file errors

    # ══════════════════════════════════════════════════════
    #  BACKGROUND SYNC
    # ══════════════════════════════════════════════════════
    def start_background_sync(self) -> None:
        """Start a daemon thread that re-validates the license every 12 h."""
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="pisum-lic-sync"
        )
        self._sync_thread.start()

    def _sync_loop(self) -> None:
        while True:
            time.sleep(SYNC_INTERVAL_SECONDS)
            self.verify_periodically()

    def verify_periodically(self) -> bool:
        """
        Re-validate against Supabase.
        Called by background thread or manually.
        Returns True if still valid.
        """
        with self._sync_lock:
            if not self._license_key:
                return False
            license_key = self._license_key

        try:
            lic = self.fetch_license(license_key)
            
            with self._sync_lock:
                if not lic:
                    self._is_active = False
                    self._plan_name = "free"
                    self._features = self._get_features_for_plan("free")
                    self.save_local_license()
                    return False

                if not lic.get("is_active", False):
                    self._is_active = False
                    self._plan_name = "free"
                    self._features = self._get_features_for_plan("free")
                    self.save_local_license()
                    return False

                self._plan_name = lic.get("plan", self._plan_name)
                self._features = lic.get("features", self._features)
                self._is_active = True
                self.save_local_license()
                self._last_server_sync = time.time()
                logger.info("Periodic sync OK — plan=%s", self._plan_name)
                return True

        except Exception as e:
            logger.warning("verify_periodically failed: %s", e)
            # Offline — continue with cache
            with self._sync_lock:
                return self._is_active

    def refresh_license(self) -> tuple[bool, str]:
        """Manually force a license refresh from Firebase."""
        success = self.verify_periodically()
        if success:
            return True, "Licence mise à jour avec succès."
        else:
            return False, "Impossible de vérifier la licence. Utilisation du cache local."

    def needs_sync(self) -> bool:
        return (time.time() - self._last_server_sync) > OFFLINE_GRACE_SECONDS

    # ══════════════════════════════════════════════════════
    #  USAGE & FEATURE GATING LOGIC
    # ══════════════════════════════════════════════════════
    def _check_daily_reset(self) -> None:
        today = str(datetime.date.today())
        if self._last_reset_date != today:
            for feat in TRACKED_FEATURES:
                self._usage[feat] = 0
            self._last_reset_date = today

    def _check_monthly_reset(self) -> None:
        """Reset monthly counters (AI Enhancer, Free plan CR) on new month."""
        current_month = datetime.date.today().strftime("%Y-%m")
        if self._monthly_reset_key != current_month:
            self._ai_enhancer_uses_month = 0
            self._reports_uses_month = 0
            self._monthly_reset_key = current_month

    def can_use_feature(self, feature_name: str):
        """
        Central feature gate checking. 
        Behavior depends on feature configuration value:
        true -> allow
        false -> block
        -1 -> unlimited
        integer -> enforce daily limit
        string -> return mode
        """
        with self._sync_lock:
            self._check_daily_reset()
            
            # Forcer les limites de la version gratuite si la licence est désactivée
            if not self._is_active:
                val = self._get_features_for_plan("free").get(feature_name, False)
            else:
                val = self._features.get(feature_name, False)
            
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val
            if isinstance(val, (int, float)):
                if val == -1:
                    return True
                usage = self._usage.get(feature_name, 0)
                return usage < val
                
            return False

    def get_limit(self, feature_name: str) -> int:
        with self._sync_lock:
            if not self._is_active:
                val = self._get_features_for_plan("free").get(feature_name, 0)
            else:
                val = self._features.get(feature_name, 0)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return int(val)
        return 0

    def increment_usage(self, feature_name: str, amount: int = 1):
        if feature_name not in TRACKED_FEATURES:
            return
            
        with self._sync_lock:
            self._check_daily_reset()
            self._usage[feature_name] = self._usage.get(feature_name, 0) + amount
            self.save_local_license()

    def get_plan_name(self) -> str:
        return self._plan_name

    # ══════════════════════════════════════════════════════
    #  LEGACY METHODS (Wrapped to use new Feature Logic)
    # ══════════════════════════════════════════════════════
    def increment_dictation_minutes(self, minutes: int) -> None:
        self.increment_usage("ai_dictation_minutes_per_day", minutes)

    def increment_patient_count(self) -> None:
        self.increment_usage("max_patients_per_day", 1)

    def decrement_patient_count(self) -> None:
        pass # Decrementing not typically required in daily caps

    def increment_custom_template_count(self) -> None:
        pass # Custom templates is now a boolean feature, no limit counter

    def can_create_report(self) -> tuple[bool, str]:
        with self._sync_lock:
            self._check_monthly_reset()
            # Daily limit (paid plans) — -1 = unlimited
            if not self.can_use_feature("max_reports_per_day"):
                return False, "Limite journalière atteinte. Passez à une offre supérieure."
            # Monthly limit (Free plan)
            monthly_limit = self._features.get("max_reports_per_month", -1)
            if monthly_limit != -1 and self._reports_uses_month >= monthly_limit:
                return False, f"Limite de {monthly_limit} CR/mois atteinte. Passez à Starter ou supérieur."
        return True, ""

    def increment_report_count(self) -> None:
        with self._sync_lock:
            self._check_monthly_reset()
            self._reports_uses_month += 1
        self.increment_usage("max_reports_per_day", 1)

    def can_add_patient(self) -> tuple[bool, str]:
        if self.can_use_feature("max_patients_per_day"):
            return True, ""
        return False, "Limite atteinte : Passez à une offre supérieure pour des patients illimités."

    def can_create_custom_template(self) -> tuple[bool, str]:
        if self.can_use_feature("custom_templates"):
            return True, ""
        return False, "Les modèles personnalisés sont disponibles à partir de Expert."

    def can_dictate(self, extra_minutes: int = 1) -> tuple[bool, str]:
        limit = self._features.get("ai_dictation_minutes_per_day", 0)
        if limit == 0:
            return False, "La dictée AI est disponible à partir de Pro."
        if self.can_use_feature("ai_dictation_minutes_per_day"):
            return True, ""
        return False, "Limite de dictée atteinte pour aujourd'hui."

    def can_use_pdf_pro(self) -> bool:
        return self.can_use_feature("export_word_quality") == "premium"

    def has_watermark(self) -> bool:
        q = self.can_use_feature("export_word_quality")
        return q in ("basic", "none", False)

    def can_use_word_export(self) -> tuple[bool, str]:
        if self._features.get("export_word", False):
            return True, ""
        return False, "L'export Word est disponible à partir de Starter."

    def can_use_ai_enhancer(self) -> tuple[bool, str]:
        with self._sync_lock:
            self._check_monthly_reset()
            limit = self._features.get("ai_enhancer_monthly_limit", 0)
            if limit == 0:
                return False, "L'AI Enhancer est disponible à partir de Pro (100/mois)."
            if limit == -1:
                return True, ""
            if self._ai_enhancer_uses_month >= limit:
                return False, f"Limite mensuelle atteinte ({limit} utilisations). Renouvelée le 1er du mois prochain."
        return True, ""

    def increment_ai_enhancer_count(self) -> None:
        with self._sync_lock:
            self._check_monthly_reset()
            limit = self._features.get("ai_enhancer_monthly_limit", 0)
            if limit not in (0, -1):   # only track if limited
                self._ai_enhancer_uses_month += 1
                self.save_local_license()

    def ai_enhancer_remaining(self) -> int | str:
        with self._sync_lock:
            self._check_monthly_reset()
            limit = self._features.get("ai_enhancer_monthly_limit", 0)
            if limit == 0:  return 0
            if limit == -1: return "∞"
            return max(0, limit - self._ai_enhancer_uses_month)

    def can_use_worklist(self) -> str | bool:
        """Returns worklist level: False | 'basic' | 'full' | 'advanced' | 'multisite'."""
        val = self._features.get("worklist", False)
        if not self._is_active:
            return False
        return val

    def can_use_stats(self):
        """Returns stats level: False | 'basic' | True | 'advanced'."""
        if not self._is_active:
            return False
        return self._features.get("stats", False)

    def get_templates_limit(self) -> int:
        """Returns max templates accessible (-1 = unlimited)."""
        return self._features.get("templates_limit", 10)

    def get_languages_limit(self) -> int:
        """Returns max languages accessible."""
        return self._features.get("languages_limit", 2)

    def can_use_multi_user(self) -> bool:
        plan = self.get_plan_name()
        return plan == "clinic"

    # ── Computed helpers ─────────────────────────────────
    def reports_remaining(self) -> int | str:
        limit = self.get_limit("max_reports_per_day")
        if limit == -1: return "∞"
        return max(0, limit - self.reports_used)

    def patients_remaining(self) -> int | str:
        limit = self.get_limit("max_patients_per_day")
        if limit == -1: return "∞"
        return max(0, limit - self.patients_count)

    def dictation_remaining_today(self) -> int | str:
        limit = self.get_limit("ai_dictation_minutes_per_day")
        if limit == -1: return "∞"
        return max(0, limit - self._dictation_used_today)

    # ── Convenience properties ────────────────────────────
    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def user_name(self) -> str:
        return self._user_name or "Utilisateur"

    @property
    def plan(self):
        """Returns a dummy plan object for backward compatibility with UI components."""
        outer = self
        class DummyPlan:
            key = outer.get_plan_name()
            display_name = outer.get_plan_name().upper()
            reports_limit = outer.get_limit('max_reports_per_day')
            patients_limit = outer.get_limit('max_patients_per_day')
            dictation_limit_min = outer.get_limit('ai_dictation_minutes_per_day')
            custom_templates_limit = 1 if outer.can_use_feature('custom_templates') else 0
            has_pdf_pro = outer.can_use_feature('export_word_quality') == 'premium'
            has_watermark = outer.can_use_feature('export_word_quality') == 'basic'
            multi_user = False
        return DummyPlan()

    @property
    def license_key(self) -> str | None:
        return self._license_key

    @property
    def reports_used(self) -> int:
        with self._sync_lock:
            self._check_daily_reset()
            return self._usage.get("max_reports_per_day", 0)

    @property
    def patients_count(self) -> int:
        with self._sync_lock:
            self._check_daily_reset()
            return self._usage.get("max_patients_per_day", 0)
            
    @property
    def _dictation_used_today(self) -> int:
        with self._sync_lock:
            self._check_daily_reset()
            return self._usage.get("ai_dictation_minutes_per_day", 0)

    def days_until_expiry(self) -> int | None:
        if not self._expires_at:
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = self._expires_at - now
        return max(0, delta.days)

    def is_expired(self) -> bool:
        if not self._expires_at:
            return False
        return datetime.datetime.now(datetime.timezone.utc) > self._expires_at

    def deactivate_device(self) -> None:
        """
        Remove this device's seat so it can be transferred.
        Call this on uninstall or from the UI.
        """
        if not self._license_key:
            return
        self._nuke_cache()
        self._is_active = False

    def smart_notification(self) -> str | None:
        """
        Return a contextual upsell notification string, or None.
        Drive conversion with data-driven messaging.
        """
        self._check_daily_reset()
        self._check_monthly_reset()
        plan = self.get_plan_name()

        # Free plan: CR monthly limit warning
        if plan == "free":
            monthly_limit = self._features.get("max_reports_per_month", 20)
            if monthly_limit > 0 and self._reports_uses_month >= monthly_limit * 0.8:
                remaining = max(0, monthly_limit - self._reports_uses_month)
                return (
                    f"Vous approchez la limite de {monthly_limit} CR/mois "
                    f"({remaining} restants).\n"
                    "Passez à Starter pour des CR illimités."
                )

        # Pro plan: AI Enhancer monthly limit warning
        if plan == "pro":
            enhancer_limit = self._features.get("ai_enhancer_monthly_limit", 100)
            if enhancer_limit > 0 and self._ai_enhancer_uses_month >= enhancer_limit * 0.8:
                remaining = max(0, enhancer_limit - self._ai_enhancer_uses_month)
                return (
                    f"AI Enhancer : {remaining} utilisations restantes ce mois.\n"
                    "Passez à Expert pour un usage illimité."
                )

        days = self.days_until_expiry()
        if days is not None and days <= 7:
            return f"Votre licence expire dans {days} jours. Renouvelez maintenant."

        return None
