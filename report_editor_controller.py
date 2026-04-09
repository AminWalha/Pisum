# -*- coding: utf-8 -*-
"""
report_editor_controller.py — PISUM Report Editor Controller
=============================================================
Separates business logic from the UI in the structured report editor.

Responsibilities
----------------
- In-memory multilingual content buffer  {section → {lang → text}}
- Async load / save from/to the PACS DB
- Feature gating (structured_reports · multilang · export)
- Section & language switching state
- Hooks for dictation (wired) and AI enhancement (stub ready to fill)

Usage
-----
    ctrl = ReportEditorController(core_state, item)
    ctrl.on_content_ready = lambda buf: view.after(0, lambda: view.fill(buf))
    ctrl.on_save_status   = lambda msg, err: view.after(0, lambda: view.set_status(msg, err))
    ctrl.load_async()

    # User types in text area
    ctrl.update_current_text(textbox.get("1.0", "end-1c"))

    # User switches section tab
    text = ctrl.set_section("technique")

    # User switches language
    text = ctrl.set_language("EN")

    # Save
    ctrl.save()
"""

import json
import logging
import datetime
import threading
import urllib.request
import urllib.error
import re as _re
from typing import Callable

# ── Gemini configuration ──────────────────────────────────────────────────────
_GEMINI_API_KEY = "AIzaSyBTtd39lwC178Fl7Iol2ZnaiiYtTGFkH3I"
_GEMINI_MODEL   = "gemini-2.5-flash-lite"
_GEMINI_URL     = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{_GEMINI_MODEL}:generateContent?key={_GEMINI_API_KEY}"
)

_RADIOLOGY_PROMPT = """\
You are a radiology text corrector. Your role is strictly limited to fixing typing and speech-to-text errors.

════════════════════════════════
ABSOLUTE PROHIBITIONS
════════════════════════════════
- NEVER add words that are not in the original text
- NEVER remove words from the original text
- NEVER complete an incomplete sentence
- NEVER infer clinical meaning
- NEVER add "pas de", "absence de", "absence d'", or any negation not present
- NEVER add "Absence", "Présence", or any qualifier not written by the radiologist
- If a sentence seems clinically incomplete or unusual → leave it EXACTLY as written
- The radiologist's wording is intentional, even if it seems wrong to you

════════════════════════════════
ALLOWED CORRECTIONS ONLY
════════════════════════════════
- Fix accents (e.g. "epanchement" → "épanchement")
- Fix obvious spelling typos (e.g. "fracure" → "fracture")
- Fix capitalization at sentence start
- Remove duplicate words or stray punctuation at end of text

════════════════════════════════
OUTPUT FORMAT
════════════════════════════════
Findings:
[corrected text — word count must remain the same as input]

Conclusion:
CASE A — if a "Conclusion:" section IS provided in the input:
  → Fix spelling/accents only
  → Analyze coherence with the Findings
  → If a MAJOR finding is clearly absent from the conclusion (e.g. significant pathology not mentioned):
      - Add it as a new numbered point at the end
      - Use the EXACT same wording as in the Findings
  → NEVER contradict the existing conclusion
  → NEVER remove or rephrase existing points
  → NEVER add minor or redundant findings already implied
  → NEVER over-interpret or add clinical recommendations

CASE B — if NO "Conclusion:" section in the input:
  → Generate 2–4 numbered points strictly from the Findings
  → Professional radiological style
  → No invented information

No extra text. No explanation.

Input:
"""

_INDICATION_PROMPT = "Correct this medical text. No addition.\n\nInput:\n"

def _call_gemini(prompt_text: str) -> str:
    """
    Appel Gemini via HTTPS direct. À exécuter dans un thread — jamais sur le thread UI.
    """
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
    }).encode("utf-8")

    req = urllib.request.Request(
        _GEMINI_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error("Gemini HTTP %s — %s", e.code, body)
        raise

    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _parse_gemini_response(text: str) -> dict[str, str]:
    """
    Parse the structured Gemini response into section dict.
    Keys: indication, technique, resultat, conclusion.
    Handles "Findings:" → resultat, "Conclusion:" → conclusion, etc.
    Returns only non-empty sections found.
    """
    import re

    # All known labels → internal key (English + French + Markdown variants)
    _MARKERS = [
        (r"indication\s*:", "indication"),
        (r"technique\s*:",  "technique"),
        (r"findings\s*:",   "resultat"),
        (r"r[eé]sultats?\s*:", "resultat"),
        (r"conclusion\s*:", "conclusion"),
    ]

    lower = text.lower()
    positions = []
    for pattern, key in _MARKERS:
        # Strip markdown bold/header prefixes: ##, **, __
        full_pattern = r"(?:#{1,3}\s*|\*{1,2}|_{1,2})?" + pattern
        for m in re.finditer(full_pattern, lower):
            positions.append((m.start(), m.end(), key))
            break  # first occurrence only per section

    if not positions:
        logger.warning("_parse_gemini_response: aucun marqueur trouvé — réponse brute: %s", text[:300])
        return {}

    positions.sort()
    result: dict[str, str] = {}
    for i, (start, end, key) in enumerate(positions):
        next_start = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[end:next_start].strip()
        # Strip trailing markdown bold/italic markers
        content = re.sub(r"\*{1,2}$", "", content).strip()
        _EMPTY_MARKERS = {"[empty]", "[leave empty]", "[laissez vide]", "[vide]", "empty", ""}
        if content and content.lower() not in _EMPTY_MARKERS:
            result[key] = content

    logger.info("_parse_gemini_response: sections trouvées → %s", list(result.keys()))
    return result


def _split_conclusion(text: str) -> tuple[str, str]:
    """Fallback: split on last 'Conclusion:' marker."""
    lower = text.lower()
    for marker in ("conclusion:", "conclusion :", "conclusion\u00a0:"):
        idx = lower.rfind(marker)
        if idx != -1:
            return text[:idx].rstrip(), text[idx + len(marker):].strip()
    return text, ""

logger = logging.getLogger(__name__)

# ── Section catalogue ──────────────────────────────────────────────────────────
SECTIONS: list[str] = ["indication", "technique", "resultat", "conclusion"]

SECTION_LABELS: dict[str, str] = {
    "indication": "Indication",
    "technique":  "Technique",
    "resultat":   "Results",
    "conclusion": "Conclusion",
}

# Display / ISO name → internal 2-letter code
_LANG_MAP: dict[str, str] = {
    "FR": "fr", "EN": "en",
    "Français": "fr", "French":  "fr", "fr": "fr",
    "English":  "en", "Anglais": "en", "en": "en",
}

# Legacy DB key names that differ from the new canonical names
_LEGACY_KEY_MAP: dict[str, str] = {
    "indication": "indication",
    "technique":  "technique",
    "results":    "resultat",   # old key → new key
    "resultat":   "resultat",
    "conclusion": "conclusion",
}


def _empty_buffer() -> dict:
    return {s: {"fr": "", "en": ""} for s in SECTIONS}


def _build_header_map() -> dict[str, str]:
    """Build section-header → key map from Comptes_Rendus.TRANSLATIONS."""
    result: dict[str, str] = {}
    try:
        from Comptes_Rendus import TRANSLATIONS
        for t in TRANSLATIONS.values():
            sections = t.get("sections", [])
            n = len(sections)
            if n < 2:
                continue
            for i, header in enumerate(sections):
                key = header.strip().lower()
                if i == 0:
                    result[key] = "indication"
                elif i == 1:
                    result[key] = "technique"
                elif i == n - 1:
                    result[key] = "conclusion"
                else:
                    result[key] = "resultat"
    except Exception:
        pass
    return result


_CTRL_HEADER_MAP: dict[str, str] = _build_header_map()


# ── Multilingual root-keyword → section key ───────────────────────────────────
# Covers singular AND plural for all 23 supported languages.
# Entries are already normalized: lowercase, no colon, no trailing spaces.
_SECTION_ROOTS: dict[str, str] = {
    # ── indication ───────────────────────────────────────────────────────────
    "indication":        "indication",  # FR/EN/NL/PL
    "indications":       "indication",
    "indikation":        "indication",  # DE/SV/NO/DA
    "indikationen":      "indication",
    "indicación":        "indication",  # ES
    "indicaciones":      "indication",
    "indicazione":       "indication",  # IT
    "indicazioni":       "indication",
    "indicação":         "indication",  # PT
    "indicações":        "indication",
    "показание":         "indication",  # RU
    "показания":         "indication",
    "endikasyon":        "indication",  # TR
    "endikasyonlar":     "indication",
    "适应症":             "indication",  # ZH
    "適応症":             "indication",  # JA
    "적응증":             "indication",  # KO
    "indikasi":          "indication",  # ID/MS
    "wskazanie":         "indication",  # PL
    "wskazania":         "indication",
    "ข้อบ่งชี้":         "indication",  # TH
    "ένδειξη":           "indication",  # EL
    "ενδείξεις":         "indication",
    "indicasyon":        "indication",  # FIL
    "indicație":         "indication",  # RO
    "indicații":         "indication",
    "संकेत":             "indication",  # HI
    # ── technique ────────────────────────────────────────────────────────────
    "technique":         "technique",   # FR/EN
    "techniques":        "technique",
    "protocole":         "technique",   # FR alt
    "protocol":          "technique",   # EN/NL/RO
    "protocols":         "technique",
    "technik":           "technique",   # DE
    "protokoll":         "technique",   # DE/SV/NO
    "técnica":           "technique",   # ES/PT
    "técnicas":          "technique",
    "tecnica":           "technique",   # IT
    "tecniche":          "technique",
    "протокол":          "technique",   # RU
    "teknik":            "technique",   # TR/SV/NO/DA/MS/ID
    "tekniker":          "technique",
    "技术":               "technique",   # ZH
    "技術":               "technique",   # JA
    "기술":               "technique",   # KO
    "techniek":          "technique",   # NL
    "technieken":        "technique",
    "protokół":          "technique",   # PL
    "protokoły":         "technique",
    "วิธีการ":           "technique",   # TH
    "τεχνική":           "technique",   # EL
    "pamamaraan":        "technique",   # FIL
    "tehnică":           "technique",   # RO
    "tehnici":           "technique",
    "तकनीक":            "technique",   # HI
    # ── resultat ─────────────────────────────────────────────────────────────
    "résultat":          "resultat",    # FR singular
    "résultats":         "resultat",    # FR plural
    "resultat":          "resultat",    # SV/NO/DA
    "resultats":         "resultat",
    "finding":           "resultat",    # EN singular
    "findings":          "resultat",    # EN plural
    "result":            "resultat",    # EN/NL
    "results":           "resultat",
    "uitkomst":          "resultat",    # NL
    "uitkomsten":        "resultat",
    "befund":            "resultat",    # DE singular
    "befunde":           "resultat",    # DE plural
    "hallazgo":          "resultat",    # ES singular
    "hallazgos":         "resultat",    # ES plural
    "reperto":           "resultat",    # IT singular
    "reperti":           "resultat",    # IT plural
    "achado":            "resultat",    # PT singular
    "achados":           "resultat",    # PT plural
    "находка":           "resultat",    # RU singular
    "находки":           "resultat",    # RU plural
    "bulgu":             "resultat",    # TR singular
    "bulgular":          "resultat",    # TR plural
    "所见":               "resultat",    # ZH
    "所見":               "resultat",    # JA
    "소견":               "resultat",    # KO
    "temuan":            "resultat",    # ID/MS
    "wynik":             "resultat",    # PL singular
    "wyniki":            "resultat",    # PL plural
    "ผล":                "resultat",    # TH
    "εύρημα":            "resultat",    # EL singular
    "εύρηματα":          "resultat",    # EL plural
    "natuklasan":        "resultat",    # FIL
    "rezultat":          "resultat",    # RO singular
    "rezultate":         "resultat",    # RO plural
    "परिणाम":            "resultat",    # HI
    # ── conclusion ───────────────────────────────────────────────────────────
    "conclusion":        "conclusion",  # FR/EN/NL
    "conclusions":       "conclusion",
    "conclusie":         "conclusion",  # NL
    "schlussfolgerung":  "conclusion",  # DE
    "beurteilung":       "conclusion",  # DE alt
    "eindruck":          "conclusion",  # DE alt
    "conclusión":        "conclusion",  # ES
    "conclusiones":      "conclusion",
    "conclusione":       "conclusion",  # IT
    "conclusioni":       "conclusion",
    "conclusão":         "conclusion",  # PT
    "conclusões":        "conclusion",
    "заключение":        "conclusion",  # RU
    "sonuç":             "conclusion",  # TR
    "结论":               "conclusion",  # ZH
    "結論":               "conclusion",  # JA
    "결론":               "conclusion",  # KO
    "kesimpulan":        "conclusion",  # ID/MS
    "wniosek":           "conclusion",  # PL singular
    "wnioski":           "conclusion",  # PL plural
    "สรุป":              "conclusion",  # TH
    "συμπέρασμα":        "conclusion",  # EL singular
    "συμπεράσματα":      "conclusion",  # EL plural
    "konklusyon":        "conclusion",  # FIL
    "concluzie":         "conclusion",  # RO
    "concluzii":         "conclusion",
    "निष्कर्ष":          "conclusion",  # HI
    "slutsats":          "conclusion",  # SV
    "konklusjon":        "conclusion",  # NO
    "konklusion":        "conclusion",  # DA
    "impression":        "conclusion",  # EN alt
    "impressions":       "conclusion",
    "assessment":        "conclusion",  # EN alt
}


def _match_section_header(raw_line: str,
                          header_map: dict[str, str]) -> str | None:
    """
    Flexible section-header matcher.  Returns a section key or None.

    Matching order:
    1. Exact lowercase match in header_map  (dynamic headers from TRANSLATIONS)
    2. Colon-space normalization: "résultat :" → "résultat:"  then retry map
    3. Root lookup: strip colon + surrounding spaces, check _SECTION_ROOTS
       (handles singular, plural, all 23 supported languages)
    """
    line = raw_line.replace("▸ ", "").replace("▸", "").strip()
    norm = line.lower().replace("\u00a0", " ").replace("\u202f", " ")

    # 1. Exact match
    if norm in header_map:
        return header_map[norm]

    # 2. Normalize spacing around colon ("word :" → "word:")
    norm2 = _re.sub(r"\s+:\s*$", ":", norm)
    if norm2 != norm and norm2 in header_map:
        return header_map[norm2]

    # 3. Root keyword: strip everything from colon onward, collapse spaces
    root = _re.sub(r"\s*[：:].+$", "", norm).strip()
    root = _re.sub(r"\s+", " ", root)
    if root in _SECTION_ROOTS:
        return _SECTION_ROOTS[root]

    return None


def _parse_plain_text(text: str) -> dict[str, str]:
    """
    Split a flat text string into {section_key: content} using
    _CTRL_HEADER_MAP (built from Comptes_Rendus.TRANSLATIONS).
    Falls back to putting everything in 'resultat' if no headers found.
    """
    result: dict[str, list[str]] = {s: [] for s in SECTIONS}
    current: str | None = None
    for raw_line in text.splitlines():
        matched = _match_section_header(raw_line, _CTRL_HEADER_MAP)
        if matched:
            current = matched
        elif current is not None:
            result[current].append(raw_line.replace("▸ ", "").replace("▸", "").strip())

    parsed = {k: "\n".join(v).strip() for k, v in result.items()}

    # Nothing matched — dump everything in resultat
    if current is None:
        parsed["resultat"] = text.strip()

    # Wrap into buffer format {section: {fr: text, en: ""}}
    return {s: {"fr": parsed.get(s, ""), "en": ""} for s in SECTIONS}


# ══════════════════════════════════════════════════════════════════════════════
#  ReportEditorController
# ══════════════════════════════════════════════════════════════════════════════

class ReportEditorController:
    """
    Controller for the structured multilingual report editor.

    The view should:
    1. Instantiate this controller.
    2. Register callbacks (on_content_ready, on_save_status, …).
    3. Call ``load_async()`` once to pull the last compte_rendu from the DB.
    4. Forward every text change to ``update_current_text()``.
    5. Call ``save()`` / ``finalize()`` on user action.
    """

    def __init__(self, core_state: dict, item: dict) -> None:
        self._s    = core_state
        self._item = dict(item)
        self._lm   = core_state.get("lm")

        # ── Content buffer ─────────────────────────────────────────────────
        self._buffer: dict[str, dict[str, str]] = _empty_buffer()

        # ── Active state ───────────────────────────────────────────────────
        self._active_section: str      = SECTIONS[0]
        self._active_lang:    str      = "fr"

        # ── DB tracking ───────────────────────────────────────────────────
        self._cr_uuid:    str | None = None
        self._saved_hash: str | None = None

        # ── Callbacks (assigned by the view after construction) ────────────
        self.on_content_ready:   Callable | None = None
        """Called on the main thread with the full buffer after a load."""

        self.on_section_changed: Callable | None = None
        """Called with (section: str, text: str) when the active section changes."""

        self.on_lang_changed:    Callable | None = None
        """Called with (lang: str, text: str) when the active language changes."""

        self.on_save_status:     Callable | None = None
        """Called with (message: str, is_error: bool) after a save attempt."""

        self.on_item_updated:    Callable | None = None
        """Called with the updated item dict when its status changes (e.g. → In Progress)."""

    # ══════════════════════════════════════════════════════════════════════
    #  Feature gating
    # ══════════════════════════════════════════════════════════════════════

    def can_use_feature(self, feature_name: str) -> bool:
        """
        Delegate to LicenseManager.can_use_feature().
        Returns True when no LicenseManager is present (dev / test mode).
        """
        if self._lm is None:
            return True
        return bool(self._lm.can_use_feature(feature_name))

    @property
    def structured_reports_enabled(self) -> bool:
        """True for PRO / CLINIC plans."""
        return self.can_use_feature("structured_reports")

    @property
    def multilang_enabled(self) -> bool:
        """True when the user may switch the report language."""
        return self.can_use_feature("multilang")

    @property
    def export_enabled(self) -> bool:
        """True when Word / PDF export is available."""
        return self.can_use_feature("export")

    # ══════════════════════════════════════════════════════════════════════
    #  Active state
    # ══════════════════════════════════════════════════════════════════════

    @property
    def active_section(self) -> str:
        return self._active_section

    @property
    def active_lang(self) -> str:
        return self._active_lang

    def set_section(self, section: str) -> str:
        """
        Switch the active section.

        Fires ``on_section_changed(section, text)`` and returns the current
        text so the caller can update the textbox immediately.
        """
        if section not in SECTIONS:
            raise ValueError(f"Unknown section: {section!r}")
        self._active_section = section
        text = self.get_current_text()
        if self.on_section_changed:
            self.on_section_changed(section, text)
        return text

    def set_language(self, lang_display: str) -> str:
        """
        Switch the active language.

        Fires ``on_lang_changed(lang_key, text)`` and returns the current text.
        """
        lang_key = _LANG_MAP.get(lang_display, lang_display.lower()[:2])
        self._active_lang = lang_key
        text = self.get_current_text()
        if self.on_lang_changed:
            self.on_lang_changed(lang_key, text)
        return text

    # ══════════════════════════════════════════════════════════════════════
    #  Content buffer
    # ══════════════════════════════════════════════════════════════════════

    def get_current_text(self) -> str:
        """Text of the active section in the active language."""
        return self._buffer[self._active_section].get(self._active_lang, "")

    def update_current_text(self, text: str) -> None:
        """Write *text* into the active section/language slot."""
        self._buffer[self._active_section][self._active_lang] = text

    def get_full_buffer(self) -> dict:
        """Return a deep copy of the full multilingual buffer."""
        return json.loads(json.dumps(self._buffer))

    def get_flat_content(self, lang: str | None = None) -> dict:
        """
        Return ``{section: text}`` for a single language.

        Defaults to the currently active language.
        """
        lang = lang or self._active_lang
        return {s: self._buffer[s].get(lang, "") for s in SECTIONS}

    def _content_hash(self) -> str:
        return json.dumps(self._buffer, sort_keys=True)

    def is_dirty(self) -> bool:
        """True if there are unsaved changes."""
        return self._content_hash() != self._saved_hash

    # ══════════════════════════════════════════════════════════════════════
    #  Load / Save
    # ══════════════════════════════════════════════════════════════════════

    def load_async(self) -> None:
        """
        Fetch the last compte_rendu for this exam from the PACS DB.

        Runs in a daemon thread; calls ``on_content_ready`` on the
        *controller* side — the view is responsible for marshalling it
        to the main thread with ``after(0, …)``.
        """
        threading.Thread(target=self._do_load, daemon=True,
                         name="ctrl-load").start()

    def _do_load(self) -> None:
        try:
            from pacs_ris_db import get_pacs_db
            db   = get_pacs_db()
            last = db.get_last_compte_rendu(self._item["examen_uuid"])

            if last:
                self._cr_uuid = last.get("cr_uuid")
                raw           = last.get("contenu", "")
                self._buffer  = self._parse_content(raw)

            db.mark_in_progress(self._item["examen_uuid"])
            if self._item.get("statut") == "En attente":
                self._item["statut"] = "En cours"
                if self.on_item_updated:
                    self.on_item_updated(self._item)

            self._saved_hash = self._content_hash()

            if self.on_content_ready:
                self.on_content_ready(self.get_full_buffer())

        except Exception as exc:
            logger.error("ReportEditorController._do_load: %s", exc,
                         exc_info=True)

    def save(self, silent: bool = False) -> bool:
        """
        Persist the buffer to the PACS DB.

        Parameters
        ----------
        silent : bool
            If True, suppress the success toast (used for auto-save).

        Returns
        -------
        bool
            True on success, False on failure.
        """
        if not self.is_dirty():
            return True
        try:
            from pacs_ris_db import get_pacs_db
            db      = get_pacs_db()
            payload = json.dumps(self._buffer, ensure_ascii=False)
            cr_uuid = db.save_compte_rendu(self._item["examen_uuid"], payload)
            if cr_uuid:
                self._cr_uuid    = cr_uuid
                self._saved_hash = self._content_hash()
                if not silent and self.on_save_status:
                    now = datetime.datetime.now().strftime("%H:%M:%S")
                    self.on_save_status(f"Saved {now}", False)
                return True
        except Exception as exc:
            logger.error("ReportEditorController.save: %s", exc)
            if self.on_save_status:
                self.on_save_status("⚠ Save failed", True)
        return False

    def finalize(self) -> bool:
        """
        Save + mark exam as *Finalisé* in the PACS DB.

        Returns True if the status update succeeded.
        """
        self.save(silent=True)
        try:
            from pacs_ris_db import get_pacs_db
            get_pacs_db().update_examen_statut(
                self._item["examen_uuid"], "Finalisé")
            self._item["statut"] = "Finalisé"
            if self.on_item_updated:
                self.on_item_updated(self._item)
            return True
        except Exception as exc:
            logger.error("ReportEditorController.finalize: %s", exc)
            return False

    def reset_for_new_item(self, new_item: dict) -> None:
        """
        Re-use the controller for a different exam without creating a new instance.
        Called by the view when "Next Patient" is triggered.
        """
        self._item       = dict(new_item)
        self._buffer     = _empty_buffer()
        self._cr_uuid    = None
        self._saved_hash = None

    # ══════════════════════════════════════════════════════════════════════
    #  Content parsing  (handles all legacy formats)
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _parse_content(raw: str) -> dict:
        """
        Deserialise a stored *contenu* string into the multilingual buffer.

        Handles three formats:

        1. **New multilingual**
           ``{"indication": {"fr": "...", "en": "..."}, ...}``

        2. **Legacy flat** (old single-language JSON)
           ``{"indication": "...", "results": "...", "conclusion": "..."}``

        3. **Plain text**
           Put everything into ``resultat.fr``.
        """
        empty = _empty_buffer()
        if not raw:
            return empty

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Plain text — try to parse section headers first
            return _parse_plain_text(raw)

        if not isinstance(data, dict):
            return _parse_plain_text(str(raw))

        first_val = next(iter(data.values()), None)

        # ── Format 1: multilingual ────────────────────────────────────────
        if isinstance(first_val, dict):
            result = {}
            for s in SECTIONS:
                stored = data.get(s, {})
                result[s] = {
                    "fr": stored.get("fr", ""),
                    "en": stored.get("en", ""),
                }
            # Sanity-check: old bug sometimes saved the entire multi-section
            # report blob in a single section (e.g. "indication").
            # Detect this by checking if ONE section holds text that
            # re-parses into multiple recognised sections while the others
            # are empty.
            filled = [s for s in SECTIONS if result[s]["fr"].strip()]
            if len(filled) == 1:
                candidate = filled[0]
                reparsed = _parse_plain_text(result[candidate]["fr"])
                n_sections = sum(
                    1 for buf in reparsed.values() if buf.get("fr", "").strip()
                )
                if n_sections > 1:
                    # Redistribute correctly; preserve existing "en" slots
                    for s, buf in reparsed.items():
                        txt = buf.get("fr", "")
                        result[s]["fr"] = txt
                    logger.info(
                        "_parse_content: redistributed multi-section blob"
                        " from '%s' into %d sections", candidate, n_sections
                    )
            return result

        # ── Format 2: legacy flat JSON ────────────────────────────────────
        # Old code sometimes saved everything in "indication" as a flat string.
        # If a section value contains section headers, re-parse it properly.
        result = _empty_buffer()
        for old_key, new_key in _LEGACY_KEY_MAP.items():
            val = data.get(old_key, "")
            if not val:
                continue
            # If this single field contains headers for multiple sections,
            # redistribute — otherwise store as-is.
            parsed = _parse_plain_text(val)
            has_multiple = sum(1 for buf in parsed.values() if buf.get("fr", "").strip()) > 1
            if has_multiple:
                for s, buf in parsed.items():
                    txt = buf.get("fr", "")
                    if txt.strip():
                        result[s]["fr"] = txt
            else:
                result[new_key]["fr"] = val
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  Word / PDF export
    # ══════════════════════════════════════════════════════════════════════

    def build_word_payload(self, medecin: str, etablissement: str,
                           language_display: str) -> dict:
        """
        Build the payload dict expected by ``ProfessionalWordGenerator``.

        The ``formula`` key contains a flat text representation of the
        report sections in the active language, which the Word generator
        renders into the report body.
        """
        content   = self.get_flat_content()
        full_text = "\n\n".join(
            f"{k.upper()}\n{v}" for k, v in content.items() if v.strip()
        )
        return {
            "formula":       full_text,
            "formula_name":  "",
            "modality":      self._item.get("modalite",    ""),
            "exam_type":     self._item.get("type_examen", ""),
            "medecin":       medecin,
            "etablissement": etablissement,
            "language":      language_display,
            "patient_data":  self._item,
            "examen_data":   self._item,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Dictation hook
    # ══════════════════════════════════════════════════════════════════════

    def on_dictation_text(self, text: str,
                          section: str | None = None) -> None:
        """
        Insert dictated text into a section buffer.

        Call this from the ``WhisperDictation`` callback.
        Pass ``section=None`` to target the currently active section.

        After inserting, fires ``on_content_ready`` so the view can
        refresh the displayed textbox.
        """
        target = section or self._active_section
        if target not in SECTIONS:
            logger.warning("on_dictation_text: unknown section %r", target)
            return

        existing = self._buffer[target].get(self._active_lang, "")
        separator = " " if existing else ""
        self._buffer[target][self._active_lang] = existing + separator + text

        if self.on_content_ready:
            self.on_content_ready(self.get_full_buffer())

    # ══════════════════════════════════════════════════════════════════════
    #  AI enhancement hook  (stub — wire a real backend when ready)
    # ══════════════════════════════════════════════════════════════════════

    def enhance_section_ai(self,
                           section:  str      | None = None,
                           callback: Callable | None = None,
                           on_error: Callable | None = None) -> None:
        """
        Per-section AI enhancement via Gemini 1.5 Flash-Lite.

        Runs the API call in a background thread so the UI stays responsive.
        Calls ``callback(enhanced_text)`` on completion (still from that thread;
        the caller must marshal to the main thread if required — the view already
        does this with ``self.after(0, ...)``.

        RESULTAT logic  — full structured report:
            • body (everything before "Conclusion :") → callback → RESULTAT field
            • conclusion text → stored directly in self._buffer["conclusion"]

        INDICATION logic — lightweight correction only.
        CONCLUSION logic — extract conclusion part from structured response.
        """
        target = section or self._active_section
        text   = self._buffer[target].get(self._active_lang, "").strip()

        if not text:
            logger.debug("AI enhance skipped — empty input (section=%s)", target)
            return

        def _run():
            try:
                if target == "indication":
                    result = _call_gemini(_INDICATION_PROMPT + text)
                    if callback:
                        callback(result)

                elif target == "conclusion":
                    raw      = _call_gemini(_RADIOLOGY_PROMPT + text)
                    sections = _parse_gemini_response(raw)
                    final    = sections.get("conclusion") or _split_conclusion(raw)[1] or raw
                    if callback:
                        callback(final)

                else:  # resultat
                    raw      = _call_gemini(_RADIOLOGY_PROMPT + text)
                    sections = _parse_gemini_response(raw)

                    # Push all parsed sections into the buffer
                    for key in ("indication", "technique", "conclusion"):
                        if sections.get(key):
                            self._buffer[key][self._active_lang] = sections[key]

                    findings = sections.get("resultat") or _split_conclusion(raw)[0] or raw
                    if callback:
                        callback(findings)

            except Exception as exc:
                logger.error("Gemini API error (section=%s): %s", target, exc)
                if on_error:
                    on_error(text)
                elif callback:
                    callback(text)

        threading.Thread(target=_run, daemon=True).start()
