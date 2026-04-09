import sys
import os
import hashlib
import logging
import uuid
import datetime
import math
import subprocess
from pathlib import Path
import urllib.request
import tempfile
import json
import threading
import time
import platform
import getpass

# ── Keeper frame invisible ────────────────────────────────────────────────────

# Importer la configuration de langue locale si existante, ou mocker la fonction
try:
    from shared_config import get_selected_language, save_selected_language
except ImportError:
    def get_selected_language(): return "Français"
    def save_selected_language(lang): pass 

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FLAGS_DIR = os.path.join(BASE_DIR, "flags")
LICENSE_STORE     = os.path.join(tempfile.gettempdir(), "pisum_license_store.json")
LANGUAGE_STORE    = os.path.join(tempfile.gettempdir(), "pisum_language_store.json")

logging.basicConfig(level=logging.WARNING)

# ==================== CONFIGURATION SUPABASE ====================
import base64 as _b64

_SALT = b"pisum_v1"

def _reveal(token: str) -> str:
    raw = _b64.b64decode(token.encode())
    k = (_SALT * (len(raw) // len(_SALT) + 1))[:len(raw)]
    return bytes(a ^ b for a, b in zip(raw, k)).decode()

_ENC_URL = "GB0HBR5lWR4cDAMEDzEeQxQOFRAZMA9CFQ0RBEMsA0ERCxIGCHEVXg=="
_ENC_KEY = "FRA5HQ8YFVg/ADk8OCU/AD4AOgYkMSQEEyo6QyQ0BmkmKjlMQzoPewAKQDgEEB97Cg0rNwUGG3cKMyA8HhYYexwzGjxbFhtJHAo7MwQ9G1kJMzQRAAUuYwYMKzsBBTF7CCAaAgQ8GwgDMyA8WxYbdwULQUEEEzV7ADArJAQQHHRDJwkgGhEcYEEkNzQeFhtnRAowPFsSHHBFJDcsXRIyZAckO0VDDAIAQiAEMl0AJGM7EwsHDxdHYCIGHzYubTl+MQggPB4UQmE0BB8nXxMZXg=="

SUPABASE_URL = _reveal(_ENC_URL)
SUPABASE_KEY = _reveal(_ENC_KEY)

def _supabase_request(method: str, endpoint: str, payload: dict = None):
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    
    data = json.dumps(payload).encode('utf-8') if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            resp_body = response.read().decode('utf-8')
            if not resp_body:
                return True, []
            return True, json.loads(resp_body)
    except urllib.error.HTTPError as e:
        print(f"Supabase HTTP Error: {e.code} - {e.read().decode('utf-8')}")
        return False, str(e)
    except Exception as e:
        print(f"Supabase Error: {e}")
        return False, str(e)

# ==================== DICTIONNAIRES DE LANGUE ====================

LANGUAGE_CONFIG = {
    "Français": {"code": "fr", "flag": "fr.png"},
    "English": {"code": "gb", "flag": "gb.png"},
    "中文": {"code": "cn", "flag": "cn.png"},
    "Español": {"code": "es", "flag": "es.png"},
    "Deutsch": {"code": "de", "flag": "de.png"},
    "Italiano": {"code": "it", "flag": "it.png"},
    "Português": {"code": "pt", "flag": "pt.png"},
    "Русский": {"code": "ru", "flag": "ru.png"},
    "Türkçe": {"code": "tr", "flag": "tr.png"},
    "Svenska": {"code": "se", "flag": "se.png"},
    "Norsk": {"code": "no", "flag": "no.png"},
    "Dansk": {"code": "dk", "flag": "dk.png"},
    "Nederlands": {"code": "nl", "flag": "nl.png"},
    "日本語": {"code": "jp", "flag": "jp.png"},
    "한국어": {"code": "kr", "flag": "kr.png"},
    "Bahasa Indonesia": {"code": "id", "flag": "id.png"},
    "Polski": {"code": "pl", "flag": "pl.png"},
    "ไทย": {"code": "th", "flag": "th.png"},
    "Bahasa Melayu": {"code": "my", "flag": "my.png"},
    "Ελληνικά": {"code": "gr", "flag": "gr.png"},
    "Filipino": {"code": "ph", "flag": "ph.png"},
    "Română": {"code": "ro", "flag": "ro.png"},
    "हिन्दी": {"code": "in", "flag": "in.png"},
}

LANGUAGE_TO_TRANSLATION = {
    "Français": "Francais",
    "English": "Anglais",
    "中文": "Chinois",
    "Español": "Espagnol",
    "Deutsch": "Allemand",
    "Italiano": "Italien",
    "Português": "Portugais",
    "Русский": "Russe",
    "Türkçe": "Turc",
    "Svenska": "Suedois",
    "Norsk": "Norvegien",
    "Dansk": "Danois",
    "Nederlands": "Neerlandais",
    "日本語": "Japonais",
    "한국어": "Coreen",
    "Bahasa Indonesia": "Indonesien",
    "Polski": "Polonais",
    "ไทย": "Thai",
    "Bahasa Melayu": "Malais",
    "Ελληνικά": "Grec",
    "Filipino": "Filipino",
    "Română": "Roumain",
    "हिन्दी": "Hindi",
}

TRANSLATIONS = {
    "Francais": {
        "err_net_msg": "Une erreur est survenue",
        "err_license_not_found": "Licence invalide (clé introuvable)",
        "err_license_banned": "Licence bannie (contactez le support)",
        "err_license_misconfigured": "Licence mal configurée - contactez le support",
        "err_license_expired_short": "Licence expirée",
        "err_seats_full": "Tous les sièges de cette licence sont utilisés ({used}/{total})",
        "success_license_valid": "Licence valide",
        "success_hwid_valid": "HWID validé avec succès",
    },
    "Anglais": {
        "err_net_msg": "An error occurred",
        "err_license_not_found": "Invalid license (key not found)",
        "err_license_banned": "License banned (please contact support)",
        "err_license_misconfigured": "License misconfigured - please contact support",
        "err_license_expired_short": "License expired",
        "err_seats_full": "All seats for this license are in use ({used}/{total})",
        "success_license_valid": "License valid",
        "success_hwid_valid": "HWID validated successfully",
    }
}

LICENSE_ERROR_TRANSLATIONS = {
    "Francais": {
        "error_title": "Licence invalide",
        "error_msg": "Cette licence est déjà activée sur un autre ordinateur",
        "return_msg": "Cette licence est activée sur un autre ordinateur",
        "what_to_do": "💡  Que faire ?\n   • Si c'est votre ordinateur habituel, contactez le support.\n   • Si vous avez changé de PC, une réinitialisation est nécessaire.\n   • Si vous avez une licence multi-postes, vérifiez que vous\n     n'avez pas atteint la limite de sièges autorisés."
    },
    "Anglais": {
        "error_title": "Invalid License",
        "error_msg": "This license is already activated on another computer",
        "return_msg": "This license is activated on another computer",
        "what_to_do": "💡  What to do?\n   • If this is your usual computer, contact support.\n   • If you changed your PC, a reset is required.\n   • If you have a multi-seat license, make sure you\n     have not reached the maximum number of seats."
    }
}

# ==================== FONCTIONS UTILITAIRES ====================

def get_translation(language, key, default=""):
    trans_key = LANGUAGE_TO_TRANSLATION.get(language, "Francais")
    return TRANSLATIONS.get(trans_key, {}).get(key, default)

def get_errortranslation(language, key, default=""):
    trans_key = LANGUAGE_TO_TRANSLATION.get(language, "Francais")
    return LICENSE_ERROR_TRANSLATIONS.get(trans_key, {}).get(key, default)

def get_hardware_id() -> str:
    raw_parts = [
        str(uuid.getnode()),
        platform.system(),
        platform.node(),
        getpass.getuser(),
    ]
    return hashlib.sha256("|".join(raw_parts).encode("utf-8")).hexdigest()

def get_legacy_hardware_id() -> str:
    try:
        result = subprocess.check_output(
            'powershell -Command "Get-CimInstance -ClassName Win32_ComputerSystemProduct | Select-Object -ExpandProperty UUID"',
            shell=True
        ).decode(errors="ignore").strip()
        if result and len(result) > 10:
            return result
    except Exception:
        pass
    try:
        result = subprocess.check_output(
            "wmic csproduct get uuid", shell=True
        ).decode(errors="ignore").splitlines()
        hwid = [l.strip() for l in result if l.strip() and "UUID" not in l][0]
        return hwid
    except Exception:
        pass
    return f"FALLBACK-{os.getenv('COMPUTERNAME', 'UNKNOWN')}"

def get_computer_name() -> str:
    try:
        comp_name = os.getenv('COMPUTERNAME')
        if comp_name:
            return comp_name
        return platform.node() or 'Unknown'
    except Exception:
        return 'Unknown'


# ==================== CLASSES DE GESTION ====================

class CloudLicenseDatabase:
    def __init__(self):
        self.language = "Français"

    def verify_license(self, license_key, current_hwid, language="Français"):
        self.language = language
        license_key = license_key.strip().upper()

        ok, data = _supabase_request('GET', f"licenses?license_key=eq.{license_key}&select=*")

        if not ok or not data:
            return False, get_translation(language, "err_license_not_found", "Invalid license (key not found)"), None, None, 1

        lic = data[0]
        
        if not lic.get("is_active", False):
            return False, get_translation(language, "err_license_banned", "License banned (please contact support)"), None, None, 1

        exp_str = lic.get("expires_at")
        if not exp_str:
            return False, get_translation(language, "err_license_misconfigured", "License misconfigured"), None, None, 1

        try:
            exp_clean = exp_str.replace("Z", "+00:00")
            expiration_date = datetime.datetime.fromisoformat(exp_clean)
            if expiration_date.tzinfo is not None:
                expiration_date = expiration_date.astimezone().replace(tzinfo=None)
        except Exception:
            expiration_date = datetime.datetime.now()

        if datetime.datetime.now() > expiration_date:
            return False, get_translation(language, "err_license_expired_short", "License expired"), lic.get("id"), None, 1

        seats = lic.get("max_seats", 1)
        return True, get_translation(language, "success_license_valid", "License valid"), lic.get("id"), expiration_date, seats


class HWIDRegistryManager:
    def __init__(self):
        pass

    def verify_hwid(self, license_key, current_hwid, language="Français", seats=1, expiration_date=None):
        license_key = license_key.strip().upper()
        current_hwid = current_hwid.strip()

        ok, lic_data = _supabase_request('GET', f"licenses?license_key=eq.{license_key}&select=id")
        if not ok or not lic_data:
            return False, get_translation(language, "err_net_msg", "Error"), None
            
        lic_id = lic_data[0]["id"]

        ok, devices = _supabase_request('GET', f"devices?license_id=eq.{lic_id}&is_active=eq.true&select=id,machine_id")
        if not ok:
            return False, get_translation(language, "err_net_msg", "Error"), None

        registered_hwids = [d["machine_id"] for d in devices]

        legacy_hwid = get_legacy_hardware_id()
        if current_hwid in registered_hwids:
            return True, get_translation(language, "success_hwid_valid", "HWID validated successfully"), None

        if legacy_hwid in registered_hwids:
            legacy_device = next((d for d in devices if d["machine_id"] == legacy_hwid), None)
            if legacy_device:
                payload = {
                    "machine_id": current_hwid,
                    "device_name": platform.node(),
                    "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                }
                _supabase_request('PATCH', f"devices?id=eq.{legacy_device['id']}", payload)
            return True, get_translation(language, "success_hwid_valid", "HWID validated successfully"), None

        if len(registered_hwids) < seats:
            payload = {
                "license_id": lic_id,
                "machine_id": current_hwid,
                "device_name": platform.node(),
                "is_active": True,
                "last_seen": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            }
            post_ok, _ = _supabase_request('POST', "devices", payload)
            if post_ok:
                return True, get_translation(language, "success_hwid_valid", "HWID validated successfully"), None
            else:
                return False, "Erreur de connexion au serveur d'activation", None

        seats_msg = get_translation(language, "err_seats_full", "All seats for this license are in use ({used}/{total})")
        seats_msg = seats_msg.replace("{used}", str(len(registered_hwids))).replace("{total}", str(seats))
        
        trans_key = LANGUAGE_TO_TRANSLATION.get(language, "Francais")
        if seats > 1:
            errormsg = seats_msg
        else:
            errormsg = LICENSE_ERROR_TRANSLATIONS.get(trans_key, {}).get("error_msg", seats_msg)
        
        return False, errormsg, None

    def register_hwid(self, license_key, hwid):
        return True


class LicenseStore:
    def __init__(self, store_file=LICENSE_STORE):
        self.store_file = store_file

    def save_license(self, license_key):
        try:
            data = {"license_key": license_key, "saved_at": datetime.datetime.now().isoformat()}
            with open(self.store_file, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            return True
        except Exception as e:
            print(f"⚠️ License save error: {e}")
            return False

    def load_license(self):
        try:
            if os.path.exists(self.store_file):
                with open(self.store_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return data.get("license_key")
        except Exception as e:
            print(f"⚠️ License loading error: {e}")
        return None

    def delete_license(self):
        try:
            if os.path.exists(self.store_file):
                os.remove(self.store_file)
                return True
        except Exception as e:
            print(f"⚠️ License deletion error: {e}")
            return False



# ==================== FIN DU MODULE ====================
