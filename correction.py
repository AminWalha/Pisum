"""correction.py
================
Correcteur médical post-transcription via Gemini 2.5 Flash Lite.

Usage:
    from correction import correct_text, is_text_too_different

Requires:
    pip install google-generativeai python-dotenv
    GEMINI_API_KEY in environment or .env file
"""

import logging
import os
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

_GEMINI_MODEL = "gemini-2.0-flash-lite"

_CORRECTION_PROMPT = """\
You are a medical transcription corrector.

TASK:
Fix transcription errors from speech-to-text.

You MUST ALWAYS correct the text, even if it is very short.

STRICT RULES:
* DO NOT add any information
* DO NOT interpret
* DO NOT rephrase
* DO NOT summarize
* DO NOT change sentence meaning
* DO NOT expand abbreviations

ALLOWED:
* Fix spelling
* Fix grammar
* Fix punctuation
* Correct obvious mis-transcribed medical words ONLY

STYLE:
* Keep original wording
* Same language
* Minimal edits only

OUTPUT:
Return ONLY the corrected text.
No explanations.
No formatting.\
"""

# ── Lazy client init ──────────────────────────────────────────────────────────

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client

    # Load .env if present (desktop app — no server)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("[Correction] GEMINI_API_KEY absent — correction désactivée")
        return None

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        _client = genai.GenerativeModel(
            model_name=_GEMINI_MODEL,
            generation_config={
                "temperature": 0.1,
                "max_output_tokens": 512,
                "candidate_count": 1,
            },
            system_instruction=_CORRECTION_PROMPT,
        )
        logger.info(f"[Correction] Client Gemini initialisé ({_GEMINI_MODEL})")
    except Exception as e:
        logger.error(f"[Correction] Impossible d'initialiser Gemini : {e}")
        return None

    return _client


# ── Public API ────────────────────────────────────────────────────────────────

def correct_text(text: str) -> str:
    """
    Envoie *text* à Gemini et retourne le texte corrigé.
    En cas d'échec (API indisponible, timeout, erreur réseau), retourne *text* intact.
    """
    if not text or not text.strip():
        return text

    client = _get_client()
    if client is None:
        return text

    try:
        import google.generativeai as genai  # noqa: F401 — already imported by _get_client

        response = client.generate_content(text)
        corrected = response.text.strip()
        if not corrected:
            return text
        logger.info(f"[Correction] Brut: {text!r} → Corrigé: {corrected!r}")
        return corrected

    except Exception as e:
        logger.warning(f"[Correction] Gemini indisponible, texte brut conservé : {e}")
        return text


def is_text_too_different(a: str, b: str, threshold: float = 0.55) -> bool:
    """
    Retourne True si *b* s'écarte trop de *a* (ratio SequenceMatcher < threshold).
    Utilisé comme garde-fou : si Gemini hallucine, on conserve le texte brut.
    """
    if not a or not b:
        return False
    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    too_different = ratio < threshold
    if too_different:
        logger.warning(
            f"[Correction] Texte trop différent (ratio={ratio:.2f}) — texte brut conservé"
        )
    return too_different
