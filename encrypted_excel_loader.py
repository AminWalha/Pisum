"""
encrypted_excel_loader.py
Module pour charger UNIQUEMENT les fichiers Excel chiffrés (.enc)
Version SÉCURISÉE — clé stockée en bytes hex (invisible à strings.exe)
PyArmor --enable-bcc + --mix-str obfusque complètement cette fonction

⚠️ APRÈS CHAQUE BUILD PyInstaller :
   Mettre à jour _EXPECTED_HASH avec la commande :
   python -c "import hashlib,sys; h=hashlib.sha256(); f=open(sys.executable,'rb'); h.update(f.read(524288)); print(h.hexdigest())"
"""

import os
import sys
import hashlib
import tempfile
import ctypes
import platform
from pathlib import Path
from cryptography.fernet import Fernet
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def get_base_path():
    """
    Retourne le chemin de base de l'application.
    Fonctionne en mode script ET en mode exe (PyInstaller).
    """
    if getattr(sys, 'frozen', False):
        return getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _get_embedded_key() -> bytes:
    """
    🔒 SÉCURITÉ : Clé stockée en entiers hexadécimaux.
    - Aucun fragment lisible par strings.exe / HxD / binwalk
    - PyArmor --enable-bcc compile cette fonction en C natif
    - PyArmor --mix-str obfusque toutes les valeurs restantes
    - IMPORTANT : ces bytes correspondent exactement à la clé Fernet
      utilisée pour chiffrer les fichiers .enc — NE PAS MODIFIER
    """
    _k = bytes([
        0x6b,0x4d,0x2d,0x5f,0x78,0x36,0x75,0x30,0x42,0x66,0x63,0x49,0x38,0x72,
        0x75,0x6f,0x6a,0x6f,0x62,0x53,0x35,0x33,0x6f,0x69,0x46,0x6d,0x6b,0x6e,
        0x76,0x48,0x5a,0x30,0x33,0x36,0x5f,0x32,0x31,0x32,0x62,0x4e,0x72,0x6a,
        0x30,0x3d,
    ])
    return _k


def _is_debugger_present() -> bool:
    """
    Détecte la présence d'un débogueur (Windows uniquement).
    Ignoré sur Linux/macOS (développement).
    PyArmor --enable-bcc compile cette fonction en C natif.
    """
    if platform.system() != "Windows":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        if kernel32.IsDebuggerPresent():
            return True
        is_remote = ctypes.c_bool(False)
        kernel32.CheckRemoteDebuggerPresent(
            kernel32.GetCurrentProcess(),
            ctypes.byref(is_remote)
        )
        if is_remote.value:
            return True
    except Exception:
        pass
    return False


# ⚠️ IMPORTANT : Remplir ce hash APRÈS chaque build PyInstaller/PyArmor.
# Commande à exécuter UNE FOIS le .exe généré :
#   python -c "import hashlib,sys; h=hashlib.sha256(); f=open(r'dist\PISUM.exe','rb'); h.update(f.read(524288)); print(h.hexdigest())"
# Puis coller le résultat (64 caractères) ci-dessous.
_EXPECTED_HASH: str = "54a33b9d311d1527b04b11960f8ce8ef1a09a5ba41857b241b4442c202d6d570"


def _verify_integrity() -> bool:
    """
    Vérifie que le binaire exe n'a pas été patché ET qu'aucun débogueur n'est attaché.
    Ignoré en mode développement (script .py).
    """
    if not getattr(sys, 'frozen', False):
        return True

    if _is_debugger_present():
        logger.error("🚨 Débogueur détecté — arrêt de sécurité.")
        return False

    if _EXPECTED_HASH:
        try:
            hasher = hashlib.sha256()
            with open(sys.executable, 'rb') as f:
                hasher.update(f.read(512 * 1024))
            computed = hasher.hexdigest()
            if computed != _EXPECTED_HASH:
                logger.error(f"🚨 Intégrité compromise : {computed[:16]}… ≠ attendu")
                return False
        except Exception as e:
            logger.error(f"❌ Erreur vérification intégrité : {e}")
            return False

    return True


# ── Clé singleton (calculée une seule fois) ───────────────────────────────────
EMBEDDED_ENCRYPTION_KEY = _get_embedded_key()


class EncryptedExcelLoader:
    """Charge UNIQUEMENT des fichiers Excel chiffrés (.enc)"""

    def __init__(self, key_file="encryption.key", embedded_key=None):
        self.key_file    = key_file
        self.embedded_key = embedded_key
        self.base_path   = get_base_path()

        if not _verify_integrity():
            raise RuntimeError("Intégrité du binaire compromise — arrêt.")

        self.key = self._load_key()

        if self.key:
            self.fernet = Fernet(self.key)
            logger.info(f"✅ Chiffrement initialisé depuis {self.base_path}")
        else:
            self.fernet = None
            logger.error("❌ AUCUNE CLÉ DE CHIFFREMENT")
            raise ValueError("Clé de chiffrement requise pour lire les fichiers .enc")

    def _load_key(self):
        """Charge la clé (priorité : embedded > fichier .key)"""
        if self.embedded_key:
            logger.info("🔐 Clé intégrée utilisée")
            return self.embedded_key

        try:
            key_path = os.path.join(self.base_path, self.key_file)
            if os.path.exists(key_path):
                with open(key_path, 'rb') as f:
                    logger.info(f"🔑 Clé chargée depuis {key_path}")
                    return f.read()
            logger.warning(f"⚠️ Fichier clé non trouvé : {key_path}")
            return None
        except Exception as e:
            logger.error(f"❌ Erreur chargement clé : {e}")
            return None

    def get_resource_path(self, relative_path):
        relative_path = relative_path.replace('/', os.sep).replace('\\', os.sep)
        return os.path.join(self.base_path, relative_path)

    def read_excel(self, relative_path, sheet_name=None, **kwargs):
        """Lit un fichier Excel chiffré UNIQUEMENT (.enc)"""
        if not relative_path.endswith('.enc'):
            relative_path = relative_path + '.enc'

        file_path = self.get_resource_path(relative_path)

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"❌ Fichier chiffré non trouvé : {relative_path}")

        if not file_path.endswith('.enc'):
            raise ValueError(f"❌ Seuls les fichiers .enc sont autorisés : {relative_path}")

        return self._read_encrypted_excel(file_path, sheet_name, **kwargs)

    def _read_encrypted_excel(self, file_path, sheet_name=None, **kwargs):
        if not self.fernet:
            raise ValueError("Aucune clé de chiffrement disponible")

        with open(file_path, 'rb') as f:
            encrypted_data = f.read()

        decrypted_data = self.fernet.decrypt(encrypted_data)

        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(decrypted_data)
            tmp_path = tmp.name

        try:
            df = pd.read_excel(tmp_path, sheet_name=sheet_name, **kwargs)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

        logger.info(f"✅ Fichier chiffré lu : {os.path.basename(file_path)}")
        return df

    def detect_excel_files(self, relative_directory, encrypted_only=True):
        full_directory = self.get_resource_path(relative_directory)
        directory = Path(full_directory)

        if not directory.exists():
            logger.warning(f"⚠️ Répertoire introuvable : {full_directory}")
            return []

        enc_files = list(directory.glob('*.xlsx.enc')) + list(directory.glob('*.xls.enc'))

        if encrypted_only:
            xlsx_files = [f for f in directory.glob('*.xlsx') if not str(f).endswith('.enc')]
            if xlsx_files:
                logger.warning(f"⚠️ {len(xlsx_files)} fichier(s) .xlsx non chiffrés ignorés")

        relative_files = []
        for f in enc_files:
            try:
                rel_path = os.path.relpath(f, self.base_path)
                relative_files.append(rel_path)
            except Exception:
                relative_files.append(str(f))

        logger.info(f"🔒 {len(relative_files)} fichier(s) .enc détectés")
        return relative_files


# ── Singleton loader ──────────────────────────────────────────────────────────

_loader = None


def get_loader():
    """Retourne l'instance singleton du loader avec clé intégrée"""
    global _loader
    if _loader is None:
        _loader = EncryptedExcelLoader(embedded_key=EMBEDDED_ENCRYPTION_KEY)
    return _loader


def read_excel(relative_path, sheet_name=None, **kwargs):
    """Helper — lit un Excel chiffré (.enc) avec chemin relatif"""
    return get_loader().read_excel(relative_path, sheet_name, **kwargs)


def get_resource_path(relative_path):
    """Helper — retourne le chemin absolu d'une ressource"""
    return get_loader().get_resource_path(relative_path)


# ── Test ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("\n" + "="*70)
    print("🔒 TEST DE LECTURE — FICHIERS CHIFFRÉS UNIQUEMENT")
    print("="*70)

    try:
        loader = get_loader()
        print(f"\n📁 Chemin de base : {loader.base_path}")
        print(f"🔑 Clé chargée    : {'✅ Oui' if loader.key else '❌ Non'}")

        test_file = "CRs by languages/Francais/IRM.xlsx.enc"
        try:
            df = read_excel(test_file, sheet_name=None)
            print(f"  ✅ Fichier chargé : {len(df)} feuille(s)")
        except Exception as e:
            print(f"  ❌ Erreur : {e}")

    except Exception as e:
        print(f"\n❌ Erreur : {e}")

    print("\n" + "="*70)
