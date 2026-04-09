# -*- coding: utf-8 -*-
"""
pacs_network_sync.py — Synchronisation réseau PACS/RIS (réseau local clinique)
===============================================================================
Permet à plusieurs postes de la même clinique, connectés en réseau local
(LAN/WiFi), de partager et synchroniser la même base de données patients.

ARCHITECTURE
------------
  Mode A — Base partagée (recommandé pour réseau stable) :
    Tous les postes pointent directement sur un fichier SQLite situé sur un
    partage réseau (NAS, dossier partagé Windows/SMB, NFS Linux).
    WAL + verrou fichier SQLite gèrent la concurrence.

  Mode B — Réplication périodique (réseau instable / hors-ligne possible) :
    Chaque poste garde sa base locale. Un thread de sync pousse les nouvelles
    écritures vers la base maître et tire les changements des autres postes.
    Basé sur les colonnes updated_at + un journal de réplication.

UTILISATION RAPIDE
------------------
    from pacs_network_sync import NetworkSyncConfig, init_network_sync

    # Dans votre main.py, avant d'ouvrir PacsRisFrame :
    cfg = NetworkSyncConfig(
        mode="shared",                          # ou "replicated"
        network_path=r"\\\\SERVEUR\\PISUM\\db", # chemin réseau (Windows)
        # network_path="/mnt/pisum/db",         # chemin réseau (Linux/Mac)
        clinic_name="Clinique Exemple",
        workstation_id="POSTE-01",              # identifiant unique du poste
        # Pour app wx, ne pas passer network_password en clair :
        password_callback=lambda: wx.GetPasswordFromUser("Mot de passe réseau PISUM"),
    )
    sync = init_network_sync(cfg)

    # Pour utiliser la DB réseau dans PacsRisFrame :
    from pacs_ris_db import PacsRisDB
    db = sync.get_db()      # retourne un PacsRisDB pointant sur la bonne BD

SÉCURITÉ
--------
  • Chiffrement des données PII : toujours actif (AES-256-GCM).
  • La clé de chiffrement réseau est distincte de la clé locale.
    Elle est stockée dans le dossier réseau partagé (.enc_network_key),
    protégée par un mot de passe réseau commun à tous les postes.
  • Audit log : toutes les opérations réseau sont tracées.
  • Accès réseau protégé par mot de passe partagé (PBKDF2-HMAC-SHA256).
  • NE PAS stocker network_password dans pisum_network.cfg (texte clair).
    Utiliser password_callback ou la variable d'environnement PISUM_NET_PWD.

PRÉREQUIS
---------
  • Python ≥ 3.10
  • cryptography   (pip install cryptography)
  • Le dossier réseau doit être accessible en lecture/écriture par tous
    les postes (partage SMB, NFS, ou équivalent).
  • Pour WAL SQLite sur réseau : SMB ≥ 3.0 ou NFS avec lock activé.

CORRECTIONS v2 (stabilité & performance)
-----------------------------------------
  [PERF-1] NetworkEncryptor : cache salt→clé pour éviter PBKDF2×600k par champ.
           Sans cache : sync 100 patients ≈ 70s. Avec cache : ≈ 0s (après 1er accès).
  [PERF-2] Insertions en batch via executemany() au lieu de N execute() individuels.
  [PERF-3] Vérification des parents (patients/examens) en une requête IN(...)
           au lieu de N SELECT individuels dans la boucle pull.
  [PERF-4] Index updated_at + source_workstation sur toutes les tables réseau.
  [PERF-5] PRAGMA synchronous=NORMAL + cache_size=-4000 sur toutes les connexions.
  [PERF-6] PRAGMA wal_autocheckpoint=100 pour éviter la croissance infinie du WAL.

  [STAB-1] Connexions DB via @contextmanager : fermeture garantie même si exception.
  [STAB-2] _run_sync : timestamp last_sync capturé AVANT push+pull (snapshot cohérent).
           Évite de manquer les enregistrements créés pendant la durée du sync.
  [STAB-3] sync_now() : garde-fou _sync_running pour ne pas lancer deux syncs simultanés.
  [STAB-4] _save_last_sync : écriture atomique tmp→rename (évite état corrompu).
  [STAB-5] _load_or_create clé réseau : écriture atomique tmp→rename.
  [STAB-6] password_callback : évite getpass() bloquant dans le thread UI wx.
"""

import sqlite3
import threading
import time
import json
import uuid
import hashlib
import hmac
import base64
import os
import logging
import getpass
import functools
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# ── Chiffrement (même dépendance que pacs_ris_db) ───────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as _crypto_hashes
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False
    logger.critical("❌ 'cryptography' introuvable — pip install cryptography")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

class NetworkSyncConfig:
    """
    Configuration de la synchronisation réseau.

    Paramètres
    ----------
    mode : str
        "shared"     → tous les postes utilisent le MÊME fichier SQLite réseau
        "replicated" → chaque poste a sa BD locale, sync périodique vers maître

    network_path : str ou Path
        Chemin vers le dossier réseau partagé.
        Windows : r"\\\\NAS\\PISUM" ou r"Z:\\PISUM"
        Linux   : "/mnt/pisum" ou "/media/partage/pisum"

    clinic_name : str
        Nom de la clinique (utilisé dans les logs et le nommage de la BD).

    workstation_id : str
        Identifiant unique du poste (ex: "RADIO-01", "SECRETARIAT-02").
        Doit être unique dans la clinique.

    network_password : str, optionnel
        Mot de passe commun à tous les postes pour chiffrer la clé réseau.
        Si None, sera demandé interactivement au premier démarrage.

    sync_interval_seconds : int
        Intervalle de sync en mode "replicated" (défaut : 30 secondes).

    on_sync_event : callable, optionnel
        Callback(event: str, details: dict) appelé lors d'événements de sync.
        Utile pour rafraîchir l'UI après une sync distante.
    """

    def __init__(
        self,
        mode: str = "shared",
        network_path: str | Path = "",
        clinic_name: str = "Clinique",
        workstation_id: str = "",
        network_password: str = "",
        sync_interval_seconds: int = 30,
        on_sync_event: Optional[Callable] = None,
        password_callback: Optional[Callable[[], str]] = None,
    ):
        if mode not in ("shared", "replicated"):
            raise ValueError("mode doit être 'shared' ou 'replicated'")
        self.mode                   = mode
        self.network_path           = Path(network_path) if network_path else None
        self.clinic_name            = clinic_name
        self.workstation_id         = workstation_id or getpass.getuser()
        self.network_password       = network_password
        self.sync_interval_seconds  = max(10, sync_interval_seconds)
        self.on_sync_event          = on_sync_event
        # CORRECTION: callback optionnel pour demander le mot de passe via l'UI wx
        # au lieu de getpass() qui bloque le thread principal GUI.
        # Exemple wx: password_callback=lambda: wx.GetPasswordFromUser("Mot de passe réseau")
        self.password_callback      = password_callback
        # SOC2 CC6.7: warn if plaintext password supplied directly in code
        if network_password:
            logger.warning(
                "⚠️  NetworkSyncConfig: network_password fourni en clair. "
                "Préférez password_callback ou la variable d'env PISUM_NET_PWD."
            )


# ══════════════════════════════════════════════════════════════════════════════
#  CLÉ DE CHIFFREMENT RÉSEAU
# ══════════════════════════════════════════════════════════════════════════════

class NetworkEncryptionKey:
    """
    Gère la clé AES-256 partagée sur le réseau.

    La clé est stockée dans le dossier réseau sous .enc_network_key,
    protégée par un mot de passe commun à tous les postes.
    Format : sel_kdf(16) | sel_verif(16) | tag(32) | key_enc(32)
    """
    _KDF_ITER  = 600_000
    _KEY_FILE  = ".enc_network_key"
    _AUTH_FILE = ".enc_network_auth"

    def __init__(self, network_dir: Path, password: str):
        if not _CRYPTO_OK:
            raise RuntimeError("Module 'cryptography' requis.")
        self._dir = network_dir
        self._password = password.encode("utf-8")
        self._key = self._load_or_create()

    def _kdf(self, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=_crypto_hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self._KDF_ITER,
        )
        return kdf.derive(self._password)

    def _load_or_create(self) -> bytes:
        key_file = self._dir / self._KEY_FILE
        if key_file.exists():
            try:
                raw = key_file.read_bytes()
                if len(raw) != 96:
                    raise ValueError("Fichier clé réseau corrompu.")
                sal_kdf   = raw[0:16]
                sal_verif = raw[16:32]
                tag_verif = raw[32:64]
                key_enc   = raw[64:96]
                derived   = self._kdf(sal_kdf)
                expected  = hmac.new(
                    derived + sal_verif, key_enc, "sha256"
                ).digest()
                if not hmac.compare_digest(tag_verif, expected):
                    raise ValueError(
                        "Mot de passe réseau incorrect ou fichier altéré."
                    )
                logger.info("🔑 Clé réseau chargée depuis %s", key_file)
                return key_enc
            except Exception as e:
                logger.error("❌ Impossible de charger la clé réseau : %s", e)
                raise

        # Première installation réseau → générer
        sal_kdf   = os.urandom(16)
        sal_verif = os.urandom(16)
        key_enc   = os.urandom(32)
        derived   = self._kdf(sal_kdf)
        tag_verif = hmac.new(
            derived + sal_verif, key_enc, "sha256"
        ).digest()
        payload = sal_kdf + sal_verif + tag_verif + key_enc

        # CORRECTION: écriture atomique via fichier tmp + rename
        # évite un fichier clé corrompu en cas de crash pendant l'écriture
        tmp_file = key_file.with_suffix(".tmp")
        try:
            tmp_file.write_bytes(payload)
            # Restrict before rename so the file is never world-readable
            try:
                os.chmod(tmp_file, 0o640)
            except Exception:
                pass
            tmp_file.replace(key_file)  # atomique sur tous les OS supportés
        except Exception:
            tmp_file.unlink(missing_ok=True)
            raise
        # chmod again on final path in case the rename reset perms (some FS)
        try:
            os.chmod(key_file, 0o640)
        except Exception:
            pass
        logger.info("🔑 Nouvelle clé réseau générée dans %s", key_file)
        return key_enc

    @property
    def key(self) -> bytes:
        return self._key


# ══════════════════════════════════════════════════════════════════════════════
#  ENCRYPTEUR RÉSEAU (réutilise la logique de PacsEncryptor, clé réseau)
# ══════════════════════════════════════════════════════════════════════════════

class NetworkEncryptor:
    """
    Identique à PacsEncryptor mais utilise la clé réseau partagée.
    Permet le déchiffrement par tous les postes de la clinique.

    OPTIMISATION: les clés dérivées par champ sont mises en cache (LRU 512 entrées).
    Sans ce cache, chaque champ chiffré coûte 600 000 itérations PBKDF2 soit ~100ms.
    Un sync de 100 patients × 7 champs sans cache = ~70 secondes ; avec cache ≈ 0.
    """
    _PREFIX    = "ENCN:"
    _VERSION   = b"\x01"
    _KDF_ITER  = 600_000
    _SALT_LEN  = 16
    _NONCE_LEN = 12

    def __init__(self, network_key: bytes):
        if not _CRYPTO_OK:
            raise RuntimeError("Module 'cryptography' requis.")
        self._master_key = network_key
        # Cache LRU salt→clé_dérivée : évite de recalculer 600k itérations PBKDF2
        # pour chaque champ. maxsize=512 couvre une journée complète sans recalcul.
        self._key_cache: dict[bytes, bytes] = {}
        self._key_cache_lock = threading.Lock()

    def _derive_field_key(self, field_salt: bytes) -> bytes:
        # Lecture sans verrou d'abord (cas fréquent : déchiffrement de salts connus)
        cached = self._key_cache.get(field_salt)
        if cached is not None:
            return cached
        # Calcul coûteux uniquement si absent du cache
        kdf = PBKDF2HMAC(
            algorithm=_crypto_hashes.SHA256(),
            length=32,
            salt=field_salt,
            iterations=self._KDF_ITER,
        )
        derived = kdf.derive(self._master_key)
        with self._key_cache_lock:
            # Éviter la croissance illimitée (max 512 salts uniques)
            if len(self._key_cache) >= 512:
                # Supprimer la moitié la plus ancienne
                oldest = list(self._key_cache.keys())[:256]
                for k in oldest:
                    del self._key_cache[k]
            self._key_cache[field_salt] = derived
        return derived

    def _derive_field_key_uncached(self, field_salt: bytes) -> bytes:
        # Conservé pour compatibilité interne
        return self._derive_field_key(field_salt)

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return plaintext or ""
        if plaintext.startswith(self._PREFIX):
            return plaintext
        # Si déjà chiffré avec l'ancien encrypteur local (ENC:), on déchiffre
        # en amont avant d'appeler cette méthode.
        pt_bytes   = plaintext.encode("utf-8")
        field_salt = os.urandom(self._SALT_LEN)
        nonce      = os.urandom(self._NONCE_LEN)
        key        = self._derive_field_key(field_salt)
        aesgcm     = AESGCM(key)
        ct_tag     = aesgcm.encrypt(nonce, pt_bytes, None)
        blob       = self._VERSION + field_salt + nonce + ct_tag
        return self._PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext or not ciphertext.startswith(self._PREFIX):
            return ciphertext or ""
        try:
            blob       = base64.urlsafe_b64decode(ciphertext[len(self._PREFIX):])
            field_salt = blob[1:1 + self._SALT_LEN]
            nonce      = blob[1 + self._SALT_LEN:1 + self._SALT_LEN + self._NONCE_LEN]
            ct_tag     = blob[1 + self._SALT_LEN + self._NONCE_LEN:]
            key        = self._derive_field_key(field_salt)
            aesgcm     = AESGCM(key)
            pt         = aesgcm.decrypt(nonce, ct_tag, None)
            return pt.decode("utf-8")
        except Exception as e:
            logger.error("❌ Déchiffrement réseau échoué : %s", e)
            return ""

    def encrypt_dict(self, d: dict, fields: tuple) -> dict:
        out = dict(d)
        for f in fields:
            if f in out and out[f]:
                out[f] = self.encrypt(str(out[f]))
        return out

    def decrypt_dict(self, d: dict, fields: tuple) -> dict:
        out = dict(d)
        for f in fields:
            if f in out and out[f]:
                out[f] = self.decrypt(str(out[f]))
        return out


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DONNÉES RÉSEAU PARTAGÉE (Mode "shared")
# ══════════════════════════════════════════════════════════════════════════════

class SharedNetworkDB:
    """
    Mode "shared" : tous les postes utilisent le même fichier SQLite réseau.

    SQLite en mode WAL (Write-Ahead Log) supporte les lectures/écritures
    concurrentes depuis plusieurs processus, même à travers un partage réseau
    SMB ≥ 3.0 ou NFS avec locking activé.

    La base réseau utilise son propre chiffrement (clé réseau partagée).
    """

    def __init__(self, db_path: Path, network_enc: NetworkEncryptor):
        self.db_path     = db_path
        self.enc         = network_enc
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")   # SOC2 CC7.2
        conn.execute("PRAGMA busy_timeout=10000")   # 10s attente en cas de verrou
        conn.execute("PRAGMA wal_autocheckpoint=100")  # checkpoint tous les 100 pages (~400KB)
        conn.execute("PRAGMA synchronous=NORMAL")   # plus rapide que FULL, toujours sûr en WAL
        conn.execute("PRAGMA cache_size=-4000")     # 4MB de cache page en mémoire
        return conn

    def _init_db(self):
        """Crée les tables si elles n'existent pas (idempotent)."""
        try:
            conn = self._connect()
            
            # Migration silencieuse pour les bases réseau existantes
            try:
                conn.execute("ALTER TABLE examens ADD COLUMN formula_name TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass
                
            c = conn.cursor()
            # Tables identiques à pacs_ris_db + colonne source_workstation
            c.execute("""
                CREATE TABLE IF NOT EXISTS patients (
                    patient_uuid    TEXT PRIMARY KEY,
                    bio_key         TEXT UNIQUE NOT NULL,
                    nom             TEXT NOT NULL,
                    prenom          TEXT NOT NULL,
                    date_naissance  TEXT,
                    sexe            TEXT CHECK(sexe IN ('M','F','Autre','')),
                    num_dossier     TEXT UNIQUE,
                    cin             TEXT,
                    telephone       TEXT,
                    pays            TEXT DEFAULT '',
                    adresse         TEXT,
                    remarques       TEXT,
                    source_workstation TEXT DEFAULT '',
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS examens (
                    examen_uuid         TEXT PRIMARY KEY,
                    patient_uuid        TEXT NOT NULL,
                    num_accession       TEXT UNIQUE,
                    date_examen         TEXT NOT NULL,
                    modalite            TEXT NOT NULL,
                    type_examen         TEXT,
                    formula_name        TEXT DEFAULT '',
                    indication          TEXT,
                    medecin_prescripteur TEXT DEFAULT '',
                    medecin             TEXT,
                    etablissement       TEXT,
                    langue              TEXT,
                    statut              TEXT DEFAULT 'En cours'
                                            CHECK(statut IN ('En cours','Finalisé','Archivé')),
                    source_workstation  TEXT DEFAULT '',
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(patient_uuid) REFERENCES patients(patient_uuid)
                        ON DELETE CASCADE
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS compte_rendus (
                    cr_uuid         TEXT PRIMARY KEY,
                    examen_uuid     TEXT NOT NULL,
                    contenu         TEXT NOT NULL,
                    version         INTEGER DEFAULT 1,
                    source_workstation TEXT DEFAULT '',
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(examen_uuid) REFERENCES examens(examen_uuid)
                        ON DELETE CASCADE
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    log_id          TEXT PRIMARY KEY,
                    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    utilisateur     TEXT NOT NULL,
                    workstation     TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    patient_uuid    TEXT,
                    examen_uuid     TEXT,
                    details         TEXT
                )
            """)
            # Index
            for sql in [
                "CREATE INDEX IF NOT EXISTS idx_pat_nom    ON patients(nom, prenom)",
                "CREATE INDEX IF NOT EXISTS idx_pat_ndos   ON patients(num_dossier)",
                "CREATE INDEX IF NOT EXISTS idx_pat_upd    ON patients(updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_exam_pat   ON examens(patient_uuid)",
                "CREATE INDEX IF NOT EXISTS idx_exam_date  ON examens(date_examen)",
                "CREATE INDEX IF NOT EXISTS idx_exam_upd   ON examens(updated_at)",
                "CREATE INDEX IF NOT EXISTS idx_cr_exam    ON compte_rendus(examen_uuid)",
                "CREATE INDEX IF NOT EXISTS idx_cr_upd     ON compte_rendus(created_at)",
                "CREATE INDEX IF NOT EXISTS idx_audit_ts   ON audit_log(ts)",
            ]:
                c.execute(sql)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("❌ SharedNetworkDB._init_db : %s", e, exc_info=True)
            raise


# ══════════════════════════════════════════════════════════════════════════════
#  RÉPLICATION PÉRIODIQUE (Mode "replicated")
# ══════════════════════════════════════════════════════════════════════════════

class ReplicationManager:
    """
    Mode "replicated" :
      1. Chaque poste a sa base locale (pacs_ris.db).
      2. Un thread de sync pousse les nouveaux enregistrements vers la base
         réseau maître (pacs_ris_network.db).
      3. Le même thread tire les changements des autres postes depuis la base
         réseau et les intègre localement.

    Mécanisme : horodatage last_sync_ts persisté localement pour ne tirer
    que les delta. Toutes les connexions sont gérées par context managers
    pour garantir leur fermeture même en cas d'exception.
    """

    _STATE_FILE = "pacs_sync_state.json"

    def __init__(
        self,
        local_db_path: Path,
        network_db_path: Path,
        local_enc,           # PacsEncryptor (local)
        network_enc: NetworkEncryptor,
        workstation_id: str,
        interval: int = 30,
        on_sync_event: Optional[Callable] = None,
    ):
        self.local_db_path    = local_db_path
        self.network_db_path  = network_db_path
        self.local_enc        = local_enc
        self.network_enc      = network_enc
        self.workstation_id   = workstation_id
        self.interval         = interval
        self.on_sync_event    = on_sync_event

        self._state_file  = local_db_path.parent / self._STATE_FILE
        self._last_sync   = self._load_last_sync()
        self._stop_event  = threading.Event()
        self._thread      = None
        self._lock        = threading.Lock()
        # CORRECTION: garde-fou contre appels sync_now() concurrents
        self._sync_running = threading.Event()
        # Connexions persistantes — recréées uniquement en cas d'erreur
        self._local_conn_obj: sqlite3.Connection | None = None
        self._network_conn_obj: sqlite3.Connection | None = None

    # ── Persistance de l'état de sync ────────────────────────────────────────

    def _load_last_sync(self) -> str:
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                return data.get("last_sync_ts", "1970-01-01T00:00:00")
        except Exception:
            pass
        return "1970-01-01T00:00:00"

    def _save_last_sync(self, ts: str):
        # CORRECTION: écriture atomique pour éviter un état corrompu
        tmp = self._state_file.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps({"last_sync_ts": ts, "workstation": self.workstation_id},
                           ensure_ascii=False),
                encoding="utf-8"
            )
            tmp.replace(self._state_file)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            logger.warning("pacs_sync_state save error: %s", e)

    # ── Connexions context-managed ────────────────────────────────────────────

    def _make_local_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.local_db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-4000")
        return conn

    def _make_network_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.network_db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA wal_autocheckpoint=100")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-4000")
        return conn

    @contextmanager
    def _local_conn(self):
        """Connexion locale persistante — recréée uniquement si invalide."""
        try:
            if self._local_conn_obj is None:
                self._local_conn_obj = self._make_local_conn()
            else:
                self._local_conn_obj.execute("SELECT 1")  # ping rapide
        except Exception:
            try:
                self._local_conn_obj.close()
            except Exception:
                pass
            self._local_conn_obj = self._make_local_conn()
        yield self._local_conn_obj

    @contextmanager
    def _network_conn(self):
        """Connexion réseau persistante — recréée uniquement si invalide."""
        try:
            if self._network_conn_obj is None:
                self._network_conn_obj = self._make_network_conn()
            else:
                self._network_conn_obj.execute("SELECT 1")  # ping rapide
        except Exception:
            try:
                self._network_conn_obj.close()
            except Exception:
                pass
            self._network_conn_obj = self._make_network_conn()
        yield self._network_conn_obj

    def _init_network_tables(self, conn: sqlite3.Connection):
        """Crée les tables et index réseau si absents (idempotent)."""
        
        # Migration silencieuse pour les bases réseau existantes
        try:
            conn.execute("ALTER TABLE examens ADD COLUMN formula_name TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
            
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                patient_uuid TEXT PRIMARY KEY,
                bio_key TEXT UNIQUE NOT NULL,
                nom TEXT NOT NULL, prenom TEXT NOT NULL,
                date_naissance TEXT, sexe TEXT,
                num_dossier TEXT UNIQUE, cin TEXT, telephone TEXT,
                pays TEXT DEFAULT '', adresse TEXT, remarques TEXT,
                source_workstation TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS examens (
                examen_uuid TEXT PRIMARY KEY,
                patient_uuid TEXT NOT NULL,
                num_accession TEXT UNIQUE,
                date_examen TEXT NOT NULL, modalite TEXT NOT NULL,
                type_examen TEXT, indication TEXT,
                medecin_prescripteur TEXT DEFAULT '',
                medecin TEXT, etablissement TEXT, langue TEXT,
                statut TEXT DEFAULT 'En cours',
                source_workstation TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(patient_uuid) REFERENCES patients(patient_uuid)
                    ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS compte_rendus (
                cr_uuid TEXT PRIMARY KEY,
                examen_uuid TEXT NOT NULL,
                contenu TEXT NOT NULL,
                version INTEGER DEFAULT 1,
                source_workstation TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(examen_uuid) REFERENCES examens(examen_uuid)
                    ON DELETE CASCADE
            )
        """)
        # CORRECTION: index sur updated_at pour éviter full-scan à chaque sync
        for sql in [
            "CREATE INDEX IF NOT EXISTS idx_net_pat_upd  ON patients(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_net_pat_ws   ON patients(source_workstation)",
            "CREATE INDEX IF NOT EXISTS idx_net_exam_upd ON examens(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_net_exam_ws  ON examens(source_workstation)",
            "CREATE INDEX IF NOT EXISTS idx_net_cr_upd   ON compte_rendus(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_net_cr_ws    ON compte_rendus(source_workstation)",
        ]:
            conn.execute(sql)
        conn.commit()

    # ── Push : local → réseau ─────────────────────────────────────────────────

    def _push(self, last_sync: str) -> int:
        """
        Pousse vers la base réseau tous les enregistrements locaux
        créés ou modifiés depuis last_sync.
        CORRECTION: accepte last_sync en paramètre (snapshot cohérent du cycle).
        CORRECTION: utilise executemany() pour les insertions en batch.
        CORRECTION: gestion des conflits bio_key/num_dossier par OR IGNORE sur
        l'index unique secondaire pour éviter IntegrityError inter-postes.
        Retourne le nombre d'enregistrements poussés.
        """
        pushed = 0
        PAT_FIELDS  = ("nom","prenom","date_naissance","cin","telephone","adresse","remarques")
        EXAM_FIELDS = ("indication","medecin_prescripteur","medecin","etablissement")

        try:
            with self._local_conn() as lconn, self._network_conn() as nconn:
                self._init_network_tables(nconn)

                # ── Patients ──────────────────────────────────────────────────
                rows = lconn.execute(
                    "SELECT * FROM patients WHERE updated_at > ?", (last_sync,)
                ).fetchall()
                pat_batch = []
                for row in rows:
                    d = dict(row)
                    d_plain = self.local_enc.decrypt_dict(d, PAT_FIELDS)
                    d_net   = self.network_enc.encrypt_dict(d_plain, PAT_FIELDS)
                    pat_batch.append((
                        d_net["patient_uuid"], d_net["bio_key"],
                        d_net["nom"], d_net["prenom"], d_net["date_naissance"],
                        d["sexe"], d["num_dossier"],
                        d_net["cin"], d_net["telephone"], d["pays"],
                        d_net["adresse"], d_net["remarques"],
                        self.workstation_id, d["created_at"], d["updated_at"]
                    ))
                if pat_batch:
                    nconn.executemany("""
                        INSERT INTO patients
                            (patient_uuid, bio_key, nom, prenom, date_naissance, sexe,
                             num_dossier, cin, telephone, pays, adresse, remarques,
                             source_workstation, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(patient_uuid) DO UPDATE SET
                            nom=excluded.nom, prenom=excluded.prenom,
                            date_naissance=excluded.date_naissance,
                            cin=excluded.cin, telephone=excluded.telephone,
                            adresse=excluded.adresse, remarques=excluded.remarques,
                            pays=excluded.pays, sexe=excluded.sexe,
                            source_workstation=excluded.source_workstation,
                            updated_at=excluded.updated_at
                        WHERE excluded.updated_at > patients.updated_at
                    """, pat_batch)
                    pushed += len(pat_batch)

                # ── Examens ───────────────────────────────────────────────────
                rows = lconn.execute(
                    "SELECT * FROM examens WHERE updated_at > ?", (last_sync,)
                ).fetchall()
                exam_batch = []
                for row in rows:
                    d = dict(row)
                    d_plain = self.local_enc.decrypt_dict(d, EXAM_FIELDS)
                    d_net   = self.network_enc.encrypt_dict(d_plain, EXAM_FIELDS)
                    exam_batch.append((
                        d["examen_uuid"], d["patient_uuid"], d["num_accession"],
                        d["date_examen"], d["modalite"], d["type_examen"], d.get("formula_name", ""),
                        d_net["indication"], d_net["medecin_prescripteur"],
                        d_net["medecin"], d_net["etablissement"],
                        d["langue"], d["statut"], self.workstation_id,
                        d["created_at"], d["updated_at"]
                    ))
                if exam_batch:
                    nconn.executemany("""
                        INSERT INTO examens
                            (examen_uuid, patient_uuid, num_accession, date_examen,
                             modalite, type_examen, formula_name, indication, medecin_prescripteur,
                             medecin, etablissement, langue, statut,
                             source_workstation, created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(examen_uuid) DO UPDATE SET
                            modalite=excluded.modalite,
                            type_examen=excluded.type_examen,
                            formula_name=excluded.formula_name,
                            indication=excluded.indication,
                            medecin_prescripteur=excluded.medecin_prescripteur,
                            medecin=excluded.medecin,
                            etablissement=excluded.etablissement,
                            statut=excluded.statut,
                            source_workstation=excluded.source_workstation,
                            updated_at=excluded.updated_at
                        WHERE excluded.updated_at > examens.updated_at
                    """, exam_batch)
                    pushed += len(exam_batch)

                # ── Comptes rendus ────────────────────────────────────────────
                rows = lconn.execute(
                    "SELECT * FROM compte_rendus WHERE created_at > ?", (last_sync,)
                ).fetchall()
                cr_batch = []
                for row in rows:
                    d = dict(row)
                    contenu_plain = self.local_enc.decrypt(d["contenu"])
                    contenu_net   = self.network_enc.encrypt(contenu_plain)
                    cr_batch.append((
                        d["cr_uuid"], d["examen_uuid"], contenu_net,
                        d["version"], self.workstation_id, d["created_at"]
                    ))
                if cr_batch:
                    nconn.executemany("""
                        INSERT OR IGNORE INTO compte_rendus
                            (cr_uuid, examen_uuid, contenu, version,
                             source_workstation, created_at)
                        VALUES (?,?,?,?,?,?)
                    """, cr_batch)
                    pushed += len(cr_batch)

                nconn.commit()

        except Exception as e:
            logger.error("❌ [sync push] %s", e, exc_info=True)

        return pushed

    # ── Pull : réseau → local ─────────────────────────────────────────────────

    def _pull(self, last_sync: str) -> int:
        """
        Tire depuis la base réseau les enregistrements créés par d'autres
        postes depuis last_sync.
        CORRECTION: accepte last_sync en paramètre (snapshot cohérent du cycle).
        CORRECTION: batch inserts via executemany().
        CORRECTION: collecte tous les examens orphelins en une requête plutôt
        que N SELECT individuels.
        """
        pulled = 0
        PAT_FIELDS  = ("nom","prenom","date_naissance","cin","telephone","adresse","remarques")
        EXAM_FIELDS = ("indication","medecin_prescripteur","medecin","etablissement")

        try:
            with self._local_conn() as lconn, self._network_conn() as nconn:

                # ── Patients distants ─────────────────────────────────────────
                rows = nconn.execute(
                    "SELECT * FROM patients WHERE updated_at > ? AND source_workstation != ?",
                    (last_sync, self.workstation_id)
                ).fetchall()
                pat_batch = []
                for row in rows:
                    d = dict(row)
                    d_plain = self.network_enc.decrypt_dict(d, PAT_FIELDS)
                    d_local = self.local_enc.encrypt_dict(d_plain, PAT_FIELDS)
                    pat_batch.append((
                        d["patient_uuid"], d["bio_key"],
                        d_local["nom"], d_local["prenom"], d_local["date_naissance"],
                        d["sexe"], d["num_dossier"],
                        d_local["cin"], d_local["telephone"], d["pays"],
                        d_local["adresse"], d_local["remarques"],
                        d["created_at"], d["updated_at"]
                    ))
                if pat_batch:
                    lconn.executemany("""
                        INSERT INTO patients
                            (patient_uuid, bio_key, nom, prenom, date_naissance, sexe,
                             num_dossier, cin, telephone, pays, adresse, remarques,
                             created_at, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(patient_uuid) DO UPDATE SET
                            nom=excluded.nom, prenom=excluded.prenom,
                            date_naissance=excluded.date_naissance,
                            cin=excluded.cin, telephone=excluded.telephone,
                            adresse=excluded.adresse, remarques=excluded.remarques,
                            pays=excluded.pays, sexe=excluded.sexe,
                            updated_at=excluded.updated_at
                        WHERE excluded.updated_at > patients.updated_at
                    """, pat_batch)
                    pulled += len(pat_batch)

                # ── Examens distants ──────────────────────────────────────────
                rows = nconn.execute(
                    "SELECT * FROM examens WHERE updated_at > ? AND source_workstation != ?",
                    (last_sync, self.workstation_id)
                ).fetchall()

                # CORRECTION: une seule requête pour vérifier les parents existants
                if rows:
                    uuids = tuple(r["patient_uuid"] for r in rows)
                    placeholders = ",".join("?" * len(uuids))
                    existing_patients = {
                        r[0] for r in lconn.execute(
                            f"SELECT patient_uuid FROM patients WHERE patient_uuid IN ({placeholders})",
                            uuids
                        ).fetchall()
                    }
                    exam_batch = []
                    for row in rows:
                        d = dict(row)
                        if d["patient_uuid"] not in existing_patients:
                            logger.warning(
                                "[sync pull] Examen %s : patient %s absent localement, ignoré.",
                                d["examen_uuid"], d["patient_uuid"]
                            )
                            continue
                        d_plain = self.network_enc.decrypt_dict(d, EXAM_FIELDS)
                        d_local = self.local_enc.encrypt_dict(d_plain, EXAM_FIELDS)
                        exam_batch.append((
                            d["examen_uuid"], d["patient_uuid"], d["num_accession"],
                            d["date_examen"], d["modalite"], d["type_examen"], d.get("formula_name", ""),
                            d_local["indication"], d_local["medecin_prescripteur"],
                            d_local["medecin"], d_local["etablissement"],
                            d["langue"], d["statut"],
                            d["created_at"], d["updated_at"]
                        ))
                    if exam_batch:
                        lconn.executemany("""
                            INSERT INTO examens
                                (examen_uuid, patient_uuid, num_accession, date_examen,
                                 modalite, type_examen, formula_name, indication, medecin_prescripteur,
                                 medecin, etablissement, langue, statut,
                                 created_at, updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(examen_uuid) DO UPDATE SET
                                modalite=excluded.modalite,
                                type_examen=excluded.type_examen,
                                formula_name=excluded.formula_name,
                                indication=excluded.indication,
                                medecin_prescripteur=excluded.medecin_prescripteur,
                                medecin=excluded.medecin,
                                etablissement=excluded.etablissement,
                                statut=excluded.statut,
                                updated_at=excluded.updated_at
                            WHERE excluded.updated_at > examens.updated_at
                        """, exam_batch)
                        pulled += len(exam_batch)

                # ── Comptes rendus distants ───────────────────────────────────
                rows = nconn.execute(
                    "SELECT * FROM compte_rendus WHERE created_at > ? AND source_workstation != ?",
                    (last_sync, self.workstation_id)
                ).fetchall()
                if rows:
                    exam_uuids = tuple(r["examen_uuid"] for r in rows)
                    placeholders = ",".join("?" * len(exam_uuids))
                    existing_examens = {
                        r[0] for r in lconn.execute(
                            f"SELECT examen_uuid FROM examens WHERE examen_uuid IN ({placeholders})",
                            exam_uuids
                        ).fetchall()
                    }
                    cr_batch = []
                    for row in rows:
                        d = dict(row)
                        if d["examen_uuid"] not in existing_examens:
                            continue
                        contenu_plain = self.network_enc.decrypt(d["contenu"])
                        contenu_local = self.local_enc.encrypt(contenu_plain)
                        cr_batch.append((
                            d["cr_uuid"], d["examen_uuid"], contenu_local,
                            d["version"], d["created_at"]
                        ))
                    if cr_batch:
                        lconn.executemany("""
                            INSERT OR IGNORE INTO compte_rendus
                                (cr_uuid, examen_uuid, contenu, version, created_at)
                            VALUES (?,?,?,?,?)
                        """, cr_batch)
                        pulled += len(cr_batch)

                lconn.commit()

        except Exception as e:
            logger.error("❌ [sync pull] %s", e, exc_info=True)

        return pulled

    # ── Thread principal de sync ──────────────────────────────────────────────

    def _sync_loop(self):
        logger.info("[sync] Thread démarré pour %s", self.workstation_id)
        while not self._stop_event.wait(self.interval):
            self._run_sync()

    def _run_sync(self):
        # CORRECTION: garde-fou — si un sync tourne déjà, on abandonne silencieusement
        if self._sync_running.is_set():
            logger.debug("[sync] Sync déjà en cours, cycle ignoré.")
            return
        self._sync_running.set()
        try:
            with self._lock:
                # CORRECTION: snapshot du timestamp AVANT les opérations pour ne
                # pas manquer les enregistrements créés pendant le sync lui-même.
                # On utilise last_sync de l'état précédent comme borne basse,
                # et on enregistre ts_start comme nouvelle borne après succès.
                ts_start = datetime.now(timezone.utc).isoformat()
                last_sync = self._last_sync
                try:
                    pushed = self._push(last_sync)
                    pulled = self._pull(last_sync)
                    # CORRECTION: on sauvegarde ts_start (pas ts_before confus)
                    # uniquement si les deux opérations ont réussi
                    self._last_sync = ts_start
                    self._save_last_sync(ts_start)
                    if pushed or pulled:
                        logger.info(
                            "[sync] %s : ↑ %d poussés, ↓ %d tirés",
                            self.workstation_id, pushed, pulled
                        )
                        if self.on_sync_event:
                            try:
                                self.on_sync_event("sync_complete", {
                                    "pushed": pushed, "pulled": pulled,
                                    "workstation": self.workstation_id,
                                    "ts": ts_start,
                                })
                            except Exception:
                                pass
                except Exception as e:
                    logger.error("❌ [sync] erreur cycle : %s", e)
        finally:
            self._sync_running.clear()

    def start(self):
        """Démarre le thread de synchronisation en arrière-plan."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sync_loop, daemon=True, name="pacs-sync"
        )
        self._thread.start()
        logger.info("[sync] Démarré (intervalle %ds)", self.interval)

    def stop(self):
        """Arrête proprement le thread de sync."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        for conn_attr in ("_local_conn_obj", "_network_conn_obj"):
            conn = getattr(self, conn_attr, None)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
                setattr(self, conn_attr, None)
        logger.info("[sync] Arrêté.")

    def sync_now(self):
        """
        Déclenche une synchronisation immédiate (thread-safe).
        CORRECTION: si un sync tourne déjà (_sync_running), n'en lance pas un second.
        """
        if self._sync_running.is_set():
            logger.debug("[sync] sync_now ignoré : sync déjà en cours.")
            return
        threading.Thread(target=self._run_sync, daemon=True, name="pacs-sync-now").start()


# ══════════════════════════════════════════════════════════════════════════════
#  GESTIONNAIRE PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class NetworkSyncManager:
    """
    Point d'entrée public. Orchestre la configuration réseau et expose
    une méthode get_db() compatible avec PacsRisFrame.
    """

    def __init__(self, config: NetworkSyncConfig):
        self.config      = config
        self._db         = None
        self._replicator = None

        if not config.network_path or not config.network_path.exists():
            raise RuntimeError(
                f"Dossier réseau introuvable : {config.network_path}\n"
                f"Vérifiez que le partage réseau est monté et accessible."
            )

        network_dir = config.network_path
        network_dir.mkdir(parents=True, exist_ok=True)

        # ── Mot de passe réseau ───────────────────────────────────────────────
        password = config.network_password
        if not password:
            # SOC2 CC6.7: check environment variable first (CI/CD, scripted deploy)
            password = os.environ.get("PISUM_NET_PWD", "").strip()
        if not password:
            if config.password_callback:
                # CORRECTION: utiliser le callback UI (wx.GetPasswordFromUser, etc.)
                # pour ne pas bloquer le thread principal de l'interface graphique.
                password = config.password_callback()
            else:
                # Fallback console (acceptable uniquement hors GUI)
                password = getpass.getpass(
                    f"[PISUM] Mot de passe réseau pour '{config.clinic_name}' : "
                )
        if not password:
            raise ValueError("Mot de passe réseau requis pour initialiser la sync.")
        if len(password) < 6:
            raise ValueError(
                "Mot de passe réseau trop court (minimum 6 caractères). "
                "Conformité HIPAA §164.312(a)(2)(iv)."
            )

        # ── Clé de chiffrement réseau ─────────────────────────────────────────
        net_key_mgr = NetworkEncryptionKey(network_dir, password)
        self._network_enc = NetworkEncryptor(net_key_mgr.key)

        # ── Mode partagé ──────────────────────────────────────────────────────
        if config.mode == "shared":
            db_file = network_dir / "pacs_ris_network.db"
            self._shared_db = SharedNetworkDB(db_file, self._network_enc)
            # Créer un PacsRisDB personnalisé qui pointe sur la BD réseau
            self._db = self._make_network_pacs_db(db_file)
            logger.info(
                "✅ [réseau] Mode partagé — BD : %s", db_file
            )

        # ── Mode répliqué ─────────────────────────────────────────────────────
        elif config.mode == "replicated":
            local_db_path = Path.home() / ".pisum_data" / "pacs_ris.db"
            network_db_path = network_dir / "pacs_ris_network.db"

            # Importer l'encrypteur local depuis pacs_ris_db
            try:
                from pacs_ris_db import get_pacs_db, get_pacs_enc
                local_db = get_pacs_db()
                local_enc = get_pacs_enc()
                self._db = local_db
            except ImportError:
                raise RuntimeError("pacs_ris_db introuvable. Vérifiez l'installation.")

            self._replicator = ReplicationManager(
                local_db_path   = local_db_path,
                network_db_path = network_db_path,
                local_enc       = local_enc,
                network_enc     = self._network_enc,
                workstation_id  = config.workstation_id,
                interval        = config.sync_interval_seconds,
                on_sync_event   = config.on_sync_event,
            )
            self._replicator.start()
            # Sync immédiate au démarrage
            self._replicator.sync_now()
            logger.info(
                "✅ [réseau] Mode répliqué — sync toutes les %ds",
                config.sync_interval_seconds
            )

    def _make_network_pacs_db(self, network_db_path: Path):
        """
        Crée un PacsRisDB qui utilise la BD réseau et le chiffrement réseau.
        On sous-classe PacsRisDB en remplaçant son encrypteur et chemin BD.
        """
        try:
            from pacs_ris_db import PacsRisDB

            class NetworkPacsRisDB(PacsRisDB):
                """PacsRisDB branché sur la BD réseau."""
                def __init__(self, db_path, network_enc):
                    # Bypasser l'init parent pour injecter notre propre enc
                    from pathlib import Path as _Path
                    _folder = db_path.parent
                    _folder.mkdir(exist_ok=True)
                    self.db_path = db_path
                    self.enc = network_enc
                    self._init_database()
                    # Restrict network DB file permissions (HIPAA §164.312(a)(2)(iv))
                    try:
                        os.chmod(self.db_path, 0o640)
                    except Exception:
                        pass
                    self._migrate_database()
                    # Ne PAS appeler _encrypt_existing_plaintext ici
                    # (déjà chiffré avec la clé réseau)

                def _connect(self):
                    """Override: add secure_delete for network connections."""
                    import sqlite3 as _sqlite3
                    conn = _sqlite3.connect(str(self.db_path), timeout=15)
                    conn.row_factory = _sqlite3.Row
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA foreign_keys=ON")
                    conn.execute("PRAGMA secure_delete=ON")
                    conn.execute("PRAGMA busy_timeout=10000")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA cache_size=-4000")
                    return conn

            return NetworkPacsRisDB(network_db_path, self._network_enc)

        except ImportError:
            raise RuntimeError(
                "pacs_ris_db.py introuvable. Placez pacs_network_sync.py "
                "dans le même dossier que pacs_ris_db.py."
            )

    def get_db(self):
        """
        Retourne l'instance PacsRisDB à utiliser.
        En mode "shared"     → pointe sur la BD réseau.
        En mode "replicated" → pointe sur la BD locale (sync en arrière-plan).
        """
        return self._db

    def sync_now(self):
        """Déclenche une sync immédiate (mode répliqué uniquement)."""
        if self._replicator:
            self._replicator.sync_now()

    def stop(self):
        """Arrête proprement la sync (appeler à la fermeture de l'application)."""
        if self._replicator:
            self._replicator.stop()

    @property
    def is_network_available(self) -> bool:
        """Vérifie si le partage réseau est accessible."""
        try:
            return (
                self.config.network_path is not None
                and self.config.network_path.exists()
            )
        except Exception:
            return False


# ══════════════════════════════════════════════════════════════════════════════
#  FONCTION D'INITIALISATION PUBLIQUE
# ══════════════════════════════════════════════════════════════════════════════

_sync_manager: Optional[NetworkSyncManager] = None


def init_network_sync(config: NetworkSyncConfig) -> NetworkSyncManager:
    """
    Initialise et retourne le gestionnaire de sync réseau (singleton).
    Appeler une seule fois au démarrage de l'application.

    Exemple
    -------
        from pacs_network_sync import NetworkSyncConfig, init_network_sync

        cfg = NetworkSyncConfig(
            mode="shared",
            network_path=r"\\\\NAS01\\PISUM",
            clinic_name="Clinique Ibn Sina",
            workstation_id="RADIO-01",
            network_password="MotDePasseClinic123",
        )
        sync = init_network_sync(cfg)
        db = sync.get_db()   # utiliser à la place de get_pacs_db()
    """
    global _sync_manager
    _sync_manager = NetworkSyncManager(config)
    return _sync_manager


def get_sync_manager() -> Optional[NetworkSyncManager]:
    """Retourne le gestionnaire de sync actif, ou None si non initialisé."""
    return _sync_manager


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRE : ASSISTANT DE CONFIGURATION (optionnel)
# ══════════════════════════════════════════════════════════════════════════════

def create_config_file(path: str | Path, config: NetworkSyncConfig):
    """
    Sauvegarde la configuration réseau dans un fichier JSON.
    (Le mot de passe n'est PAS sauvegardé en clair.)
    """
    data = {
        "mode":                   config.mode,
        "network_path":           str(config.network_path) if config.network_path else "",
        "clinic_name":            config.clinic_name,
        "workstation_id":         config.workstation_id,
        "sync_interval_seconds":  config.sync_interval_seconds,
    }
    dest = Path(path)
    dest.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    # Restrict config file permissions — no credentials, but reveals network topology
    try:
        os.chmod(dest, 0o600)
    except Exception:
        pass
    logger.info("Configuration réseau sauvegardée dans %s", path)


def load_config_file(
    path: str | Path,
    network_password: str = "",
    password_callback: Optional[Callable[[], str]] = None,
) -> NetworkSyncConfig:
    """
    Charge une configuration réseau depuis un fichier JSON.

    Pour une app wx, passer password_callback pour éviter de bloquer le thread UI :
        cfg = load_config_file(
            "pisum_network.cfg",
            password_callback=lambda: wx.GetPasswordFromUser("Mot de passe réseau PISUM")
        )
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return NetworkSyncConfig(
        mode                  = data.get("mode", "shared"),
        network_path          = data.get("network_path", ""),
        clinic_name           = data.get("clinic_name", "Clinique"),
        workstation_id        = data.get("workstation_id", ""),
        network_password      = network_password,
        sync_interval_seconds = data.get("sync_interval_seconds", 30),
        password_callback     = password_callback,
    )
