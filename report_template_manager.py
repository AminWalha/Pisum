# -*- coding: utf-8 -*-
"""
report_template_manager.py — PISUM Structured Report Template Manager
=======================================================================
Manages multilingual, structured report templates stored as JSON.

Template schema
---------------
{
  "id":         str (UUID),
  "name":       str,
  "is_free":    bool,
  "content": {
    "indication":  {"fr": "...", "en": "..."},
    "technique":   {"fr": "...", "en": "..."},
    "resultat":    {"fr": "...", "en": "..."},
    "conclusion":  {"fr": "...", "en": "..."}
  },
  "updated_at": ISO-8601 str
}

Storage
-------
~/.pisum_data/report_templates.json
"""

import json
import uuid
import logging
import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
TEMPLATE_SECTIONS: list[str] = ["indication", "technique", "resultat", "conclusion"]
SUPPORTED_LANGUAGES: list[str] = ["fr", "en"]

DATA_DIR       = Path.home() / ".pisum_data"
TEMPLATES_FILE = DATA_DIR / "report_templates.json"

# Display name → ISO-2 code
_LANG_MAP: dict[str, str] = {
    "français": "fr", "french": "fr", "fr": "fr",
    "english":  "en", "anglais": "en", "en": "en",
}


# ══════════════════════════════════════════════════════════════════════════════
#  TemplateManager
# ══════════════════════════════════════════════════════════════════════════════

class TemplateManager:
    """
    CRUD manager for structured multilingual report templates.

    All templates are persisted in a single JSON file so they survive
    application restarts without a database dependency.

    Usage
    -----
        tm = TemplateManager()

        # Create / update
        tid = tm.save_template(None, {
            "name": "Chest X-Ray Normal",
            "is_free": True,
            "content": {
                "indication":  {"fr": "Douleur thoracique", "en": "Chest pain"},
                "technique":   {"fr": "Radiographie standard PA+profil", "en": "Standard PA+lateral CXR"},
                "resultat":    {"fr": "Parenchyme pulmonaire normal.", "en": "Normal lung parenchyma."},
                "conclusion":  {"fr": "Pas d'anomalie.", "en": "No abnormality."},
            }
        })

        # Read section
        text = tm.get_section(tid, "resultat", "fr")

        # Update one section
        tm.update_section(tid, "conclusion", "en", "No significant finding.")

        # Delete
        tm.delete_template(tid)
    """

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._templates: dict[str, dict] = {}
        self._load_all()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        """Load all templates from the JSON store into memory."""
        if not TEMPLATES_FILE.exists():
            return
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, list):
                self._templates = {t["id"]: t for t in raw if "id" in t}
            else:
                logger.warning("report_templates.json has unexpected format — resetting.")
                self._templates = {}
        except Exception as exc:
            logger.error("TemplateManager._load_all: %s", exc)
            self._templates = {}

    def _save_all(self) -> None:
        """Persist all in-memory templates to the JSON store."""
        try:
            with open(TEMPLATES_FILE, "w", encoding="utf-8") as fh:
                json.dump(
                    list(self._templates.values()),
                    fh, ensure_ascii=False, indent=2,
                )
        except Exception as exc:
            logger.error("TemplateManager._save_all: %s", exc)

    # ── Core API ───────────────────────────────────────────────────────────────

    def load_template(self, template_id: str) -> dict | None:
        """
        Return a deep copy of the template dict, or None if not found.

        Returns a copy so callers cannot accidentally mutate the store.
        """
        t = self._templates.get(template_id)
        return json.loads(json.dumps(t)) if t else None

    def save_template(self, template_id: str | None, data: dict) -> str:
        """
        Upsert a template.  Generates a new UUID if *template_id* is falsy.

        Parameters
        ----------
        template_id : str or None
            Existing ID to overwrite, or None to create a new entry.
        data : dict
            Must contain at least ``"name"`` and ``"content"`` keys.
            Optional: ``"is_free"`` (bool, default False).

        Returns
        -------
        str
            The final template ID (new or existing).
        """
        if not template_id:
            template_id = str(uuid.uuid4())

        entry: dict = {
            "id":         template_id,
            "name":       data.get("name", "Untitled"),
            "is_free":    bool(data.get("is_free", False)),
            "content":    self._normalize_content(data.get("content", {})),
            "updated_at": datetime.datetime.now().isoformat(),
        }
        self._templates[template_id] = entry
        self._save_all()
        return template_id

    def delete_template(self, template_id: str) -> bool:
        """
        Remove a template by ID.

        Returns True if the template existed and was deleted, False otherwise.
        """
        if template_id in self._templates:
            del self._templates[template_id]
            self._save_all()
            return True
        return False

    def list_templates(self, is_free: bool | None = None) -> list[dict]:
        """
        Return all templates (shallow list copy), optionally filtered by plan tier.

        Parameters
        ----------
        is_free : bool or None
            - True  → return only free templates
            - False → return only paid templates
            - None  → return all templates
        """
        items = list(self._templates.values())
        if is_free is not None:
            items = [t for t in items if bool(t.get("is_free")) == is_free]
        return items

    # ── Section helpers ────────────────────────────────────────────────────────

    def get_section(self, template_id: str, section_name: str,
                    language: str) -> str:
        """
        Return the text for a specific section and language.

        Returns an empty string when the template, section, or language is
        not found — never raises.
        """
        t = self._templates.get(template_id)
        if not t:
            return ""
        lang = self._normalize_lang(language)
        return t.get("content", {}).get(section_name, {}).get(lang, "")

    def update_section(self, template_id: str, section_name: str,
                       language: str, text: str) -> bool:
        """
        Overwrite a single section/language slot in an existing template.

        Calls ``_save_all()`` automatically.

        Returns
        -------
        bool
            True on success, False if the template or section is unknown.
        """
        t = self._templates.get(template_id)
        if not t:
            logger.warning("update_section: template %r not found.", template_id)
            return False
        if section_name not in TEMPLATE_SECTIONS:
            logger.warning("update_section: unknown section %r.", section_name)
            return False

        lang = self._normalize_lang(language)
        (t.setdefault("content", {})
          .setdefault(section_name, {}))[lang] = text
        t["updated_at"] = datetime.datetime.now().isoformat()
        self._save_all()
        return True

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_lang(language: str) -> str:
        """Map any display name or code to an ISO-2 key ('fr' / 'en')."""
        return _LANG_MAP.get(language.lower(), language.lower()[:2])

    @staticmethod
    def _normalize_content(content: dict) -> dict:
        """
        Ensure the content dict has all required sections and language keys.
        Missing values default to empty strings.
        """
        result: dict = {}
        for section in TEMPLATE_SECTIONS:
            existing = content.get(section, {})
            if isinstance(existing, str):
                # Accept plain strings and promote to multilingual dict
                existing = {"fr": existing, "en": ""}
            result[section] = {
                lang: existing.get(lang, "")
                for lang in SUPPORTED_LANGUAGES
            }
        return result
