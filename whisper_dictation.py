"""whisper_dictation.py
====================
Moteur de dictée vocale hors ligne basé sur OpenAI Whisper (MIT)
et correcteur radiologique post-transcription (RadioCorrector).

Extrait de Comptes_Rendus.py pour améliorer la maintenabilité.

Dépendances optionnelles :
    pip install openai-whisper sounddevice numpy

Utilisation :
    from whisper_dictation import (
        WhisperDictation, RadioCorrector,
        WHISPER_AVAILABLE, SOUNDDEVICE_AVAILABLE,
    )
"""

# ── Bibliothèques standard ───────────────────────────────────────────
import os
import re
import difflib
import queue
import threading
import unicodedata
import logging
import sys
import subprocess
import importlib

# --- CORRECTION POUR COMPILATION SANS CONSOLE (PYARMOR/PYINSTALLER) ---
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ── Auto-installation des dépendances de dictée vocale ───────────────
# Exécuté une seule fois au premier lancement sur chaque PC.
# N'installe que ce qui manque — silencieux si tout est déjà présent.
def _ensure_dictation_deps(on_progress=None):
    """
    Vérifie et installe automatiquement les dépendances de la dictée vocale.
    Appelée avant le premier usage de WhisperDictation.
    Retourne True si tout est disponible, False si une installation a échoué.
    """
    def _log(msg):
        if on_progress:
            on_progress(msg)
        else:
            logging.getLogger(__name__).info(msg)

    # Packages requis : (nom_import, package_pip, version_pip)
    # torch doit être installé en premier car whisper en dépend
    REQUIRED = [
        ("torch",       "torch",          None),
        ("numpy",       "numpy",          None),
        ("sounddevice", "sounddevice",    None),
        ("whisper",     "openai-whisper", None),
        ("tiktoken",    "tiktoken",       None),
        ("tqdm",        "tqdm",           None),
        ("more_itertools", "more-itertools", None),
    ]

    missing = []
    for import_name, pip_name, version in REQUIRED:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append((import_name, pip_name, version))

    if not missing:
        return True

    _log("Installation des composants de dictée vocale...")

    # Déterminer le bon exécutable pip (dans un exe PyInstaller, utiliser sys.executable)
    python_exe = sys.executable

    # Dans un bundle PyInstaller, sys.executable est PISUM.exe — on cherche python.exe
    # dans le même répertoire, dans PATH, ou dans les installations Python standard.
    import os
    exe_dir = os.path.dirname(python_exe)

    # Chercher dans les chemins d'installation Python standard (Windows)
    _std_python_paths = []
    try:
        import winreg
        for _root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for _sub in (
                r"SOFTWARE\Python\PythonCore",
                r"SOFTWARE\WOW6432Node\Python\PythonCore",
            ):
                try:
                    with winreg.OpenKey(_root, _sub) as _k:
                        i = 0
                        while True:
                            try:
                                _ver = winreg.EnumKey(_k, i)
                                with winreg.OpenKey(_k, _ver + r"\InstallPath") as _p:
                                    _path = winreg.QueryValue(_p, None)
                                    _std_python_paths.append(
                                        os.path.join(_path.strip(), "python.exe")
                                    )
                                i += 1
                            except OSError:
                                break
                except OSError:
                    pass
    except Exception:
        pass

    python_candidates = [
        os.path.join(exe_dir, "python.exe"),  # à côté de l'exe (cas rare)
        "python",                              # dans PATH
        "python3",
    ] + _std_python_paths
    pip_python = None
    for candidate in python_candidates:
        try:
            result = subprocess.run(
                [candidate, "-c", "import sys; print(sys.version)"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                pip_python = candidate
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if pip_python is None:
        _log("Python introuvable — impossible d'installer les dépendances.")
        return False

    success = True
    for import_name, pip_name, version in missing:
        pkg_spec = f"{pip_name}=={version}" if version else pip_name
        _log(f"Installation de {pkg_spec}...")
        try:
            result = subprocess.run(
                [pip_python, "-m", "pip", "install", pkg_spec,
                 "--quiet", "--no-warn-script-location"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                _log(f"{pip_name} installé.")
            else:
                _log(f"Erreur installation {pip_name} : {result.stderr[-200:]}")
                success = False
        except subprocess.TimeoutExpired:
            _log(f"Timeout lors de l'installation de {pip_name}.")
            success = False
        except Exception as e:
            _log(f"Erreur inattendue lors de l'installation de {pip_name} : {e}")
            success = False

    return success


# ── Bibliothèques optionnelles (dictée vocale) ───────────────────────
# En mode PyInstaller (frozen), toutes les dépendances sont déjà bundlées
# dans l'exe — on ne tente PAS d'auto-installer via pip (python.exe absent).
# En mode script normal, on tente l'auto-install avant les imports.
_IS_FROZEN = getattr(sys, 'frozen', False)

if not _IS_FROZEN:
    # Pré-installation silencieuse AVANT les imports pour que les flags
    # WHISPER_AVAILABLE / SOUNDDEVICE_AVAILABLE soient corrects dès le départ.
    _ensure_dictation_deps()

WHISPER_AVAILABLE = False
SOUNDDEVICE_AVAILABLE = False
try:
    import whisper as _whisper_lib
    WHISPER_AVAILABLE = True
except ImportError:
    pass
try:
    import sounddevice as _sd
    import numpy as _np
    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Alias conservé pour compatibilité interne avec RadioCorrector
_re = re

# ── Dédoublonnage de phrases ──────────────────────────────────────────────────
def _deduplicate_sentences(text: str) -> str:
    """
    Supprime les phrases dupliquées qu'un bloc Whisper peut retourner.
    Exemples filtrés :
      "No signs of appendicitis. No signs of appendicitis." → "No signs of appendicitis."
      "A. B. A. B." → "A. B."
    Stratégie : découpe sur les ponctuations de fin de phrase et déduplique
    en conservant l'ordre d'apparition.
    """
    import re as _re_dd
    # Découper sur . ! ? (en gardant le séparateur)
    parts = _re_dd.split(r'(?<=[.!?])\s+', text.strip())
    seen  = []
    result = []
    for part in parts:
        key = part.lower().strip().rstrip('.!? ')
        if not key:
            continue
        # Vérifier si très similaire à une phrase déjà vue (seuil 80%)
        duplicate = False
        for prev_key in seen:
            ratio = difflib.SequenceMatcher(None, key, prev_key).ratio()
            if ratio >= 0.80:
                duplicate = True
                break
        if not duplicate:
            seen.append(key)
            result.append(part)
    deduped = ' '.join(result).strip()
    if deduped != text.strip():
        logger.info(f"[Whisper] Dédoublonnage : {text!r} → {deduped!r}")
    return deduped


# ── Détecteur de changement de langue (hallucination bilingue) ───────────────
def _detect_language_switch(text: str, expected_lang: str) -> str:
    """
    Détecte une hallucination bilingue de Whisper au niveau des phrases.
    Whisper transcrit parfois la bonne langue puis hallucine une traduction.
    Ex: "Gallbladder normal. Vésicule biliaire normale." en mode anglais.
    Retourne le texte tronqué à la première phrase dans la mauvaise langue.
    """
    import re as _re_ls

    # Marqueurs lexicaux forts par langue (mots grammaticaux distinctifs).
    # RÈGLE : chaque langue doit avoir ses propres marqueurs pour que exp_score > 0
    # quand Whisper transcrit correctement. Sans marqueurs, exp_score = 0 toujours
    # → la fonction croit que TOUTE phrase est un "switch" vers une autre langue
    # et tronque / supprime le texte correctement transcrit.
    _LANG_MARKERS = {
        'fr': {'le', 'la', 'les', 'des', 'du', 'un', 'une', 'est', 'sont',
               'dans', 'avec', 'pour', 'sur', 'par', 'au', 'aux',
               'mais', 'que', 'qui', 'sans', 'cette', 'ces', 'pas', 'plus',
               'très', 'aussi', 'son', 'notre', 'votre', 'leur'},
        'en': {'the', 'a', 'an', 'of', 'in', 'with', 'without', 'within',
               'and', 'or', 'to', 'for', 'on', 'at', 'by', 'from',
               'is', 'are', 'was', 'not', 'no', 'this', 'that', 'its',
               'which', 'normal', 'mild', 'moderate', 'bilateral', 'showing'},
        'de': {'der', 'die', 'das', 'ein', 'eine', 'und', 'mit', 'ohne',
               'von', 'zu', 'ist', 'sind', 'nicht', 'kein', 'keine'},
        'es': {'el', 'la', 'los', 'las', 'un', 'una', 'del', 'con', 'sin',
               'por', 'para', 'es', 'son', 'no', 'se', 'al', 'sus'},
        'it': {'il', 'lo', 'la', 'gli', 'le', 'un', 'una', 'del', 'con',
               'senza', 'per', 'è', 'sono', 'non', 'si', 'al'},
        'pt': {'o', 'a', 'os', 'as', 'um', 'uma', 'de', 'da', 'do', 'com',
               'sem', 'por', 'para', 'em', 'é', 'são', 'não', 'se', 'ao'},
        'ru': {'и', 'в', 'на', 'с', 'по', 'из', 'не', 'это', 'что', 'как',
               'но', 'или', 'же', 'ли', 'бы', 'то', 'от', 'до', 'за', 'при'},
        'nl': {'de', 'het', 'een', 'van', 'in', 'met', 'zonder', 'op', 'en',
               'of', 'niet', 'geen', 'is', 'zijn', 'was', 'worden', 'bij'},
        # ── Langues manquantes — ajoutées pour éviter exp_score=0 systématique ──
        'ro': {'și', 'în', 'cu', 'de', 'la', 'pe', 'nu', 'este', 'sunt',
               'că', 'din', 'se', 'o', 'un', 'ale', 'care', 'sau', 'dar',
               'fără', 'prin', 'între', 'după', 'față', 'spre', 'absența',
               'prezența', 'aspect', 'normal', 'normală'},
        'pl': {'i', 'w', 'z', 'na', 'do', 'się', 'nie', 'że', 'to', 'jak',
               'jest', 'są', 'ale', 'lub', 'bez', 'po', 'przez', 'przy',
               'nad', 'pod', 'przed', 'między'},
        'sv': {'och', 'i', 'att', 'det', 'en', 'är', 'av', 'för', 'på',
               'med', 'till', 'inte', 'om', 'som', 'men', 'utan', 'vid'},
        'no': {'og', 'i', 'er', 'det', 'en', 'av', 'for', 'på', 'med',
               'til', 'ikke', 'om', 'som', 'men', 'uten', 'ved'},
        'da': {'og', 'i', 'er', 'det', 'en', 'af', 'for', 'på', 'med',
               'til', 'ikke', 'om', 'som', 'men', 'uden', 'ved'},
        'tr': {'ve', 'bir', 'bu', 'da', 'de', 'ile', 'için', 'var', 'yok',
               'olan', 'olan', 'veya', 'ama', 'gibi', 'kadar', 'sonra'},
        'el': {'και', 'το', 'τα', 'τη', 'τον', 'της', 'του', 'σε', 'με',
               'για', 'από', 'στο', 'στη', 'δεν', 'είναι', 'ή', 'αλλά'},
        'pl': {'i', 'w', 'z', 'na', 'do', 'się', 'nie', 'że', 'to',
               'jest', 'są', 'ale', 'lub', 'bez', 'po', 'przez'},
        # Langues à script non-latin : les marqueurs sont leurs mots grammaticaux
        # les plus fréquents — suffisant pour exp_score > 0 vs fr/en.
        'hi': {'और', 'है', 'में', 'को', 'के', 'की', 'का', 'से', 'पर',
               'यह', 'वह', 'हैं', 'नहीं', 'भी', 'तो', 'लिए'},
        'id': {'dan', 'di', 'yang', 'dengan', 'untuk', 'dari', 'ke', 'ini',
               'tidak', 'ada', 'atau', 'juga', 'serta', 'pada', 'dalam'},
        'ms': {'dan', 'di', 'yang', 'dengan', 'untuk', 'dari', 'ke', 'ini',
               'tidak', 'ada', 'atau', 'juga', 'serta', 'pada', 'dalam'},
        'th': {'และ', 'ใน', 'ของ', 'ที่', 'การ', 'มี', 'ไม่', 'เป็น',
               'ได้', 'จาก', 'หรือ', 'แต่', 'โดย', 'กับ', 'ตาม'},
        'tl': {'ang', 'ng', 'sa', 'at', 'ay', 'mga', 'na', 'hindi',
               'ito', 'siya', 'nang', 'para', 'kung', 'pero'},
        # CJK : caractères idéographiques suffisent — pas de mots séparés par espaces,
        # on ne peut pas faire de lookup par token, donc on laisse vide et on
        # désactive le switch-detect pour ces langues (géré plus bas).
        'zh': set(), 'ja': set(), 'ko': set(),
    }

    # Accents fortement associés au français (signal fort en contexte anglais/allemand)
    # EXCLURE aussi le roumain — il partage ă â î ș ț qui peuvent interférer
    _FR_ACCENTS = _re_ls.compile(r'[àâçéèêëîïôùûüœæÀÂÇÉÈÊËÎÏÔÙÛÜŒÆ]')

    sentences = _re_ls.split(r'(?<=[.!?…])\s+', text.strip())
    if len(sentences) <= 1:
        return text

    # Langues pour lesquelles le switch-detect est désactivé :
    # - CJK : tokenisation par caractère, pas de marqueurs lexicaux fiables
    # - Langues sans marqueurs définis : risque de faux positifs trop élevé
    _NO_SWITCH_DETECT = {'zh', 'ja', 'ko', 'th', 'hi'}
    if expected_lang in _NO_SWITCH_DETECT:
        return text

    exp_markers = _LANG_MARKERS.get(expected_lang, set())
    other_markers = {k: v for k, v in _LANG_MARKERS.items()
                     if k != expected_lang and v}  # ignorer les sets vides (CJK)

    good = []
    switched = False

    for sent in sentences:
        if switched:
            continue
        clean = _re_ls.sub(r'[^\w\s]', '', sent.lower())
        words_set = set(clean.split())

        exp_score   = len(words_set & exp_markers)
        other_score = max((len(words_set & m) for m in other_markers.values()), default=0)

        # Signal fort : accents français dans une langue non romane attendue.
        # EXCLURE les langues romanes (ro, es, it, pt, fr, ca) car elles ont
        # des accents légitimes similaires.
        _ROMANCE = {'fr', 'es', 'it', 'pt', 'ro', 'ca', 'gl', 'oc'}
        has_foreign_accents = (
            expected_lang not in _ROMANCE
            and bool(_FR_ACCENTS.search(sent))
        )

        # Règle de switch : déclencher seulement si la phrase est clairement
        # dans une autre langue ET pas dans la langue attendue.
        # On exige other_score >= 3 (au lieu de 2) pour réduire les faux positifs
        # sur les langues avec peu de marqueurs (ro, pl, tr...).
        is_switched = has_foreign_accents or (
            other_score >= 3
            and other_score > exp_score
            and exp_score == 0   # aucun marqueur de la langue attendue → vraiment autre langue
        )

        if is_switched:
            switched = True
            logger.debug(
                f"[Whisper] Switch langue détecté (exp={exp_score}, other={other_score}, "
                f"accents={has_foreign_accents}) : {sent[:50]!r}"
            )
        else:
            good.append(sent)

    if switched and good:
        return ' '.join(good)
    return text


# ============ DICTÉE VOCALE WHISPER ============
class WhisperDictation:
    """
    Moteur de dictée vocale hors ligne basé sur OpenAI Whisper (MIT).
    - Détection automatique du silence (VAD) : ne transcrit que si la voix est détectée
    - Prompt médical radiology pour orienter le modèle
    - Filtre anti-hallucinations (phrases parasites YouTube, musique, etc.)
    - Seuil d'énergie configurable

    Installation :
        pip install openai-whisper sounddevice numpy
    """

    SAMPLE_RATE    = 16000
    BLOCK_SECONDS  = 5        # durée max d'un bloc avant transcription (6 causait des chevauchements)
    CHANNELS       = 1
    DTYPE          = "float32"

    # ── Seuil VAD (Voice Activity Detection) ─────────────────────────────
    VAD_THRESHOLD = 0.01

    # ── Prompts médicaux par langue ───────────────────────────────────────
    INITIAL_PROMPTS = {
        "fr": (
            "Transcription verbatim en FRANÇAIS UNIQUEMENT. "
            "Ne pas traduire. Ne pas répéter en anglais ni dans une autre langue. "
            "Compte rendu radiologique. "
            "Vocabulaire : vésicule biliaire, cholécystite, lithiase, pancréas, "
            "foie, rate, reins, différenciation cortico-médullaire, parenchyme, "
            "échographie, scanner, IRM, épanchement, ascite, hépatomégalie, "
            "splénomégalie, hydronéphrose, aorte, veine porte, kyste, nodule."
        ),
        "en": (
            "Verbatim transcription in ENGLISH ONLY. "
            "Do not translate. Do not repeat in French or any other language. "
            "Radiology report. "
            "Medical vocabulary: gallbladder, cholecystitis, lithiasis, pancreas, "
            "liver, spleen, kidneys, corticomedullary differentiation, parenchyma, "
            "ultrasound, CT scan, MRI, effusion, ascites, hepatomegaly, "
            "splenomegaly, hydronephrosis, aorta, portal vein, cyst, nodule, "
            "tendon, biceps, rotator cuff, echogenicity, hypoechoic, hyperechoic."
        ),
        "de": (
            "Verbatim-Transkription NUR AUF DEUTSCH. "
            "Nicht übersetzen. Nicht auf Französisch oder einer anderen Sprache wiederholen. "
            "Radiologiebericht. "
            "Vokabular: Gallenblase, Cholezystitis, Bauchspeicheldrüse, Leber, "
            "Milz, Nieren, kortikomedulläre Differenzierung, Parenchym, "
            "Ultraschall, CT, MRT, Erguss, Aszites, Hepatomegalie, Splenomegalie."
        ),
        "es": (
            "Transcripción verbatim EN ESPAÑOL ÚNICAMENTE. "
            "No traducir. No repetir en francés ni en otro idioma. "
            "Informe radiológico. "
            "Vocabulario: vesícula biliar, colecistitis, litiasis, páncreas, "
            "hígado, bazo, riñones, diferenciación corticomedular, parénquima, "
            "ecografía, TAC, RMN, derrame, ascitis, hepatomegalia, esplenomegalia."
        ),
        "it": (
            "Trascrizione verbatim SOLO IN ITALIANO. "
            "Non tradurre. Non ripetere in francese o in un'altra lingua. "
            "Referto radiologico. "
            "Vocabolario: cistifellea, colecistite, pancreas, fegato, milza, reni, "
            "differenziazione corticomidollare, parenchima, ecografia, TAC, RMN."
        ),
        "pt": (
            "Transcrição verbatim SOMENTE EM PORTUGUÊS. "
            "Não traduzir. Não repetir em francês ou em outro idioma. "
            "Laudo radiológico. "
            "Vocabulário: vesícula biliar, colecistite, pâncreas, fígado, baço, rins, "
            "diferenciação corticomedular, parênquima, ultrassonografia, TC, RM."
        ),
        "ru": (
            "Дословная транскрипция ТОЛЬКО НА РУССКОМ. "
            "Не переводить. Не повторять на французском или другом языке. "
            "Радиологическое заключение. "
            "Словарь: желчный пузырь, холецистит, поджелудочная железа, печень, "
            "селезёнка, почки, кортикомедуллярная дифференциация, паренхима."
        ),
        "zh": (
            "仅用中文逐字转录。不得翻译。不得重复用法语或其他语言。"
            "放射学报告。词汇：胆囊、胆囊炎、胰腺、肝脏、脾脏、肾脏、"
            "皮髓质分化、实质、超声、CT、MRI、积液、腹水。"
        ),
        "ja": (
            "日本語のみで逐語的に転写してください。翻訳しないでください。"
            "フランス語や他の言語で繰り返さないでください。放射線科レポート。"
            "用語：胆嚢、胆嚢炎、膵臓、肝臓、脾臓、腎臓、皮質髄質分化、実質。"
        ),
        "tr": (
            "YALNIZCA TÜRKÇE kelimesi kelimesine transkripsiyon. "
            "Çeviri yapmayın. Fransızca veya başka bir dilde tekrar etmeyin. "
            "Radyoloji raporu. "
            "Kelime dağarcığı: safra kesesi, kolesistit, pankreas, karaciğer, dalak, böbrekler."
        ),
        "nl": (
            "Woordelijke transcriptie UITSLUITEND IN HET NEDERLANDS. "
            "Niet vertalen. Niet herhalen in het Frans of een andere taal. "
            "Radiologisch verslag."
        ),
        "pl": (
            "Dosłowna transkrypcja WYŁĄCZNIE PO POLSKU. "
            "Nie tłumaczyć. Nie powtarzać po francusku ani w innym języku. "
            "Raport radiologiczny."
        ),
        "sv": (
            "Ordagrann transkription ENDAST PÅ SVENSKA. "
            "Översätt inte. Upprepa inte på franska eller något annat språk. "
            "Radiologisk rapport."
        ),
        "no": (
            "Ordrett transkripsjon KUN PÅ NORSK. "
            "Ikke oversett. Ikke gjenta på fransk eller et annet språk. "
            "Radiologisk rapport."
        ),
        "da": (
            "Ordret transskription KUN PÅ DANSK. "
            "Oversæt ikke. Gentag ikke på fransk eller et andet sprog. "
            "Radiologisk rapport."
        ),
        "el": (
            "Αυτολεξεί μεταγραφή ΜΟΝΟ ΣΤΑ ΕΛΛΗΝΙΚΑ. "
            "Μην μεταφράζετε. Μην επαναλαμβάνετε στα γαλλικά ή άλλη γλώσσα. "
            "Ακτινολογική έκθεση."
        ),
        "ro": (
            "Transcriere literală NUMAI ÎN ROMÂNĂ. "
            "Nu traduceți. Nu repetați în franceză sau altă limbă. "
            "Raport radiologic ecografic. "
            "Vocabular exact: vezică urinară, vezică biliară, colecistită, "
            "semi-repleție, semi-replețiată, conținut anecoic, anecoic, anecoică, "
            "hipoecoic, hiperecoic, ecogenitate, "
            "pancreas, ficat, splină, rinichi, parenchim, ecografie, CT, IRM, "
            "diferențierea cortico-medulară, efuziune, ascită, hepatomegalie, "
            "splenomegalie, hidronefroză, aortă, vena portă, chist, nodul, "
            "tendon, menisc, cartilaj, recesul subcvadricipital, recesul suprapatelar, "
            "efuziune intraarticulară, sinovită, bursă, ligament, calcifiere, litiază, calcul."
        ),
        "ko": (
            "한국어로만 그대로 받아쓰기하십시오. "
            "번역하지 마십시오. 프랑스어나 다른 언어로 반복하지 마십시오. "
            "방사선과 보고서."
        ),
        "hi": (
            "केवल हिंदी में शब्दशः ट्रांस्क्रिप्शन। "
            "अनुवाद न करें। फ्रेंच या किसी अन्य भाषा में दोहराएं नहीं। "
            "रेडियोलॉजी रिपोर्ट।"
        ),
        "id": (
            "Transkripsi verbatim HANYA DALAM BAHASA INDONESIA. "
            "Jangan menerjemahkan. Jangan mengulangi dalam bahasa Prancis atau bahasa lain. "
            "Laporan radiologi."
        ),
        "th": (
            "การถอดความคำต่อคำเป็นภาษาไทยเท่านั้น "
            "ห้ามแปล ห้ามพูดซ้ำเป็นภาษาฝรั่งเศสหรือภาษาอื่น "
            "รายงานทางรังสีวิทยา"
        ),
        "ms": (
            "Transkripsi verbatim DALAM BAHASA MELAYU SAHAJA. "
            "Jangan terjemah. Jangan ulangi dalam bahasa Perancis atau bahasa lain. "
            "Laporan radiologi."
        ),
    }

    # ── Hallucinations connues (toutes langues) ────────────────────────────
    HALLUCINATION_PATTERNS = [
        # ── Phrases YouTube / réseaux sociaux (FR) ──────────────────────
        r"merci d.avoir regard",
        r"merci pour votre attention",
        r"n.oubliez pas de vous abonn",
        r"sous-titr[eé]",
        r"abonnez.vous",
        r"transcrit par",
        r"la mer de marseille",
        r"de l.enqu[eê]te de l.enqu[eê]te",
        r"vergar\b",
        r"le f.sconomie",
        r"cliquez sur le lien",
        r"laissez un commentaire",
        r"likez cette vid[eé]o",
        r"partagez cette vid[eé]o",

        # ── Phrases YouTube / réseaux sociaux (EN) ──────────────────────
        r"thanks? for watching",
        r"thank you for watching",
        r"don.t forget to (like|subscribe|comment)",
        r"like and subscribe",
        r"hit the (like|subscribe|bell)",
        r"in this video",
        r"click (here|the link|below|on)",
        r"leave a comment",
        r"share this video",
        r"see you (in the next|next time)",
        r"if you (like|enjoyed) this",
        r"smash the like",

        # ── Phrases YouTube (DE) ─────────────────────────────────────────
        r"danke f.rs zuschauen",
        r"danke f.r das zuschauen",
        r"vergiss nicht.*abonnieren",
        r"daumen hoch",
        r"hinterlass.*kommentar",

        # ── Phrases YouTube (ES) ─────────────────────────────────────────
        r"gracias por ver",
        r"gracias por (mirar|verlo)",
        r"no olvides suscribirte",
        r"dale like",
        r"d[eé]jame un comentario",

        # ── Phrases YouTube (IT) ─────────────────────────────────────────
        r"grazie per (aver guardato|la visione)",
        r"non dimenticare di iscriverti",
        r"metti mi piace",
        r"lascia un commento",

        # ── Phrases YouTube (PT) ─────────────────────────────────────────
        r"obrigado por (assistir|ver)",
        r"n.o esque[cç]a de se inscrever",
        r"curte o v[ií]deo",
        r"deixe um coment[aá]rio",

        # ── Phrases YouTube (RO) ─────────────────────────────────────────
        r"v[aă]\s+mul[tț]umesc\s+(frumos\s+)?pentru\s+vizionare",
        r"mul[tț]umesc\s+pentru\s+vizionare",
        r"nu uita[tț]i\s+s[aă]\s+v[aă]\s+abonat[iî]",
        r"d[aă]\s+like",
        r"l[aă]sa[tț]i\s+un\s+comentariu",
        r"p[aâ]n[aă]\s+data\s+viitoare",

        # ── Phrases YouTube (RU) ─────────────────────────────────────────
        r"спасибо за просмотр",
        r"не забудьте подписаться",
        r"ставьте лайк",
        r"оставляйте комментарии",

        # ── Phrases YouTube (TR) ─────────────────────────────────────────
        r"izledi[gğ]iniz i[cç]in te[sş]ekk[uü]r",
        r"abone olmay[ıi] unutmay[ıi]n",
        r"be[gğeni]ni bırak[ıi]n",

        # ── Phrases YouTube (ZH) ─────────────────────────────────────────
        r"感谢(观看|收看)",
        r"别忘了订阅",
        r"点赞",

        # ── Phrases YouTube (JA) ─────────────────────────────────────────
        r"ご視聴ありがとうございます",
        r"チャンネル登録",
        r"いいねを押して",

        # ── Parasites musicaux et techniques (toutes langues) ────────────
        r"remix\b",
        r"\bfeat\.",
        r"www\.",
        r"https?://",
        r"\[musique\]",
        r"\[music\]",
        r"\[applaudissements\]",
        r"\[rires\]",
        r"\[laughter\]",
        r"\[applause\]",
        r"\[inaudible\]",
        r"\[silence\]",
        r"\[bruit\]",
        r"\[noise\]",
        r"©|copyright|all rights reserved",
        r"ghostland",
        r"vendors?\s+[eé]quilibr",
        r"vegas\s+directive",
        r"converteria",
        r"opart",

        # ── Texte de remplissage / hallucinations connues Whisper ────────
        r"transcribed by",
        r"subtitles by",
        r"captions by",
        r"amara\.org",
        r"please subscribe",
        r"\(upbeat music\)",
        r"\(dramatic music\)",
        r"\(gentle music\)",
        r"\[musique douce\]",
        r"aujourd.hui nous allons",
        r"dans cette vid[eé]o",
        r"bienvenue sur (ma cha[iî]ne|notre cha[iî]ne)",
        r"welcome to (my|our) channel",
    ]

    def __init__(self, model_size: str = "medium", language: str = "fr"):
        self.model_size  = model_size
        self.language    = language
        self._model      = None
        self._running    = False
        self._thread     = None
        self._audio_queue: queue.Queue = queue.Queue()
        self._loaded     = False
        self._last_text  = ""   # anti-duplication

        self._hallucination_re = [
            re.compile(p, re.IGNORECASE)
            for p in self.HALLUCINATION_PATTERNS
        ]
        self._bilingual_split = None  # résultat tronqué si hallucination bilingue

        # FIX v6.2 : stocker la langue au niveau module pour survie PyArmor RFT.
        # Si RFT renomme self.language, _get_prompt() et _transcribe_and_call()
        # peuvent toujours lire ce fallback module-level.
        try:
            import sys as _sys
            _mod = _sys.modules.get(__name__) or _sys.modules.get('whisper_dictation')
            if _mod is not None:
                _mod._CURRENT_WHISPER_LANG = language
        except Exception:
            pass

    def _get_prompt(self) -> str:
        """Retourne le prompt médical dans la langue active.

        FIX v6.2 : fallback module-level si PyArmor RFT a renommé self.language.
        Sans ce fallback, self.language introuvable → prompt "en" utilisé même
        en mode roumain → Whisper hallucine du français/anglais dans la sortie.
        """
        lang = getattr(self, 'language', None)
        if not lang:
            try:
                import sys as _sys
                _mod = _sys.modules.get(__name__) or _sys.modules.get('whisper_dictation')
                lang = getattr(_mod, '_CURRENT_WHISPER_LANG', None) if _mod else None
            except Exception:
                lang = None
        lang = lang or 'fr'
        return self.INITIAL_PROMPTS.get(lang, self.INITIAL_PROMPTS["en"])

    # ── Chargement du modèle ──────────────────────────────────────────────
    def load(self, on_progress=None, translations=None):
        """
        Charge le modele Whisper avec progression temps reel.
        Compatible PyArmor : n'utilise pas tqdm (casse apres obfuscation).
        Strategie : patch urllib.request.urlretrieve reporthook (niveau OS,
        transparent pour PyArmor) + timer spinner pour le chargement RAM.
        Installe automatiquement les dependances manquantes au premier lancement.
        """
        # ── Auto-installation si nécessaire ──────────────────────────────
        global WHISPER_AVAILABLE, SOUNDDEVICE_AVAILABLE
        global _whisper_lib, _sd, _np

        if not WHISPER_AVAILABLE or not SOUNDDEVICE_AVAILABLE:
            t = translations or {}

            if _IS_FROZEN:
                # Mode exe PyInstaller : essayer d'abord les imports bundlés,
                # sinon chercher python système pour auto-installer.
                try:
                    import importlib as _il
                    _whisper_lib = _il.import_module("whisper")
                    WHISPER_AVAILABLE = True
                except ImportError:
                    pass

                try:
                    import importlib as _il
                    _sd = _il.import_module("sounddevice")
                    _np = _il.import_module("numpy")
                    SOUNDDEVICE_AVAILABLE = True
                except ImportError:
                    pass

                # Si toujours manquant, tenter l'auto-install via python système
                if not WHISPER_AVAILABLE or not SOUNDDEVICE_AVAILABLE:
                    installing_msg = t.get("whisper_installing", "Installation des composants vocaux...")
                    if on_progress:
                        on_progress(installing_msg)
                    ok = _ensure_dictation_deps(on_progress=on_progress)
                    if ok:
                        # Re-importer après installation
                        try:
                            import importlib as _il
                            _whisper_lib = _il.import_module("whisper")
                            WHISPER_AVAILABLE = True
                        except ImportError as e:
                            raise ImportError(
                                f"openai-whisper manquant et installation impossible : {e}\n"
                                "Lancez manuellement : pip install openai-whisper sounddevice numpy torch"
                            )
                        try:
                            _sd = _il.import_module("sounddevice")
                            _np = _il.import_module("numpy")
                            SOUNDDEVICE_AVAILABLE = True
                        except ImportError as e:
                            raise ImportError(
                                f"sounddevice/numpy manquant et installation impossible : {e}\n"
                                "Lancez manuellement : pip install sounddevice numpy"
                            )
                    else:
                        missing_pkgs = []
                        if not WHISPER_AVAILABLE:
                            missing_pkgs.append("openai-whisper torch")
                        if not SOUNDDEVICE_AVAILABLE:
                            missing_pkgs.append("sounddevice numpy")
                        raise ImportError(
                            f"Dépendances manquantes : {' '.join(missing_pkgs)}\n"
                            "Installez Python depuis https://python.org puis lancez :\n"
                            f"pip install {' '.join(missing_pkgs)}"
                        )
            else:
                # Mode script : auto-installation via pip
                installing_msg = t.get("whisper_installing", "Installation des composants vocaux...")
                if on_progress:
                    on_progress(installing_msg)

                ok = _ensure_dictation_deps(on_progress=on_progress)
                if not ok:
                    raise ImportError(
                        "Impossible d'installer les dependances de la dictee vocale. "
                        "Verifiez votre connexion Internet et reessayez."
                    )

                # Re-importer apres installation
                try:
                    import importlib as _il
                    _whisper_lib = _il.import_module("whisper")
                    WHISPER_AVAILABLE = True
                except ImportError as e:
                    raise ImportError(f"openai-whisper non disponible apres installation : {e}")
                try:
                    _sd = _il.import_module("sounddevice")
                    _np = _il.import_module("numpy")
                    SOUNDDEVICE_AVAILABLE = True
                except ImportError as e:
                    raise ImportError(f"sounddevice/numpy non disponible apres installation : {e}")

        if not WHISPER_AVAILABLE:
            raise ImportError("openai-whisper non installe. pip install openai-whisper")
        if not SOUNDDEVICE_AVAILABLE:
            raise ImportError("sounddevice non installe. pip install sounddevice numpy")

        import urllib.request as _urllib_req
        import time as _time_mod

        t = translations or {}

        def _fmt_bytes(b):
            for u in ("o", "Ko", "Mo", "Go"):
                if b < 1024:
                    return "{:.1f} {}".format(b, u)
                b /= 1024
            return "{:.1f} To".format(b)

        def _fmt_time(s):
            s = int(s)
            if s < 60:
                return "{}s".format(s)
            if s < 3600:
                return "{}m {:02d}s".format(s // 60, s % 60)
            return "{}h {:02d}m".format(s // 3600, (s % 3600) // 60)

        def _msg_connecting():
            return t.get("whisper_connecting", "Connexion au serveur...")

        def _msg_dl(dl, total, pct, speed, remaining):
            tpl = t.get("whisper_downloading",
                        "Telechargement Whisper | {dl} / {total} ({pct}%) | {speed} | {remaining}")
            return tpl.format(dl=dl, total=total, pct=pct, speed=speed, remaining=remaining)

        def _msg_loading(pct):
            tpl = t.get("whisper_loading",
                        "Chargement Whisper en memoire... ({pct}%)")
            return tpl.format(n=int(pct), total=100, pct=int(pct), remaining="")

        def _msg_ready():
            return t.get("whisper_ready", "Modele charge - demarrage...")

        # ── Patch urllib.request.urlretrieve reporthook ────────────────
        # whisper telecharge via torch.hub qui appelle urlretrieve.
        # On wrappe la fonction pour injecter notre reporthook.
        _orig_urlretrieve = _urllib_req.urlretrieve
        _dl_start = [0.0]
        _downloading = [False]

        def _patched_urlretrieve(url, filename=None, reporthook=None, data=None):
            _downloading[0] = True
            _dl_start[0] = _time_mod.time()
            if on_progress:
                on_progress(_msg_connecting())

            def _hook(block_count, block_size, total_size):
                if on_progress is None:
                    return
                downloaded = block_count * block_size
                elapsed = _time_mod.time() - _dl_start[0]
                if total_size > 0 and elapsed > 0.5:
                    pct_val   = min(downloaded / total_size * 100, 100)
                    speed_raw = downloaded / elapsed
                    remain    = (total_size - downloaded) / speed_raw if speed_raw > 0 else 0
                    on_progress(_msg_dl(
                        dl=_fmt_bytes(downloaded),
                        total=_fmt_bytes(total_size),
                        pct="{:.0f}".format(pct_val),
                        speed=_fmt_bytes(speed_raw) + "/s",
                        remaining=_fmt_time(remain),
                    ))
                if reporthook:
                    reporthook(block_count, block_size, total_size)

            return _orig_urlretrieve(url, filename=filename, reporthook=_hook, data=data)

        _urllib_req.urlretrieve = _patched_urlretrieve

        # ── Timer spinner pour le chargement RAM (apres DL) ────────────
        # whisper.load_model() peut prendre 10-30s pour deserialiser
        # le modele en memoire — on anime le bouton pendant ce temps.
        _load_start   = [0.0]
        _spinner_stop = [False]
        _spinner_dots = ["", ".", "..", "..."]

        def _spinner_thread():
            idx = 0
            while not _spinner_stop[0]:
                elapsed = _time_mod.time() - _load_start[0]
                if on_progress and not _downloading[0]:
                    pct_anim = min(int(elapsed / 25 * 100), 95)
                    dots = _spinner_dots[idx % 4]
                    msg = _msg_loading(pct_anim) + dots
                    on_progress(msg)
                idx += 1
                _time_mod.sleep(0.6)

        spinner = threading.Thread(target=_spinner_thread, daemon=True)
        spinner.start()
        _load_start[0] = _time_mod.time()

        # 1. Empêcher les blocages réseau infinis
        import socket
        socket.setdefaulttimeout(15.0)

        # 2. Pointer vers le dossier d'extraction de l'exécutable
        import os
        model_dir = None
        if getattr(sys, 'frozen', False):
            meipass = getattr(sys, '_MEIPASS', None)
            if meipass:
                # Chercher à la racine
                if os.path.exists(os.path.join(meipass, f"{self.model_size}.pt")):
                    model_dir = meipass
                # Chercher dans le sous-dossier whisper_models
                elif os.path.exists(os.path.join(meipass, "whisper_models", f"{self.model_size}.pt")):
                    model_dir = os.path.join(meipass, "whisper_models")

        try:
            # 3. Charger le modèle avec le bon chemin
            self._model = _whisper_lib.load_model(self.model_size, download_root=model_dir)
        finally:
            _spinner_stop[0] = True
            _urllib_req.urlretrieve = _orig_urlretrieve
            _downloading[0] = False

        if on_progress:
            on_progress(_msg_ready())

        self._loaded = True

    # ── Démarrage / arrêt ────────────────────────────────────────────────
    def start(self, on_text_callback):
        if not self._loaded:
            raise RuntimeError("Modèle Whisper non chargé. Appelez load() d'abord.")
        self._running = True
        self._thread  = threading.Thread(
            target=self._listen_loop,
            args=(on_text_callback,),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._running = False
        self._last_text = ""   # reset anti-duplication between sessions

    def set_language(self, lang: str):
        """
        Change la langue active et met à jour le fallback module-level.

        FIX v6.3 : utiliser set_language() au lieu de self._whisper.language = x
        dans Comptes_Rendus.py pour garantir que le fallback _CURRENT_WHISPER_LANG
        est toujours synchronisé après un changement de langue dans l'UI.
        Sans cela, si PyArmor RFT renomme self.language, _get_prompt() et
        _transcribe_and_call() utilisent le fallback — qui pointe encore vers
        l'ancienne langue si on a seulement mis à jour self.language directement.
        """
        self.language = lang
        try:
            import sys as _sys
            _mod = _sys.modules.get(__name__) or _sys.modules.get('whisper_dictation')
            if _mod is not None:
                _mod._CURRENT_WHISPER_LANG = lang
        except Exception:
            pass

    # ── VAD : vérifie si le chunk contient de la voix ────────────────────
    def _has_voice(self, audio_f32: "_np.ndarray") -> bool:
        """Retourne True si le RMS dépasse le seuil VAD."""
        rms = _np.sqrt(_np.mean(audio_f32 ** 2))
        return float(rms) >= self.VAD_THRESHOLD

    # ── Filtre anti-hallucination + anti-duplication ────────────────────
    def _is_hallucination(self, text: str) -> bool:
        """Retourne True si le texte est une hallucination ou une duplication."""
        text_stripped = text.strip()

        # 1. Texte trop court (< 3 mots)
        words = text_stripped.split()
        if len(words) <= 2:
            return True

        # 2. Ratio caractères non-ASCII élevé = charabia
        valid_accents = set("àâçéèêëîïôùûüœæÀÂÇÉÈÊËÎÏÔÙÛÜŒÆäöüßÄÖÜáéíóúñÁÉÍÓÚÑ")
        non_ascii = sum(1 for c in text_stripped if ord(c) > 127 and c not in valid_accents)
        if len(text_stripped) > 0 and non_ascii / len(text_stripped) > 0.12:
            logger.warning(f"[Whisper] Hallucination (charabia) filtrée : {text!r}")
            return True

        # 3. Patterns connus d'hallucination
        for pattern in self._hallucination_re:
            if pattern.search(text_stripped):
                logger.warning(f"[Whisper] Hallucination (pattern) filtrée : {text!r}")
                return True

        # 4. Anti-duplication : similitude > 92% avec le dernier texte transcrit
        if self._last_text:
            ratio = difflib.SequenceMatcher(
                None,
                self._last_text.lower().strip(),
                text_stripped.lower()
            ).ratio()
            if ratio > 0.92:
                logger.warning(f"[Whisper] Duplication filtrée (ratio={ratio:.2f}) : {text!r}")
                return True

        # 5. Détection hallucination bilingue par changement de langue au niveau phrase
        # Whisper hallucine une traduction dans une autre langue après la transcription.
        # Stratégie : détecter le point de bascule lexical entre phrases.
        # FIX v6.4 : utiliser getattr + fallback module-level (PyArmor RFT peut
        # renommer self.language → AttributeError silencieux ou None passé à
        # _detect_language_switch → suppression incorrecte de texte valide).
        _lang_for_detect = getattr(self, 'language', None)
        if not _lang_for_detect:
            try:
                import sys as _sys
                _mod = _sys.modules.get(__name__) or _sys.modules.get('whisper_dictation')
                _lang_for_detect = getattr(_mod, '_CURRENT_WHISPER_LANG', 'fr') if _mod else 'fr'
            except Exception:
                _lang_for_detect = 'fr'
        clean_result = _detect_language_switch(text_stripped, _lang_for_detect)
        if clean_result != text_stripped:
            logger.warning(
                f"[Whisper] Hallucination bilingue tronquée : {text_stripped[:80]!r}"
                f" → {clean_result[:60]!r}"
            )
            self._bilingual_split = clean_result
            return False  # sera géré par _transcribe_and_call

        # 6. Détection bilingue par similarité des moitiés (filet de sécurité)
        if len(words) >= 10:
            half = len(words) // 2
            first_half  = " ".join(words[:half]).lower()
            second_half = " ".join(words[half:]).lower()
            bilingual_ratio = difflib.SequenceMatcher(
                None, first_half, second_half
            ).ratio()
            if bilingual_ratio > 0.45:
                logger.warning(
                    f"[Whisper] Hallucination bilingue (ratio={bilingual_ratio:.2f}) : {text[:80]!r}"
                )
                self._bilingual_split = " ".join(words[:half])
                return False

        self._bilingual_split = None
        return False

    # ── Boucle d'écoute ──────────────────────────────────────────────────
    # ── Boucle d'écoute ──────────────────────────────────────────────────
    def _listen_loop(self, callback):
        frames_per_block = self.SAMPLE_RATE * self.BLOCK_SECONDS

        _transcribe_lock = threading.Lock()

        def _safe_transcribe(audio_data):
            if _transcribe_lock.acquire(blocking=False):
                try:
                    self._transcribe_and_call(audio_data, callback)
                finally:
                    _transcribe_lock.release()
            else:
                logger.debug("[Whisper] Transcription already running, skipping overlapping block")

        def _audio_cb(indata, frames, time_info, status):
            self._audio_queue.put(indata.copy())

        def _find_input_device():
            try:
                devices = _sd.query_devices()
                default_idx = _sd.default.device[0]

                if (default_idx is not None
                        and isinstance(default_idx, int)
                        and default_idx >= 0):
                    dev = devices[default_idx]
                    if dev.get('max_input_channels', 0) > 0:
                        return default_idx

                for idx, dev in enumerate(devices):
                    if dev.get('max_input_channels', 0) > 0:
                        return idx

                raise RuntimeError("Aucun microphone détecté...")
            except Exception as e:
                if "Aucun microphone" in str(e):
                    raise
                return None 

        def _find_sample_rate(device_idx):
            for rate in [self.SAMPLE_RATE, 44100, 48000, 22050]:
                try:
                    _sd.check_input_settings(
                        device=device_idx,
                        channels=self.CHANNELS,
                        dtype=self.DTYPE,
                        samplerate=rate,
                    )
                    return rate
                except Exception:
                    continue
            raise RuntimeError("Le microphone ne supporte aucun sample rate compatible.")

        try:
            device_idx  = _find_input_device()
            sample_rate = _find_sample_rate(device_idx)
        except RuntimeError as e:
            callback(f"\n⚠️ {e}\n")
            self._running = False
            return

        try:
            stream_ctx = _sd.InputStream(
                samplerate=sample_rate,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                blocksize=sample_rate,
                device=device_idx,
                callback=_audio_cb,
            )
        except Exception as e:
            callback(f"\n⚠️ Impossible d'ouvrir le microphone : {e}\n")
            self._running = False
            return

        with stream_ctx:
            accumulated        = []
            accumulated_frames = 0
            silent_chunks      = 0
            _frames_per_block = sample_rate * self.BLOCK_SECONDS

            while self._running:
                # --- FIX : Tuer les processus fantômes (empêche le clonage) ---
                if self._thread is not threading.current_thread():
                    break
                
                try:
                    chunk = self._audio_queue.get(timeout=1.0)
                    chunk_f32 = chunk.flatten()

                    if self._has_voice(chunk_f32):
                        silent_chunks = 0
                        accumulated.append(chunk_f32)
                        accumulated_frames += len(chunk_f32)

                        if accumulated_frames >= _frames_per_block:
                            audio_data = _np.concatenate(accumulated)
                            accumulated        = []
                            accumulated_frames = 0
                            threading.Thread(
                                target=_safe_transcribe,
                                args=(audio_data,),
                                daemon=True,
                            ).start()
                    else:
                        silent_chunks += 1
                        if silent_chunks >= 2 and accumulated:
                            audio_data = _np.concatenate(accumulated)
                            accumulated        = []
                            accumulated_frames = 0
                            silent_chunks      = 0
                            threading.Thread(
                                target=_safe_transcribe,
                                args=(audio_data,),
                                daemon=True,
                            ).start()

                except queue.Empty:
                    if accumulated:
                        audio_data = _np.concatenate(accumulated)
                        accumulated        = []
                        accumulated_frames = 0
                        silent_chunks      = 0
                        threading.Thread(
                            target=_safe_transcribe,
                            args=(audio_data,),
                            daemon=True,
                        ).start()

    # ── Transcription ─────────────────────────────────────────────────────
    def _transcribe_and_call(self, audio_f32: "_np.ndarray", callback):
        try:
            # VAD globale sur le bloc complet
            if not self._has_voice(audio_f32):
                logger.debug("[Whisper] Bloc silencieux ignoré")
                return

            # FIX v6.2 : lire la langue via getattr pour survie PyArmor RFT.
            # Si RFT renomme self.language, on lit le fallback module-level.
            _lang = getattr(self, 'language', None)
            if not _lang:
                try:
                    import sys as _sys
                    _mod = _sys.modules.get(__name__) or _sys.modules.get('whisper_dictation')
                    _lang = getattr(_mod, '_CURRENT_WHISPER_LANG', 'fr') if _mod else 'fr'
                except Exception:
                    _lang = 'fr'

            result = self._model.transcribe(
                audio_f32,
                language=_lang,
                task="transcribe",               # JAMAIS "translate" — transcription pure
                fp16=False,
                initial_prompt=self._get_prompt(),
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                logprob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                # temperature=0 → déterministe mais hallucinations bilingues
                # Fallback à 0.2 si le résultat est suspect (géré par Whisper)
                temperature=[0.0, 0.2, 0.4],  # liste = fallback progressif si résultat suspect
            )

            text = result.get("text", "").strip()

            if not text:
                return

            # ── Dédoublonnage intra-résultat ─────────────────────────────
            # Whisper retourne parfois la même phrase deux fois dans un seul
            # appel transcribe() quand il est incertain sur la frontière de segment.
            text = _deduplicate_sentences(text)

            if not text:
                return

            # FIX thread-safety: use local variable instead of shared instance
            # attribute self._bilingual_split, which could be clobbered by a
            # concurrent call from another thread.
            _bilingual_split_local = None

            # Temporarily store result in instance for _is_hallucination compatibility,
            # then capture it immediately into local scope.
            self._bilingual_split = None  # reset avant test
            if self._is_hallucination(text):
                return

            # Capture immediately after _is_hallucination() sets it
            _bilingual_split_local = getattr(self, "_bilingual_split", None)

            # Si une hallucination bilingue a été détectée, utiliser seulement
            # la première moitié (la vraie transcription dans la bonne langue)
            if _bilingual_split_local:
                logger.info(
                    f"[Whisper] [{_lang}] Hallucination bilingue tronquée : "
                    f"{text[:80]!r} → {_bilingual_split_local!r}"
                )
                text = _bilingual_split_local

            if not text:
                return

            # Mémoriser pour anti-duplication
            self._last_text = text

            logger.info(f"[Whisper] [{_lang}] Transcrit : {text!r}")

            # ── Correction médicale Gemini (tourne déjà dans un thread BG) ──
            try:
                from correction import correct_text, is_text_too_different
                corrected = correct_text(text)
                final_text = text if is_text_too_different(text, corrected) else corrected
            except Exception as _corr_err:
                logger.warning(f"[Whisper] Correction ignorée : {_corr_err}")
                final_text = text

            callback(final_text)

        except Exception as e:
            logger.error(f"[Whisper] Erreur transcription : {e}")


# ============ CORRECTEUR RADIOLOGIQUE POST-TRANSCRIPTION ============


class RadioCorrector:
    """
    Correcteur post-transcription spécialisé radiologie.
    Corrige les erreurs phonétiques typiques de Whisper sur le vocabulaire médical.
    Les corrections sont triées du plus long au plus court pour éviter les conflits.
    """

    # ── Dictionnaire de corrections (clé = regex insensible à la casse) ──
    # Format : "mauvaise transcription phonétique" -> "terme correct"
    CORRECTIONS = {
        # ══ VÉSICULE BILIAIRE & VOIES BILIAIRES ══
        r"\bvésicul[e]?\s*-?\s*bili[eèa]r[e]?\b":               "vésicule biliaire",
        r"\bvésiculbili[eèa]r[e]?\b":                             "vésicule biliaire",
        r"\bvésicul[e]?\s+billi[eèa]r[e]?\b":                    "vésicule biliaire",
        r"\bvesicul[e]?\s*biliaire\b":                           "vésicule biliaire",
        r"\bvésicul[eo]\s*-?\s*biliaire\b":                     "vésicule biliaire",
        r"\bvésiculbiliaire\b":                                   "vésiculobiliaire",
        r"\bvési[ck]ul[oe]biliaire\b":                            "vésiculobiliaire",
        r"\bcholestite\b":                                        "cholécystite",
        r"\bcholécystit[e]?\b":                                   "cholécystite",
        r"\bcholélithias[e]?\b":                                  "cholélithiase",
        r"\bcholedoque\b":                                        "cholédoque",
        r"\bVBP\b":                                               "VBP",
        r"\bangiocholite\b":                                      "angiocholite",
        r"\bcalcul[s]?\s+biliaire[s]?\b":                        "calcul biliaire",
        r"\bcalcul[s]?\s+vésiculaire[s]?\b":                     "calcul vésiculaire",
        r"\bvoie[s]?\s+biliaire[s]?\s+principale[s]?\b":        "voie biliaire principale",

        # ══ REINS & VOIES URINAIRES ══
        r"\bmoins\s+de\s+diff[eé]rentes?\s+sessions?\s+cortico[- ]?m[eé]dull\w*\b": "diminution de la différenciation cortico-médullaire",
        r"\bmoins\s+de\s+diff[eé]renciation\s+cortico[- ]?m[eé]dull\w*\b":           "diminution de la différenciation cortico-médullaire",
        r"\bdiff[eé]rentes?\s+sessions?\s+cortico[- ]?m[eé]dull\w*\b":                "différenciation cortico-médullaire",
        r"\bdiff[eé]renci[ao]tion\s+cortico[- ]?m[eé]dullaire\b":                       "différenciation cortico-médullaire",
        r"\bdiff[eé]rentiation\s+cortico[- ]?m[eé]dullaire\b":                          "différenciation cortico-médullaire",
        r"\bsessions?\s+cortico[- ]?m[eé]dull\w*\b":                                   "différenciation cortico-médullaire",
        r"\bjoncti[oe]n[s]?\s+cortico[- ]?m[eé]dull\w*\b":                             "différenciation cortico-médullaire",
        r"\bnéphromégalie\b":                                     "néphromégalie",
        r"\bnéphrolithiase\b":                                    "néphrolithiase",
        r"\burolithiase\b":                                       "urolithiase",
        r"\bhydronéphrose\b":                                     "hydronéphrose",
        r"\burétéro-hydronéphrose\b":                             "urétéro-hydronéphrose",
        r"\bpyélonéphrite\b":                                     "pyélonéphrite",
        r"\bpyélo-caliciel\b":                                    "pyélo-caliciel",
        r"\bpyélocaliciel\b":                                     "pyélo-caliciel",
        r"\bsinus\s+rénal\b":                                    "sinus rénal",
        r"\bcortex\s+rénal\b":                                   "cortex rénal",

        # ══ FOIE ══
        r"\bhépato[- ]?mégalie\b":                                "hépatomégalie",
        r"\bstéa[dt]ose\b":                                       "stéatose",
        r"\bcirr[eo]se\b":                                        "cirrhose",
        r"\bhémangi[ao]me\b":                                     "hémangiome",
        r"\bparenchyme\s+hépatique\b":                           "parenchyme hépatique",
        r"\béchogénicit[eé]\b":                                   "échogénicité",
        r"\bhyperéchog[eè]ne\b":                                  "hyperéchogène",
        r"\bhypoéchog[eè]ne\b":                                   "hypoéchogène",
        r"\bisoéchog[eè]ne\b":                                    "isoéchogène",
        r"\banéchog[eè]ne\b":                                     "anéchogène",
        r"\béchog[eè]ne\b":                                       "échogène",

        # ══ PANCRÉAS ══
        r"\bpancr[eé]a\b":                                        "pancréas",
        r"\bpancr[eé]as\b":                                       "pancréas",
        r"\bpancréatique\b":                                      "pancréatique",
        r"\bpancréatite\b":                                       "pancréatite",
        r"\bcanal\s+de\s+wirsung\b":                            "canal de Wirsung",
        r"\bwirsung\b":                                           "Wirsung",

        # ══ RATE ══
        r"\bspléno[- ]?mégalie\b":                                "splénomégalie",
        r"\bsplen[oe]mégalie\b":                                  "splénomégalie",

        # ══ RÉPÉTITIONS / BÉGAIEMENTS ══
        r"\bsans\s+sans\b":                                      "sans",
        r"\bet\s+et\b":                                          "et",
        r"\ble\s+le\b":                                          "le",
        r"\bla\s+la\b":                                          "la",
        r"\bde\s+de\b":                                          "de",

        # ══ AORTE & VAISSEAUX ══
        r"\btronc\s+c[oœ]li[ae]que\b":                          "tronc cœliaque",
        r"\bartère[s]?\s+mésentérique[s]?\b":                    "artère mésentérique",
        r"\bveine\s+porte\b":                                    "veine porte",
        r"\bveine[s]?\s+sus[- ]?hépatique[s]?\b":               "veines sus-hépatiques",
        r"\baorte\s+abdominale\b":                               "aorte abdominale",
        r"\banévrisme\b":                                         "anévrisme",
        r"\banévrysme\b":                                         "anévrisme",

        # ══ TAILLE / CONTOURS ══
        r"\bnormal[e]?,?\s+de\s+contours?\s+r[eé]gulier[s]?\b": "normale, de contours réguliers",
        r"\bde\s+taille\s+normal[e]?\b":                        "de taille normale",
        r"\bde\s+contours?\s+r[eé]gulier[s]?\b":               "de contours réguliers",
        r"\bde\s+contours?\s+irr[eé]gulier[s]?\b":             "de contours irréguliers",
        r"\btaille\s+normal[e]?\b":                              "taille normale",
        r"\bcontours?\s+r[eé]gulier[s]?\b":                     "contours réguliers",
        r"\bcontours?\s+irr[eé]gulier[s]?\b":                   "contours irréguliers",

        # ══ DISTENSION ══
        r"\bdisten[dt]?u[e]?\b":                                  "distendue",
        r"\bdistandu[e]?\b":                                      "distendue",

        # ══ PATHOLOGIES COURANTES ══
        r"\blithiasique\b":                                       "lithiasique",
        r"\blithias[e]?\b":                                       "lithiase",
        r"\bmicrolithiase\b":                                     "microlithiase",
        r"\bcalcification[s]?\b":                                 "calcification",
        r"\bascite\b":                                            "ascite",
        r"\bépanchement\s+liquidien\b":                          "épanchement liquidien",
        r"\bépanchement\s+péritonéal\b":                         "épanchement péritonéal",
        r"\bsigne\s+de\s+murphy\b":                             "signe de Murphy",
        r"\bshadowing\b":                                         "shadowing acoustique",

        # ══ ORTHOGRAPHE / ACCENTS ══
        r"\baig[uü][eë]?\b":                                      "aiguë",
        r"\béchographique[s]?\b":                                 "échographique",
        r"\béchographie\b":                                       "échographie",
        r"\baspects?\s+[eé]chographiques?\b":                    "aspect échographique",

        # ══ ANATOMIE TOPOGRAPHIQUE ══
        r"\bhypocondre\s+droit\b":                               "hypocondre droit",
        r"\bhypocondre\s+gauche\b":                              "hypocondre gauche",
        r"\bfosse\s+iliaque\s+droite\b":                        "fosse iliaque droite",
        r"\bfosse\s+iliaque\s+gauche\b":                        "fosse iliaque gauche",
        r"\bespace\s+de\s+morrison\b":                          "espace de Morrison",
        r"\bespace\s+de\s+douglas\b":                           "espace de Douglas",

        # ══ QUALIFICATIFS ══
        r"\bhomog[eè]ne\b":                                       "homogène",
        r"\bhétérog[eè]ne\b":                                     "hétérogène",
        r"\bfinement\s+hétérog[eè]ne\b":                         "finement hétérogène",

        # ══ CONCLUSIONS ══
        r"\bpas\s+d.anomalie\s+décel[eé]e\b":                   "pas d'anomalie décelée",
        r"\bsans\s+particularit[eé]\b":                          "sans particularité",
        r"\baspects?\s+en\s+faveur\b":                          "aspect en faveur",
        r"\bcompatible\s+avec\b":                                "compatible avec",
        r"\ben\s+faveur\s+d[e']\b":                            "en faveur d'",

        # ══ VESSIE & PROSTATE ══
        r"\bvessi[e]?\b":                                         "vessie",
        r"\bparoi[s]?\s+v[eé]sical[e]?s?\b":                      "paroi vésicale",
        r"\bépaisseur\s+pari[eé]tal[e]?\b":                        "épaisseur pariétale",
        r"\br[eé]sidu\s+post-?mictionnel\b":                     "résidu post-mictionnel",
        r"\bRPM\b":                                               "RPM",
        r"\bprostat[e]?\b":                                       "prostate",
        r"\bvolum[e]?\s+prostati[ck]u[e]?\b":                     "volume prostatique",
        r"\bhypertrophi[e]?\s+b[eé]nign[e]?\b":                   "hypertrophie bénigne",
        r"\bad[eé]nom[e]?\s+prostati[ck]u[e]?\b":                 "adénome prostatique",

        # ══ APPENDICE & TUBE DIGESTIF ══
        r"\bappendic[e]?\b":                                      "appendice",
        r"\bappendicit[e]?\b":                                    "appendicite",
        r"\bstercolith[e]?\b":                                    "stercolithe",
        r"\bdifférenciation\s+des\s+tunique[s]?\b":               "différenciation des tuniques",
        r"\banse[s]?\s+gr[eê]le[s]?\b":                           "anses grêles",
        r"\bganglion[s]?\s+m[eé]sent[eé]ri[ck]ue[s]?\b":           "ganglions mésentériques",
        r"\bad[eé]nom[eé]galie[s]?\b":                            "adénomégalie",

        # ══ SURRÉNALES ══
        r"\bsurr[eé]nal[e]?s?\b":                                 "surrénale",
        r"\bloge[s]?\s+surr[eé]nalienne[s]?\b":                   "loge surrénale",
        r"\bnodul[e]?\s+surr[eé]nalien\b":                         "nodule surrénalien",

        # ══ VASCULAIRE (COMPLÉMENTS) ══
        r"\bveine\s+cav[e]\s+inf[eé]rieur[e]\b":                  "veine cave inférieure",
        r"\bVCI\b":                                               "VCI",
        r"\baxe\s+spl[eé]no-?portal\b":                           "axe spléno-portal",
        r"\bart[eè]re[s]?\s+r[eé]nal[e]?s?\b":                    "artères rénales",
        r"\bdiffluence\s+veineus[e]?\b":                          "diffluence veineuse",

        # ══ LÉSIONS & MASSES ══
        r"\bkyst[e]?\s+r[eé]nal\b":                               "kyste rénal",
        r"\bkyst[e]?\s+biliair[e]\b":                             "kyste biliaire",
        r"\bformation\s+kysti[ck]u[e]?\b":                        "formation kystique",
        r"\bformation\s+tissulair[e]\b":                          "formation tissulaire",
        r"\bmass[e]\s+solide\b":                                  "masse solide",
        r"\bnodul[e]?s?\s+h[eé]patiques?\b":                      "nodule hépatique",
        r"\bcalcifi[eé]s?\b":                                     "calcifié",

        # ══ DOPPLER & FLUX ══
        r"\bdoppler\b":                                           "Doppler",
        r"\bflux\s+an[dt][eé]rograd[e]\b":                        "flux antérograde",
        r"\bv[eé]locit[eé]\b":                                    "vélocité",
        r"\bvascularisation\b":                                   "vascularisation",

        # ══ DIVERS / THORAX ══
        r"\bépanchement\s+pleural\b":                             "épanchement pleural",
        r"\bcul[- ]de[- ]sac\b":                                  "cul-de-sac",
        r"\bbase\s+pulmonair[e]\b":                               "base pulmonaire",

        # ══ MESURES & ÉTAT ══
        r"\bdiam[eè]tr[e]\b":                                     "diamètre",
        r"\bgrand\s+ax[e]\b":                                     "grand axe",
        r"\btransversal\b":                                       "transversal",
        r"\banormalement\b":                                      "anormalement",
        r"\binfiltrat\b":                                         "infiltrat",

        # ══ GYNÉCOLOGIE & PELVIS ══
        r"\but[eé]rus\b":                                         "utérus",
        r"\but[eé]rin[e]?\b":                                     "utérin",
        r"\bendo[- ]?m[eè]tr[e]\b":                               "endomètre",
        r"\bmyo[- ]?m[eè]tr[e]\b":                                "myomètre",
        r"\bovair[e]s?\b":                                        "ovaire",
        r"\bfollicul[e]s?\b":                                     "follicule",
        r"\bant[eé]vers[eé]\b":                                   "antéversé",
        r"\br[eé]trovers[eé]\b":                                  "rétroversé",
        r"\bdouglas\b":                                           "Douglas",
        r"\bm[eé]nopause\b":                                      "ménopause",

        # ══ THYROÏDE & COU ══
        r"\bthyro[iï]d[e]\b":                                     "thyroïde",
        r"\bisthm[e]\s+thyro[iï]dien\b":                          "isthme thyroïdien",
        r"\blobe\s+droit\b":                                      "lobe droit",
        r"\blobe\s+gauche\b":                                     "lobe gauche",
        r"\bparathyro[iï]d[e]\b":                                 "parathyroïde",
        r"\bgoitre\b":                                            "goitre",

        # ══ GANGLIONS / LYMPHATIQUES ══
        r"\bad[eé]nopathi[e]s?\b":                                "adénopathie",
        r"\blymphocent[re]s?\b":                                  "lymphocentre",
        r"\bhile\s+graisseux\b":                                  "hile graisseux",
        r"\binfra[- ]?centim[eé]tri[ck]ue\b":                     "infracentimétrique",

        # ══ OSTÉO-ARTICULAIRE ══
        r"\btendon\b":                                            "tendon",
        r"\bt[eé]nosynovite\b":                                   "ténosynovite",
        r"\bbours[e]\s+s[eé]reus[e]\b":                           "bourse séreuse",
        r"\bintra[- ]?articulair[e]\b":                           "intra-articulaire",
        r"\bfissur[e]\b":                                         "fissure",
        r"\bruptur[e]\b":                                         "rupture",

        # ══ DESCRIPTIFS GÉNÉRIQUES ══
        r"\b[eé]paississement\b":                                 "épaississement",
        r"\b[eé]panchement\b":                                    "épanchement",
        r"\binfiltration\b":                                      "infiltration",
        r"\bplage[s]?\b":                                         "plages",
        r"\bfoyer[s]?\b":                                         "foyer",
        r"\bponctu[eé]s?\b":                                      "ponctué",
        r"\bcloisons?\b":                                         "cloison",
        r"\bremaniement[s]?\b":                                   "remaniement",

        # ══ LOCALISATION & ORIENTATION ══
        r"\bsagittal\b":                                          "sagittal",
        r"\baxial\b":                                             "axial",
        r"\bcoronal\b":                                           "coronal",
        r"\banth[eé]rieur\b":                                     "antérieur",
        r"\bpost[eé]rieur\b":                                     "postérieur",
        r"\bproximal\b":                                          "proximal",
        r"\bdistal\b":                                            "distal",

        # ══ QUALITÉ D'EXAMEN ══
        r"\bgaz\s+digestifs?\b":                                  "gaz digestifs",
        r"\binterposition\b":                                     "interposition",
        r"\bexamen\s+limit[eé]\b":                                "examen limité",
        r"\bconditions?\s+di[f]+icil[e]s?\b":                     "conditions difficiles",

        # ══ SCROTUM & TESTICULES ══
        r"\btesticul[e]s?\b":                                     "testicule",
        r"\b[eé]pididym[e]s?\b":                                  "épididyme",
        r"\bhydroc[eè]l[e]\b":                                    "hydrocèle",
        r"\bvaricoc[eè]l[e]\b":                                   "varicocèle",
        r"\bvaginal[e]\b":                                        "vaginale",
        r"\bmicrolithiase[s]?\s+testiculair[e]s?\b":              "microlithiase testiculaire",
        r"\balbugine\b":                                          "albuginée",

        # ══ SÉNOLOGIE (SEIN) ══
        r"\bmamm[ao]ir[e]s?\b":                                   "mammaire",
        r"\bqran[dt]\s+cadran\b":                                 "cadran",
        r"\bQSI\b":                                               "QSI", # Quadrant Supéro-Interne
        r"\bQSE\b":                                               "QSE", # Quadrant Supéro-Externe
        r"\bQII\b":                                               "QII",
        r"\bQIE\b":                                               "QIE",
        r"\bproth[eè]s[e]s?\b":                                   "prothèse",
        r"\bfibroad[eé]nom[e]\b":                                 "fibroadénome",
        r"\bmastit[e]\b":                                         "mastite",
        r"\br[eé]tro[- ]mamelonnaire\b":                          "rétro-mamelonnaire",
        r"\bBIRADS\b":                                            "BI-RADS",

        # ══ VASCULAIRE PÉRIPHÉRIQUE ══
        r"\bcarotid[e]s?\b":                                      "carotide",
        r"\bbifurcation\b":                                       "bifurcation",
        r"\bplaque\s+ath[eé]romateuse\b":                         "plaque athéromateuse",
        r"\bst[eé]nos[e]\b":                                      "sténose",
        r"\bthrombos[e]\b":                                       "thrombose",
        r"\bsaph[eè]ne\b":                                        "saphène",
        r"\bperm[eé]abilit[eé]\b":                                "perméabilité",
        r"\bflux\s+triphasi[ck]ue\b":                             "flux triphasique",

        # ══ TISSUS MOUS & PAROI ══
        r"\blipom[e]\b":                                          "lipome",
        r"\bh[eé]matom[e]\b":                                     "hématome",
        r"\bherni[e]\b":                                          "hernie",
        r"\beventration\b":                                       "éventration",
        r"\bcollection\s+liquidi[e]?nne\b":                       "collection liquidienne",
        r"\bsous[- ]cutan[eé]\b":                                 "sous-cutané",
        r"\bapon[eé]vros[e]\b":                                   "aponévrose",

        # ══ TERMES DE SUIVI & LOGISTIQUE ══
        r"\bcomparai?son\b":                                      "comparaison",
        r"\bant[eé]riorit[eé]s?\b":                               "antériorités",
        r"\bsurveillance\b":                                      "surveillance",
        r"\bcontr[oô]l[e]\b":                                     "contrôle",
        r"\bconfrontation\s+biologique\b":                        "confrontation biologique",
        r"\bexamen\s+ant[eé]rieur\b":                             "examen antérieur",
        r"\bà\s+revoir\b":                                        "à revoir",

        # ══ PATHOLOGIES INFECTIEUSES / DIVERS ══
        r"\babsc[eè]s\b":                                         "abcès",
        r"\bsuinter?ment\b":                                      "suintement",
        r"\bfistul[e]\b":                                         "fistule",
        r"\bn[eé]cros[e]\b":                                      "nécrose",

        # ══ RACHIS & OS (SOUVENT VISIBLES EN TDM/IRM) ══
        r"\bra[ck]is\b":                                          "rachis",
        r"\bvert[eé]bral[e]s?\b":                                 "vertébrale",
        r"\bdiscal[e]s?\b":                                       "discale",
        r"\bost[eé]ophyt[e]s?\b":                                 "ostéophyte",
        r"\bd[eé]bord\s+discal\b":                                "débord discal",
        r"\bforamen\b":                                           "foramen",
        r"\bcanal\s+rachidien\b":                                 "canal rachidien",
        r"\bpincement\b":                                         "pincement",
        r"\bspondylolisth[eé]sis\b":                              "spondylolisthésis",

        # ══ THORAX & POUMONS (COMPLÉMENTS) ══
        r"\bparenchyme\s+pulmonair[e]\b":                         "parenchyme pulmonaire",
        r"\bnodul[e]s?\s+pulmonair[e]s?\b":                       "nodule pulmonaire",
        r"\bscissur[e]\b":                                        "scissure",
        r"\bat[eé]lectasi[e]\b":                                  "atélectasie",
        r"\bemphys[eè]m[e]\b":                                    "emphysème",
        r"\bpneumothorax\b":                                      "pneumothorax",
        r"\bsilhouett[e]\s+cardia[ck]ue\b":                       "silhouette cardiaque",
        r"\bm[eé]diastin\b":                                      "médiastin",

        # ══ TECHNIQUES SCANNER / IRM / CONTRASTE ══
        r"\bproduit\s+de\s+contrast[e]\b":                        "produit de contraste",
        r"\binjection\b":                                         "injection",
        r"\btemps\s+art[eé]riel\b":                                "temps artériel",
        r"\btemps\s+portal\b":                                    "temps portal",
        r"\btemps\s+tardif\b":                                    "temps tardif",
        r"\bunité[s]?\s+hounsfield\b":                            "unités Hounsfield",
        r"\bUH\b":                                                "UH",
        r"\bpond[eé]ration\b":                                    "pondération",
        r"\bséquenc[e]\b":                                        "séquence",
        r"\bart[eé]fact[s]?\b":                                   "artéfact",

        # ══ OBSTÉTRIQUE (SI PERTINENT) ══
        r"\bgrossess[e]\b":                                       "grossesse",
        r"\bsac\s+gestationnel\b":                                "sac gestationnel",
        r"\bembryon\b":                                           "embryon",
        r"\bactivit[eé]\s+cardia[ck]ue\b":                        "activité cardiaque",
        r"\bli[ck]uid[e]\s+amnioti[ck]ue\b":                      "liquide amniotique",
        r"\btrophoblast[e]\b":                                    "trophoblaste",

        # ══ TERMES ADMINISTRATIFS & STRUCTURE ══
        r"\badress[eé]\s+par\b":                                  "adressé par",
        r"\bmotif\s+de\s+l.examen\b":                             "motif de l'examen",
        r"\bconclusion\b":                                        "CONCLUSION",
        r"\br[eé]sultat[s]?\b":                                   "RÉSULTATS",
        r"\bclinique\b":                                          "clinique",
        r"\banamn[eè]s[e]\b":                                     "anamnèse",

        # ══ ORIENTATION & DIVERS ══
        r"\bcontig[uü][eë]s?\b":                                  "contiguës",
        r"\bsuspicion\b":                                         "suspicion",
        r"\bétag[eé]\b":                                          "étage",
        r"\bsym[eé]tri[ck]ue\b":                                  "symétrique",

        # ══ PROTOCOLES & SÉQUENCES IRM ══
        r"\bpond[eé]ration\s+T1\b":                               "pondération T1",
        r"\bpond[eé]ration\s+T2\b":                               "pondération T2",
        r"\bpond[eé]r[eé]\s+T[12]\b":                             "pondéré T1/T2",
        r"\bflair\b":                                             "FLAIR",
        r"\bstir\b":                                              "STIR",
        r"\bdiffusion\b":                                         "diffusion",
        r"\bcart[e]\s+ADC\b":                                     "carte ADC",
        r"\bcoefficient\s+de\s+diffusion\b":                      "coefficient de diffusion",
        r"\bséquence\s+en\s+écho\s+de\s+gradient\b":              "écho de gradient",
        r"\bEG\s+T2\b":                                           "EG T2*",
        r"\btenseur\s+de\s+diffusion\b":                          "tenseur de diffusion",
        r"\bsaturation\s+du\s+gras\b":                            "saturation de la graisse",
        r"\bfat[- ]?sat\b":                                       "Fat-Sat",
        r"\bdixon\b":                                             "Dixon",

        # ══ PRODUITS DE CONTRASTE & REHAUSSEMENT ══
        r"\bgadolinium\b":                                        "gadolinium",
        r"\binjection\s+de\s+gadolinium\b":                       "injection de gadolinium",
        r"\brehaussement\s+tardif\b":                             "rehaussement tardif",
        r"\bprise\s+de\s+contraste\b":                            "prise de contraste",
        r"\bhyposignal\b":                                        "hyposignal",
        r"\bhypersignal\b":                                       "hypersignal",
        r"\bisosignal\b":                                         "isosignal",

        # ══ EXAMENS SPÉCIFIQUES IRM ══
        r"\bbili[- ]?irm\b":                                      "Bili-IRM",
        r"\bcholangio[- ]?irm\b":                                 "Cholangio-IRM",
        r"\bent[eé]ro[- ]?irm\b":                                 "Entéro-IRM",
        r"\bangio[- ]?irm\b":                                     "Angio-IRM",
        r"\bpelvi[- ]?irm\b":                                     "Pelvi-IRM",
        r"\birm\s+cardia[ck]ue\b":                                "IRM cardiaque",
        r"\birm\s+mammair[e]\b":                                  "IRM mammaire",
        r"\birm\s+prostati[ck]u[e]\b":                            "IRM prostatique",

        # ══ ARTEFACTS & PHYSIQUE ══
        r"\bsusceptibilit[eé]\s+magn[eé]ti[ck]ue\b":              "susceptibilité magnétique",
        r"\bart[eé]fact\s+de\s+mouvement\b":                      "artéfact de mouvement",
        r"\balias\s*ing\b":                                       "aliasing",
        r"\bchamp\s+magn[eé]ti[ck]ue\b":                          "champ magnétique",
        r"\b[eé]metteur\s+r[eé]cepteur\b":                        "émetteur-récepteur",
        r"\bantenn[e]\b":                                         "antenne",

        # ══ NEURO-IRM (SPÉCIFIQUE) ══
        r"\bsubstanc[e]\s+blanc[h]e\b":                           "substance blanche",
        r"\bsubstanc[e]\s+gris[e]\b":                             "substance grise",
        r"\bsillon[s]?\s+corticaux\b":                            "sillons corticaux",
        r"\bsyst[eè]me\s+ventriculair[e]\b":                      "système ventriculaire",
        r"\bespac[e]s?\s+de\s+virchow[- ]robin\b":                "espaces de Virchow-Robin",
        # ══ NEURO-IMAGERIE (CERVEAU & MOELLE) ══
        r"\bparenchym[e]\s+c[eé]r[eé]bral\b":                    "parenchyme cérébral",
        r"\blign[e]\s+m[eé]dian[e]\b":                            "ligne médiane",
        r"\bstruc?tur[e]s\s+m[eé]dian[e]s\b":                     "structures médianes",
        r"\bventricul[e]s\s+lat[eé]raux\b":                       "ventricules latéraux",
        r"\bh[eé]misph[eè]r[e]\b":                                "hémisphère",
        r"\bfoss[e]\s+post[eé]rieur[e]\b":                        "fosse postérieure",
        r"\btronc\s+c[eé]r[eé]bral\b":                            "tronc cérébral",
        r"\bcervel[e]t\b":                                        "cervelet",
        r"\bhypophys[e]\b":                                       "hypophyse",
        r"\bsinus\s+veineux\b":                                   "sinus veineux",
        r"\bm[eé]ning[e]s\b":                                     "méninges",

        # ══ CLASSIFICATIONS RADS (STANDARDS) ══
        r"\bti[- ]?rads\b":                                       "TI-RADS",
        r"\bli[- ]?rads\b":                                       "LI-RADS",
        r"\bpi[- ]?rads\b":                                       "PI-RADS",
        r"\bbi[- ]?rads\b":                                       "BI-RADS",
        r"\blung[- ]?rads\b":                                     "Lung-RADS",

        # ══ ÉVOLUTION & TEMPORALITÉ ══
        r"\bstationnair[e]\b":                                    "stationnaire",
        r"\bstabilit[eé]\b":                                      "stabilité",
        r"\bprogression\b":                                       "progression",
        r"\br[eé]gression\b":                                     "régression",
        r"\baig[uü][eë]\b":                                       "aiguë",
        r"\bsub[- ]?aigu[eë]\b":                                  "subaiguë",
        r"\bchroni[ck]u[e]\b":                                    "chronique",
        r"\binv[oó]lution\b":                                     "involution",

        # ══ SÉVÉRITÉ & QUANTIFICATION ══
        r"\bmod[eé]r[eé]\b":                                      "modéré",
        r"\bs[eé]v[eè]r[e]\b":                                    "sévère",
        r"\bdiscr[eè]t[e]\b":                                     "discrète",
        r"\bminime\b":                                            "minime",
        r"\babondant\b":                                          "abondant",
        r"\bmassif\b":                                            "massif",
        r"\bprédominant\b":                                       "prédominant",

        # ══ THORAX & ABDOMEN (DÉTAILS PRÉCIS) ══
        r"\bp[eé]ritoin[e]\b":                                    "péritoine",
        r"\bm[eé]sos\b":                                          "mésos",
        r"\bdiaphragm[e]\b":                                      "diaphragme",
        r"\bpiliers\s+du\s+diaphragme\b":                         "piliers du diaphragme",
        r"\bhil[e]s\s+pulmonair[e]s\b":                           "hiles pulmonaires",
        r"\bpl[eè]vr[e]\b":                                       "plèvre",
        r"\bcul[- ]de[- ]sac\s+pleural\b":                        "cul-de-sac pleural",

        # ══ CARDIOVASCULAIRE PRÉCIS ══
        r"\boricull[e]\b":                                        "oreillette",
        r"\bventricul[e]\s+droit\b":                              "ventricule droit",
        r"\bventricul[e]\s+gauche\b":                             "ventricule gauche",
        r"\bvalv[e]s?\b":                                         "valves",
        r"\bp[eé]ricard[e]\b":                                    "péricarde",
        r"\bcross[e]\s+aorti[ck]u[e]\b":                          "crosse aortique",
        r"\bath[eé]romatos[e]\b":                                 "athéromatose",
        r"\bcalcification[s]?\s+vasculair[e]s\b":                 "calcifications vasculaires",

        # ══ SEGMENTATION HÉPATIQUE (COUINAUD) ══
        r"\bsegment\s+I\b":                                       "segment I",
        r"\bsegment\s+II\b":                                      "segment II",
        r"\bsegment\s+III\b":                                     "segment III",
        r"\bsegment\s+IV[ab]?\b":                                 "segment IV",
        r"\bsegment\s+V\b":                                       "segment V",
        r"\bsegment\s+VI\b":                                      "segment VI",
        r"\bsegment\s+VII\b":                                     "segment VII",
        r"\bsegment\s+VIII\b":                                    "segment VIII",
        r"\bdôme\s+h[eé]patique\b":                                "dôme hépatique",

        # ══ O.R.L. & STOMATOLOGIE ══
        r"\bcavum\b":                                             "cavum",
        r"\bglandes?\s+parotide[s]?\b":                           "glande parotide",
        r"\bglandes?\s+sous[- ]maxillaire[s]?\b":                 "glande sous-maxillaire",
        r"\bglandes?\s+sub[- ]linguale[s]?\b":                    "glande sublinguale",
        r"\bsinus\s+maxillaire\b":                                "sinus maxillaire",
        r"\bsinus\s+frontal\b":                                   "sinus frontal",
        r"\bsinus\s+sph[eé]no[iï]dal\b":                          "sinus sphénoïdal",
        r"\bcellules?\s+[eé]thmo[iï]dale[s]?\b":                  "cellules ethmoïdales",
        r"\bconduit\s+auditif\s+externe\b":                       "CAE",
        r"\bconduit\s+auditif\s+interne\b":                       "CAI",
        r"\bmasto[iï]de\b":                                       "mastoïde",

        # ══ OPHTALMOLOGIE ══
        r"\bglobes?\s+oculaire[s]?\b":                            "globe oculaire",
        r"\bnert\s+optique\b":                                    "nerf optique",
        r"\borbite[s]?\b":                                        "orbite",
        r"\bcristallin\b":                                        "cristallin",

        # ══ THORAX & CŒUR (DÉTAILLÉ) ══
        r"\baorte\s+ascendante\b":                                "aorte ascendante",
        r"\baorte\s+descendante\b":                               "aorte descendante",
        r"\bart[eè]re\s+pulmonaire\b":                            "artère pulmonaire",
        r"\bveines?\s+pulmonaire[s]?\b":                          "veine pulmonaire",
        r"\bventricule\s+gauche\b":                               "VG",
        r"\bventricule\s+droit\b":                                "VD",
        r"\boreillette\s+gauche\b":                               "OG",
        r"\boreillette\s+droit[e]?\b":                            "OD",
        r"\bseptum\s+inter[- ]ventriculaire\b":                   "septum interventriculaire",
        r"\barc\s+aortique\b":                                    "arc aortique",
        r"\bp[eé]ricarde\b":                                      "péricarde",

        # ══ ABDOMEN : DIGESTIF & PAROI ══
        r"\boesophage\b":                                         "œsophage",
        r"\bjonction\s+oeso[- ]gastrique\b":                      "jonction œsogastrique",
        r"\bcardia\b":                                            "cardia",
        r"\bpylore\b":                                            "pylore",
        r"\bdénum\b":                                             "duodénum",
        r"\bd[eé]nu[oó]d[eé]num\b":                               "duodénum",
        r"\bj[eé]junum\b":                                        "jéjunum",
        r"\bil[eé]on\b":                                          "iléon",
        r"\bc[ao]ecum\b":                                         "cæcum",
        r"\bcolon\s+ascendant\b":                                 "côlon ascendant",
        r"\bcolon\s+transverse\b":                                "côlon transverse",
        r"\bcolon\s+descendant\b":                                "côlon descendant",
        r"\bsigmo[iï]de\b":                                       "sigmoïde",
        r"\brectum\b":                                            "rectum",
        r"\bcharnière\s+recto[- ]sigmo[iï]dienne\b":              "charnière recto-sigmoïdienne",

        # ══ URO-GÉNITAL PRÉCIS ══
        r"\bur[eè]t[eè]re[s]?\b":                                 "uretère",
        r"\bur[eè]tre\b":                                         "urètre",
        r"\btrigone\s+v[eé]sical\b":                              "trigone vésical",
        r"\bjat\s+urinaire\b":                                    "jet urinaire",
        r"\bv[eé]sicules?\s+s[eé]minale[s]?\b":                   "vésicule séminale",
        r"\bcorps\s+caverneux\b":                                 "corps caverneux",
        r"\btrompes?\s+de\s+fallope\b":                           "trompe de Fallope",
        r"\bcul[- ]de[- ]sac\s+de\s+douglas\b":                    "cul-de-sac de Douglas",

        # ══ ÉPAULE & MEMBRE SUPÉRIEUR ══
        r"\barticulation\s+acromio[- ]claviculaire\b":            "articulation acromio-claviculaire",
        r"\barticulation\s+gl[eé]no[- ]hum[eé]rale\b":            "articulation gléno-humérale",
        r"\bboursite\b":                                          "boursite",
        r"\blabrum\b":                                            "labrum",
        r"\bbourrelet\b":                                         "bourrelet",
        r"\btendon\s+supraspinatus\b":                            "tendon supraspinatus",
        r"\bsou[- ]scapulaire\b":                                 "sous-scapulaire",
        r"\binfra[- ]épineux\b":                                  "infra-épineux",
        r"\bcoiffe\s+des\s+rotateurs\b":                          "coiffe des rotateurs",
        r"\b[eé]picondylite\b":                                   "épicondylite",
        r"\bcanal\s+carpien\b":                                   "canal carpien",

        # ══ HANCHE & MEMBRE INFÉRIEUR ══
        r"\barticulation\s+coxo[- ]f[eé]morale\b":                "articulation coxo-fémorale",
        r"\bcol\s+du\s+f[eé]mur\b":                               "col du fémur",
        r"\btrochanter\b":                                        "trochanter",
        r"\bm[eé]nisque\s+interne\b":                             "ménisque interne",
        r"\bm[eé]nisque\s+externe\b":                             "ménisque externe",
        r"\bligament\s+crois[eé]\s+ant[eé]rieur\b":               "LCA",
        r"\bligament\s+crois[eé]\s+post[eé]rieur\b":              "LCP",
        r"\bpatella\b":                                           "patella",
        r"\brotule\b":                                            "rotule",
        r"\btendon\s+achill[eé]en\b":                             "tendon achilléen",
        r"\bapon[eé]vrosite\s+plantaire\b":                       "aponévrosite plantaire",

        # ══ DESCRIPTEURS DE LÉSIONS (SOLIDE/LIQUIDE) ══
        r"\bmass[e]\s+spicul[eé]e\b":                             "masse spiculée",
        r"\bcontours\s+polycycli[ck]ues\b":                       "contours polycycliques",
        r"\bcloisons?\s+intra[- ]kysti[ck]ue\b":                  "cloison intra-kystique",
        r"\bbourgeon\s+charnu\b":                                 "bourgeon charnu",
        r"\bn[eé]crose\s+centrale\b":                             "nécrose centrale",
        r"\bliqu[eé]faction\b":                                   "liquéfaction",
        r"\bcontenu\s+h[eé]matique\b":                            "contenu hématique",
        r"\bniveau\s+liquide[- ]liquide\b":                       "niveau liquide-liquide",

        # ══ TERMES RADIOLOGIQUES SPÉCIFIQUES ══
        r"\bdensit[eé]\s+hydri[ck]ue\b":                          "densité hydrique",
        r"\bdensit[eé]\s+graisseuse\b":                           "densité graisseuse",
        r"\bdensit[eé]\s+calci[ck]ue\b":                          "densité calcique",
        r"\bhyper[- ]densit[eé]\b":                               "hyperdensité",
        r"\bhypo[- ]densit[eé]\b":                                "hypodensité",
        r"\bisodensit[eé]\b":                                     "isodensité",
        r"\bprise\s+de\s+contraste\s+p[eé]riph[eé]ri[ck]ue\b":    "prise de contraste périphérique",
        r"\beffet\s+de\s+mass[e]\b":                              "effet de masse",
        r"\bd[eé]viation\b":                                      "déviation",

        # ══ FAUTES DE DICTÉE VOCALE / PHONÉTIQUE ══
        r"\bsessions?\s+cortico\b":                               "sections cortico", # Erreur classique dictée
        r"\bail\s+de\s+la\s+rate\b":                              "hile de la rate",
        r"\bail\s+du\s+rein\b":                                   "hile du rein",
        r"\barete\b":                                             "rate",
        r"\bfoit\b":                                              "foie",
        r"\bvoie\s+biliaire\s+principal\b":                       "voie biliaire principale",
        r"\bveine\s+cave\s+inf\b":                                "VCI",
        r"\bveine\s+cave\s+sup\b":                                "VCS",
        r"\bcaillou\b":                                           "calcul", # Parfois dicté ainsi

        # ══ QUANTIFICATION ET MESURES ══
        r"\bcentim[eè]tre[s]?\b":                                 "cm",
        r"\bmillim[eè]tre[s]?\b":                                 "mm",
        r"\bmillilitre[s]?\b":                                    "ml",
        r"\bcc\b":                                                "cm³",
        r"\bgrand\s+diam[eè]tre\b":                               "grand diamètre",
        r"\bépaisseur\s+maximale\b":                              "épaisseur maximale",

        # ══ ÉTAT DU PATIENT / EXAMEN ══
        r"\bpatient\s+à\s+jeun\b":                                "patient à jeun",
        r"\bvessie\s+en\s+semi[- ]repletion\b":                   "vessie en semi-réplétion",
        r"\bvessie\s+vide\b":                                     "vessie vide",
        r"\bbonne\s+échog[eé]nicit[eé]\b":                        "bonne échogénicité",
        r"\bexamen\s+laborieux\b":                                "examen laborieux",
        r"\bmauvaise\s+échog[eé]nicit[eé]\b":                      "mauvaise échogénicité",
        r"\bob[eé]sit[eé]\b":                                     "obésité",
        r"\bmétéorisme\s+abdominal\b":                            "météorisme abdominal",

        # ══ TERMINOLOGIE VASCULAIRE ══
        r"\btrombus\b":                                           "thrombus",
        r"\bembolie\s+pulmonaire\b":                              "embolie pulmonaire",
        r"\bperm[eé]able\b":                                      "perméable",
        r"\bnon\s+occlusi[fv]\b":                                 "non occlusif",
        r"\bflux\s+laminair[e]\b":                                "flux laminaire",
        r"\bturbulences\b":                                       "turbulences",
        r"\baliasing\b":                                          "aliasing",
        r"\bindex\s+de\s+r[eé]sistance\b":                        "index de résistance",
        r"\bIR\b":                                                "IR",

        # ══ DIVERS & NETTOYAGE FINAL ══
        r"\bsans\s+anomalie\s+morphologique\b":                  "sans anomalie morphologique",
        r"\bau\s+total\b":                                        "AU TOTAL",
        r"\ben\s+r[eé]sum[eé]\b":                                 "EN RÉSUMÉ",
        r"\bdans\s+les\s+limites\s+de\s+la\s+normal[e]\b":        "dans les limites de la normale",

        # ══ RACHIS DÉTAILLÉ (SEGMENTS) ══
        r"\brachis\s+cervical\b":                                 "rachis cervical",
        r"\brachis\s+dorsal\b":                                   "rachis dorsal",
        r"\brachis\s+lombaire\b":                                 "rachis lombaire",
        r"\bcharnière\s+cervico[- ]thoraci[ck]ue\b":              "charnière cervico-thoracique",
        r"\bcharnière\s+thoraco[- ]lombaire\b":                  "charnière thoraco-lombaire",
        r"\bcharnière\s+lombo[- ]sacr[eé]e\b":                    "charnière lombo-sacrée",
        r"\bdisque\s+inter[- ]vert[eé]bral\b":                    "disque intervertébral",
        r"\bhernie\s+discale\b":                                  "hernie discale",
        r"\bconflit\s+disco[- ]radicualire\b":                    "conflit disco-radiculaire",
        r"\bforamen\s+de\s+conjugaison\b":                        "foramen de conjugaison",

        # ══ OSTÉO-ARTICULAIRE : MAIN & POIGNET ══
        r"\bscapho[iï]de\b":                                      "scaphoïde",
        r"\btrap[eè]ze\b":                                        "trapèze",
        r"\bsemi[- ]lunaire\b":                                   "lunatum",
        r"\bpyramidal\b":                                         "triquetrum",
        r"\bm[eé]tacarpe\b":                                      "métacarpe",
        r"\bphalange\b":                                          "phalange",
        r"\barticulation\s+radio[- ]carpienne\b":                "articulation radio-carpienne",

        # ══ OSTÉO-ARTICULAIRE : PIED & CHEVILLE ══
        r"\bcalcan[eé]um\b":                                      "calcanéus",
        r"\btalus\b":                                             "talus",
        r"\bastragale\b":                                         "astragale",
        r"\bm[eé]tatarse\b":                                      "métatarse",
        r"\bmall[eé]ole\s+externe\b":                             "malléole latérale",
        r"\bmall[eé]ole\s+interne\b":                             "malléole médiale",
        r"\btendon\s+d.achille\b":                                "tendon calcanéen",

        # ══ NEUROLOGIE & CRÂNE (AVANCÉ) ══
        r"\bpolygone\s+de\s+willis\b":                            "polygone de Willis",
        r"\bselle\s+turcique\b":                                  "selle turcique",
        r"\bcorps\s+calleux\b":                                   "corps calleux",
        r"\bnoyaux\s+gris\s+centraux\b":                          "noyaux gris centraux",
        r"\bthalamus\b":                                          "thalamus",
        r"\bhypothalamus\b":                                      "hypothalamus",
        r"\bcapsule\s+interne\b":                                 "capsule interne",
        r"\bespace\s+sous[- ]arachno[iï]dien\b":                  "espace sous-arachnoïdien",
        r"\bleuco[-]?[eé]ra[iï]ose\b":                            "leucoaraïose",

        # ══ VASCULAIRE SPÉCIFIQUE ══
        r"\bart[eè]re\s+m[eé]sent[eé]ri[ck]ue\s+sup[eé]rieure\b":  "AMS",
        r"\bart[eè]re\s+m[eé]sent[eé]ri[ck]ue\s+inf[eé]rieure\b":   "AMI",
        r"\bart[eè]re\s+iliaque\s+commune\b":                     "artère iliaque commune",
        r"\bart[eè]re\s+f[eé]morale\b":                           "artère fémorale",
        r"\bart[eè]re\s+poplit[eé]e\b":                           "artère poplitée",
        r"\bcrosse\s+de\s+la\s+saph[eè]ne\b":                      "crosse de la saphène",

        # ══ SIGNES & SEMIOLOGIE ÉCHOGRAPHIQUE ══
        r"\brenforcement\s+post[eé]rieur\b":                      "renforcement postérieur",
        r"\bcône\s+d.ombre\b":                                    "cône d'ombre acoustique",
        r"\bqueue\s+de\s+com[eè]te\b":                            "artéfact en queue de comète",
        r"\bsigne\s+du\s+flot\b":                                 "signe du flot",

        # ══ MÉDECINE NUCLÉAIRE / PET-SCAN ══
        r"\bhypo[- ]fixation\b":                                  "hypofixation",
        r"\bhyper[- ]fixation\b":                                 "hyperfixation",
        r"\bSUV\s+max\b":                                         "SUV max",
        r"\btraceur\b":                                           "traceur",
        r"\bactivité\s+m[eé]tabolique\b":                         "activité métabolique",

        # ══ ERREURS DE DICTÉE PHONÉTIQUE (LES "PIÈGES") ══
        r"\bsait\s+L5\b":                                         "C'est L5",
        r"\bsept\s+L5\b":                                         "C'est L5",
        r"\bfoie\s+gauche\b":                                     "lobe gauche", # Si dicté "foie gauche"
        r"\bfoie\s+droit\b":                                      "lobe droit",
        r"\bvoie\s+biliaire\s+principale\s+est\s+fine\b":         "VBP fine",
        r"\bpas\s+de\s+masse\s+décelable\b":                      "pas de masse décelable",
        r"\bil\s+n.y\s+a\s+pas\b":                                "absence de",

        # ══ PATHOLOGIES DIVERSES ══
        r"\bpneumoperitoine\b":                                   "pneumopéritoine",
        r"\bocclusion\b":                                         "occlusion",
        r"\bsyndrome\s+occlusif\b":                               "syndrome occlusif",
        r"\bdiverticulite\b":                                     "diverticulite",
        r"\bsigmo[iï]dite\b":                                     "sigmoïdite",
        r"\bischi[eé]mie\b":                                      "ischémie",
        r"\binfarctus\b":                                         "infarctus",

        # ══ ÉVOLUTION & COMPARAISON ══
        r"\ben\s+faveur\s+d.une\s+stabilit[eé]\b":                "en faveur d'une stabilité",
        r"\baspect\s+superposable\b":                             "aspect superposable",
        r"\bmodifications\s+notables\b":                          "modifications notables",
        r"\bcompar[eé]\s+à\s+l.examen\s+du\b":                    "comparé à l'examen du",

        # ══ FORMULATIONS DE CONCLUSION ══
        r"\bexamen\s+normal\b":                                   "examen dans les limites de la normale",
        r"\bau\s+total\b":                                        "AU TOTAL",
        r"\bce\s+qui\s+conclut\b":                                "CONCLUSION",
        r"\bpas\s+d.anomalie\s+significative\b":                  "pas d'anomalie significative",

        # ══ NEUROLOGIE & CRÂNE (COMPLÉMENTS) ══
        r"\blangue\b":                                            "langue",
        r"\bglande\s+sous[- ]mandibulaire\b":                     "glande sous-mandibulaire",
        r"\bcavum\b":                                             "cavum",
        r"\bvallecule\b":                                         "vallécule",
        r"\bépiglotte\b":                                         "épiglotte",
        r"\bcordes\s+vocales\b":                                  "cordes vocales",
        r"\blarynx\b":                                            "larynx",
        r"\bpharynx\b":                                           "pharynx",
        r"\bglande\s+parotide\b":                                 "glande parotide",
        r"\bconduit\s+auditif\b":                                 "conduit auditif",
        r"\bcellules\s+mastoidiennes\b":                          "cellules mastoïdiennes",
        r"\bcaisse\s+du\s+tympan\b":                              "caisse du tympan",
        r"\btrompe\s+d.eustache\b":                               "trompe d'Eustache",
        r"\bfosse\s+nasale\b":                                    "fosse nasale",
        r"\bcornet\s+nasal\b":                                    "cornet nasal",

        # ══ SYSTÈME CARDIOVASCULAIRE DÉTAILLÉ ══
        r"\bartère\s+carotide\s+interne\b":                       "carotide interne",
        r"\bartère\s+carotide\s+externe\b":                       "carotide externe",
        r"\bartère\s+vertébrale\b":                               "artère vertébrale",
        r"\bartère\s+sous[- ]clavière\b":                         "artère sous-clavière",
        r"\bartère\s+axillaire\b":                                "artère axillaire",
        r"\bartère\s+brachiale\b":                                "artère brachiale",
        r"\bartère\s+radiale\b":                                  "artère radiale",
        r"\bartère\s+ulnaire\b":                                  "artère ulnaire",
        r"\barc\s+palmaire\b":                                    "arc palmar",
        r"\bartère\s+iliaque\s+externe\b":                        "artère iliaque externe",
        r"\bartère\s+iliaque\s+interne\b":                        "artère iliaque interne",
        r"\bartère\s+fémorale\s+commune\b":                       "artère fémorale commune",
        r"\bartère\s+fémorale\s+profonde\b":                      "artère fémorale profonde",
        r"\bartère\s+tibiale\s+antérieure\b":                     "artère tibiale antérieure",
        r"\bartère\s+tibiale\s+postérieure\b":                    "artère tibiale postérieure",
        r"\bartère\s+pédieuse\b":                                 "artère pédieuse",
        r"\bveine\s+jugulaire\b":                                 "veine jugulaire",
        r"\bveine\s+sous[- ]clavière\b":                          "veine sous-clavière",
        r"\bveine\s+axillaire\b":                                 "veine axillaire",
        r"\bveine\s+fémorale\b":                                  "veine fémorale",
        r"\bveine\s+poplitée\b":                                  "veine poplitée",
        r"\bgrande\s+saphène\b":                                  "grande saphène",
        r"\bpetite\s+saphène\b":                                  "petite saphène",

        # ══ OSTÉO-ARTICULAIRE : MEMBRE SUPÉRIEUR ══
        r"\bclavicule\b":                                         "clavicule",
        r"\bomoplate\b":                                          "scapula",
        r"\bacromion\b":                                          "acromion",
        r"\bhumerus\b":                                           "humérus",
        r"\bradius\b":                                            "radius",
        r"\bcubitus\b":                                           "ulna",
        r"\bcarpe\b":                                             "carpe",
        r"\bmétacarpe\b":                                         "métacarpe",
        r"\bphalange\s+proximale\b":                              "phalange proximale",
        r"\bphalange\s+moyenne\b":                                "phalange moyenne",
        r"\bphalange\s+distale\b":                                "phalange distale",

        # ══ OSTÉO-ARTICULAIRE : MEMBRE INFÉRIEUR ══
        r"\bbassin\b":                                            "bassin",
        r"\bos\s+iliaque\b":                                      "os iliaque",
        r"\bsacrum\b":                                            "sacrum",
        r"\bcoccyx\b":                                            "coccyx",
        r"\bfémur\b":                                             "fémur",
        r"\btibia\b":                                             "tibia",
        r"\bpéroné\b":                                            "fibula",
        r"\btarse\b":                                             "tarse",
        r"\bmétatarse\b":                                         "métatarse",
        r"\bastragale\b":                                         "talus",
        r"\bcalcaneum\b":                                         "calcanéus",
        r"\bos\s+naviculaire\b":                                  "os naviculaire",
        r"\bcuboid\b":                                            "cuboïde",
        r"\bcunéiforme\b":                                        "cunéiforme",

        # ══ PATHOLOGIES & SIGNES (VOLUME HAUT) ══
        r"\bdéminéralisation\s+osseuse\b":                        "déminéralisation osseuse",
        r"\bostéopénie\b":                                        "ostéopénie",
        r"\bostéoporose\b":                                       "ostéoporose",
        r"\bcal\s+vicieux\b":                                     "cal vicieux",
        r"\bpseudarthrose\b":                                     "pseudarthrose",
        r"\bostéomyélite\b":                                      "ostéomyélite",
        r"\bsynovite\b":                                          "synovite",
        r"\bchondropathie\b":                                     "chondropathie",
        r"\benthésopathie\b":                                     "enthésopathie",
        r"\btendinopathie\b":                                     "tendinopathie",
        r"\bcalcifiante\b":                                       "calcifiante",
        r"\bfissure\s+méniscale\b":                               "fissure méniscale",
        r"\bkyste\s+poplité\b":                                   "kyste poplité",
        r"\bkyste\s+de\s+baker\b":                                "kyste de Baker",

        # ══ ONCOLOGIE & CARACTÉRISATION ══
        r"\blesion\s+suspecte\b":                                 "lésion suspecte",
        r"\bprocessus\s+expansif\b":                              "processus expansif",
        r"\benvahissement\b":                                     "envahissement",
        r"\bstadification\b":                                     "stadification",
        r"\bcarcinome\b":                                         "carcinome",
        r"\bsarcome\b":                                           "sarcome",
        r"\badénocarcinome\b":                                    "adénocarcinome",
        r"\bmétastase\b":                                         "métastase",
        r"\blytique\b":                                           "lytique",
        r"\bcondensant\b":                                        "condensant",
        r"\bblastiq?ue\b":                                        "blastique",
        r"\benvahit\b":                                           "envahit",
        r"\binfiltrant\b":                                        "infiltrant",

        # ══ TERMES DE COMPTE-RENDU (VERBIAGE RADIOLOGIQUE) ══
        r"\bsignale\b":                                           "signale",
        r"\bmise\s+en\s+évidence\b":                              "mise en évidence",
        r"\bretrouve\b":                                          "retrouve",
        r"\bobjective\b":                                         "objective",
        r"\bvisualise\b":                                         "visualise",
        r"\bconfirme\b":                                          "confirme",
        r"\binfirmer\b":                                          "infirmer",
        r"\bsuggère\b":                                           "suggère",
        r"\bévoque\b":                                            "évoque",
        r"\bélimine\b":                                           "élimine",
        r"\bdouteux\b":                                           "douteux",
        r"\batypique\b":                                          "atypique",
        r"\bpolymorphe\b":                                        "polymorphe",

        # ══ UNITÉS & MESURES (PRECISION) ══
        r"\bmillimètre\b":                                        "mm",
        r"\bcentimètre\b":                                        "cm",
        r"\bmètre\b":                                             "m",
        r"\bgramme\b":                                            "g",
        r"\bkilogramme\b":                                        "kg",
        r"\bseconde\b":                                           "s",
        r"\bminute\b":                                            "min",
        r"\bheure\b":                                             "h",
        r"\bdégré\b":                                             "°",
        r"\bpourcent\b":                                          "%",

        # ══ NEURO-ANATOMIE FINALE & CRÂNE ══
        r"\bpolygone\s+de\s+willis\b":                            "polygone de Willis",
        r"\bsillon\s+lat[eé]ral\b":                               "sillon latéral",
        r"\bscissure\s+de\s+sylvius\b":                           "scissure de Sylvius",
        r"\bscissure\s+de\s+rolando\b":                          "scissure de Rolando",
        r"\bgyrus\b":                                             "gyrus",
        r"\binsula\b":                                            "insula",
        r"\bnoyau\s+caud[eé]\b":                                  "noyau caudé",
        r"\bputamen\b":                                           "putamen",
        r"\bpallidum\b":                                          "pallidum",
        r"\bthalamus\b":                                          "thalamus",
        r"\bhypothalamus\b":                                      "hypothalamus",
        r"\bad[eé]nohypophys[e]\b":                               "adénohypophyse",
        r"\bneurohypophys[e]\b":                                  "neurohypophyse",
        r"\btige\s+pituitaire\b":                                 "tige pituitaire",
        r"\bchiasma\s+optique\b":                                 "chiasma optique",
        r"\baqueduc\s+de\s+sylvius\b":                            "aqueduc de Sylvius",
        r"\btrou\s+de\s+monro\b":                                 "trou de Monro",
        r"\bquatri[eè]me\s+ventricule\b":                         "V4",
        r"\btroisi[eè]me\s+ventricule\b":                         "V3",
        r"\bespac[e]\s+p[eé]rivasculair[e]\b":                    "espace périvasculaire",
        r"\bpontaill[e]\b":                                       "pontique",

        # ══ O.R.L. & MAXILLO-FACIAL ══
        r"\bsinus\s+ethmo[iï]d[eé]al\b":                          "sinus ethmoïdal",
        r"\bsinus\s+sph[eé]no[iï]dal\b":                          "sinus sphénoïdal",
        r"\bcornet\s+inf[eé]rieur\b":                             "cornet inférieur",
        r"\bcornet\s+moyen\b":                                    "cornet moyen",
        r"\bcornet\s+sup[eé]rieur\b":                             "cornet supérieur",
        r"\bseptum\s+nasal\b":                                    "septum nasal",
        r"\bcloisons\s+nasale\b":                                 "cloison nasale",
        r"\bglandes\s+salivaires\b":                              "glandes salivaires",
        r"\bcanal\s+de\s+st[eé]non\b":                            "canal de Sténon",
        r"\bcanal\s+de\s+wharton\b":                              "canal de Wharton",
        r"\barticulation\s+temporo[- ]mandibulaire\b":            "ATM",
        r"\bcondyle\b":                                           "condyle",

        # ══ CARDIOVASCULAIRE (GRAND FORMAT) ══
        r"\baorte\s+horizontale\b":                               "aorte horizontale",
        r"\bart[eè]re\s+sous[- ]clavi[eè]re\b":                   "artère sous-clavière",
        r"\bart[eè]re\s+axillair[e]\b":                           "artère axillaire",
        r"\bart[eè]re\s+hum[eé]rale\b":                           "artère humérale",
        r"\bart[eè]re\s+radial[e]\b":                             "artère radiale",
        r"\bart[eè]re\s+cubital[e]\b":                            "artère ulnaire",
        r"\bart[eè]re\s+hypogastri[ck]u[e]\b":                    "artère iliaque interne",
        r"\bart[eè]re\s+f[eé]morale\s+superficielle\b":           "artère fémorale superficielle",
        r"\bart[eè]re\s+poplit[eé]e\b":                           "artère poplitée",
        r"\bart[eè]re\s+tibiale\s+ant[eé]rieur[e]\b":              "artère tibiale antérieure",
        r"\bart[eè]re\s+tibiale\s+post[eé]rieur[e]\b":             "artère tibiale postérieure",
        r"\bart[eè]re\s+p[eé]di[eé]use\b":                        "artère pédieuse",
        r"\bveine\s+jugulaire\s+intern[e]\b":                     "VJI",
        r"\bveine\s+jugulaire\s+extern[e]\b":                     "VJE",
        r"\bveine\s+axillair[e]\b":                               "veine axillaire",
        r"\bveine\s+c[eé]phali[ck]u[e]\b":                        "veine céphalique",
        r"\bveine\s+basili[ck]u[e]\b":                            "veine basilique",
        r"\bveine\s+iliaque\s+commun[e]\b":                       "veine iliaque commune",
        r"\bveine\s+f[eé]morale\s+commun[e]\b":                   "veine fémorale commune",
        r"\bveine\s+poplit[eé]e\b":                               "veine poplitée",
        r"\bgrande\s+saph[eè]n[e]\b":                             "grande saphène",
        r"\bpetit[e]\s+saph[eè]n[e]\b":                           "petite saphène",

        # ══ THORAX & PULMONAIRE ══
        r"\blobe\s+sup[eé]rieur\s+droit\b":                       "LSD",
        r"\blobe\s+moyen\b":                                      "lobe moyen",
        r"\blobe\s+inf[eé]rieur\s+droit\b":                       "LID",
        r"\blobe\s+sup[eé]rieur\s+gauche\b":                      "LSG",
        r"\blobe\s+inf[eé]rieur\s+gauche\b":                      "LIG",
        r"\blingula\b":                                           "lingula",
        r"\bsegment\s+apical\b":                                  "segment apical",
        r"\bsegment\s+basal\b":                                   "segment basal",
        r"\bart[eè]re\s+pulmonaire\s+gauche\b":                   "artère pulmonaire gauche",
        r"\bart[eè]re\s+pulmonaire\s+droite\b":                    "artère pulmonaire droite",
        r"\bveines\s+pulmonaires\b":                              "veines pulmonaires",
        r"\btrach[eé]e\b":                                        "trachée",
        r"\bbronch[e]\s+souche\b":                                "bronche souche",
        r"\bcar[eè]n[e]\b":                                       "carène",
        r"\bm[eé]diastin\s+ant[eé]rieur\b":                       "médiastin antérieur",
        r"\bm[eé]diastin\s+moyen\b":                              "médiastin moyen",
        r"\bm[eé]diastin\s+post[eé]rieur\b":                      "médiastin postérieur",

        # ══ ABDOMEN & DIGESTIF (PIÉGEAGE AVANCÉ) ══
        r"\bpilier\s+du\s+diaphragm[e]\b":                        "pilier du diaphragme",
        r"\bespac[e]\s+p[eé]ri[- ]r[eé]nal\b":                    "espace périrénal",
        r"\bfasci[a]\s+de\s+gerota\b":                            "fascia de Gerota",
        r"\bloge\s+h[eé]patique\b":                               "loge hépatique",
        r"\bloge\s+spl[eé]ni[ck]u[e]\b":                          "loge splénique",
        r"\bespac[e]\s+p[eé]riton[eé]al\b":                       "espace péritonéal",
        r"\bm[eé]so[- ]colon\b":                                  "mésocôlon",
        r"\bm[eé]sent[eè]r[e]\b":                                 "mésentère",
        r"\bgrande\s+courbure\b":                                 "grande courbure gastrique",
        r"\bpetit[e]\s+courbure\b":                               "petite courbure gastrique",
        r"\bd2\b":                                                "D2 (duodénum)",
        r"\bd3\b":                                                "D3 (duodénum)",
        r"\bangle\s+de\s+treitz\b":                               "angle de Treitz",
        r"\bvalvul[e]\s+il[eé]o[- ]c[ae]cal[e]\b":                "valvule iléo-cæcale",
        r"\bc[oô]lon\s+ascendant\b":                              "côlon ascendant",
        r"\bc[oô]lon\s+descendant\b":                             "côlon descendant",
        r"\bangle\s+h[eé]patique\b":                              "angle colique droit",
        r"\bangle\s+spl[eé]ni[ck]u[e]\b":                         "angle colique gauche",

        # ══ SYSTÈME URINAIRE FIN ══
        r"\bcalic[e]\s+sup[eé]rieur\b":                           "calice supérieur",
        r"\bcalic[e]\s+moyen\b":                                  "calice moyen",
        r"\bcalic[e]\s+inf[eé]rieur\b":                           "calice inférieur",
        r"\btige\s+caliciell[e]\b":                               "tige calicielle",
        r"\bbassinet\b":                                          "bassinet",
        r"\bpy[eé]lon\b":                                         "pyélon",
        r"\bjonction\s+py[eé]lo[- ]ur[eé]t[eé]ral[e]\b":          "jonction pyélo-urétérale",
        r"\bur[eé]t[eè]re\s+lombair[e]\b":                        "uretère lombaire",
        r"\bur[eé]t[eè]re\s+pelvien\b":                           "uretère pelvien",
        r"\bm[eé]at\s+ur[eé]t[eé]ral\b":                          "méat urétéral",

        # ══ OSTÉO-ARTICULAIRE (COMPLET) ══
        r"\bos\s+ilia[ck]u[e]\b":                                 "os iliaque",
        r"\bischi[ao]n\b":                                        "ischion",
        r"\bpubis\b":                                             "pubis",
        r"\bac[eé]tabulum\b":                                     "acétabulum",
        r"\bcotyloid[e]\b":                                       "cotyloïde",
        r"\bsymphys[e]\s+pubienn[e]\b":                           "symphyse pubienne",
        r"\barticulation\s+sacro[- ]ilia[ck]u[e]\b":              "articulation sacro-iliaque",
        r"\bgrand\s+trochanter\b":                                "grand trochanter",
        r"\bpetit\s+trochanter\b":                                "petit trochanter",
        r"\bplateau\s+tibial\b":                                  "plateau tibial",
        r"\b[eé]minenc[e]\s+inter[- ]condylienn[e]\b":            "éminence inter-condylienne",
        r"\btub[eé]rosit[eé]\s+tibial[e]\s+ant[eé]rieur[e]\b":     "TTA",
        r"\bmall[eé]ol[e]\s+intern[e]\b":                         "malléole médiale",
        r"\bmall[eé]ol[e]\s+extern[e]\b":                         "malléole latérale",

        # ══ SEMIOLOGIE & PATHOLOGIE (DENSITÉ MAX) ══
        r"\bost[eé]ophytos[e]\b":                                 "ostéophytose",
        r"\bscl[eé]ros[e]\s+sous[- ]chondral[e]\b":               "sclérose sous-chondrale",
        r"\bg[eé]od[e]\b":                                        "géode",
        r"\bpincement\s+articulaire\b":                           "pincement articulaire",
        r"\bpanus\s+synovial\b":                                  "pannus synovial",
        r"\bépanchement\s+intra[- ]articulaire\b":                "épanchement intra-articulaire",
        r"\bkyst[e]\s+arthro[- ]synovial\b":                      "kyste arthro-synovial",
        r"\bténosynovite\b":                                      "ténosynovite",
        r"\bcalcification\s+tendineus[e]\b":                      "calcification tendineuse",
        r"\bsarcop[eé]ni[e]\b":                                   "sarcopénie",
        r"\binfiltration\s+graisseus[e]\b":                       "infiltration graisseuse",

        # ══ PIÉGEAGE PHONÉTIQUE (BIAISAGE DE DICTÉE) ══
        r"\baspect\s+en\s+verre\s+d[eé]poli\b":                   "aspect en verre dépoli",
        r"\brayon\s+de\s+miel\b":                                 "rayon de miel",
        r"\blign[e]\s+septal[e]\b":                               "ligne septale",
        r"\bmicronodul[e]\b":                                     "micronodule",
        r"\bmass[e]\s+tissulair[e]\b":                            "masse tissulaire",
        r"\bprocessus\s+expansi[fv]\b":                           "processus expansif",
        r"\bl[eé]sion\s+focal[e]\b":                              "lésion focale",
        r"\bplag[e]\s+d.hypo[- ]échog[eé]nicit[eé]\b":            "plage d'hypoéchogénicité",
        r"\banomalie\s+de\s+signal\b":                            "anomalie de signal",
        r"\brehaussement\s+nodulair[e]\b":                        "rehaussement nodulaire",
        r"\bprise\s+de\s+contrast[e]\s+annulair[e]\b":            "prise de contraste annulaire",

        # ══ CONCLUSIONS & FORMULAIRES ══
        r"\babsence\s+d.anomalie\b":                              "absence d'anomalie",
        r"\bau\s+total\b":                                        "AU TOTAL",
        r"\bconfrontation\s+clinico[- ]biologi[ck]u[e]\b":        "confrontation clinico-biologique",
        r"\bà\s+contrôler\s+dans\b":                              "à contrôler dans",
        r"\bsous\s+r[eé]serv[e]\b":                               "sous réserve",

        r"Biaisage\b":                                            "Piégeage",

        # ══ THORAX / PETITES VOIES AÉRIENNES ══
        r"\bC['']?[eé]tait\s+normal[e]?\b":                      "Densité normale",
        r"\bDans\s+ces\s+t[eé]norma[l]?[e]?s?\b":               "Densité normale",
        r"\bdensit[eé]\s+norma[l]?[e]?\b":                       "densité normale",
        r"\bpetites?\s+voies?\s+a[eé]rienne?s?\b":               "petites voies aériennes",
        r"\bpetites?\s+voies?\s+a[eé]r\b":                       "petites voies aériennes",
        r"\bvoies?\s+a[eé]rienne?s?\b":                          "voies aériennes",
        r"\bpi[eé][cg]h?[aâ]g[e]\b":                            "piégeage",
        r"\bpi[eé]t?[aâ]ch[e]?\b":                              "piégeage",
        r"\bpi[eé]geage\b":                                      "piégeage",
        r"\bp[eé]tach[e]?\b":                                    "piégeage",
        r"\bp[eé]tachage\b":                                     "piégeage",
        r"\bvirgule\s+\d+\s+anomalie\b":                         "sans anomalie",
        r"\bsans\s+anomalie\s+des\s+petites\s+voies\s+a[eé]rienne?s?\b": "sans anomalie des petites voies aériennes",
        r"\bsigne\s+de\s+pi[eé]g[e]?age\b":                     "signe de piégeage",
        r"\bsignaux?\s+de\s+pi[eé]g\b":                         "signe de piégeage",

        # ══ PHONÉTIQUE WHISPER : VOIES BILIAIRES AVANCÉ ══
        r"\bcholélithiase\s+vésiculaire\b":                      "cholélithiase vésiculaire",
        r"\bcholédoco[- ]?lithiase\b":                           "cholédocolithiase",
        r"\bmicrocalcul[s]?\s+vésiculaires?\b":                  "microcalculs vésiculaires",
        r"\bboue\s+biliaire\b":                                  "boue biliaire",
        r"\bsludge\s+biliaire\b":                                "sludge biliaire",
        r"\bhydrocholécyste\b":                                  "hydrocholécyste",
        r"\bvésicule\s+alithiasique\b":                          "vésicule alithiasique",
        r"\bcanal\s+hépatique\s+commun\b":                       "canal hépatique commun",
        r"\bcanal\s+cystique\b":                                 "canal cystique",
        r"\bcholestase\b":                                       "cholestase",
        r"\bdilatation\s+des\s+voies\s+biliaires\b":             "dilatation des voies biliaires",
        r"\bpneumobili[e]\b":                                    "pneumobilie",
        r"\bvoie\s+biliaire\s+non\s+dilat[eé]e\b":              "voie biliaire non dilatée",
        r"\bcholécystite\s+aigu[eë]\b":                          "cholécystite aiguë",
        r"\bcholécystite\s+chronique\b":                         "cholécystite chronique",
        r"\bcholécystite\s+alithiasique\b":                      "cholécystite alithiasique",
        r"\bmurphy\s+[eé]chographique\b":                        "signe de Murphy échographique",
        r"\bparoi\s+vésiculaire\s+[eé]paissie\b":               "paroi vésiculaire épaissie",

        # ══ PHONÉTIQUE WHISPER : FOIE AVANCÉ ══
        r"\bstéatos[e]?\s+hépatique\b":                          "stéatose hépatique",
        r"\bstéatos[e]?\s+macro[- ]vésiculaire\b":               "stéatose macrovésiculaire",
        r"\bstéatos[e]?\s+micro[- ]vésiculaire\b":               "stéatose microvésiculaire",
        r"\bhépato[- ]?splénomégalie\b":                         "hépato-splénomégalie",
        r"\bhypertension\s+portale\b":                           "hypertension portale",
        r"\bascite\s+réfractaire\b":                             "ascite réfractaire",
        r"\bcarcinome\s+hépatocellulaire\b":                     "carcinome hépatocellulaire",
        r"\bchol[ae]ngiocarcinome\b":                            "cholangiocarcinome",
        r"\bhyperplasie\s+nodulaire\s+focale\b":                 "hyperplasie nodulaire focale",
        r"\bHNF\b":                                              "HNF",
        r"\badenome\s+hépatique\b":                              "adénome hépatique",
        r"\bfibros[e]?\s+hépatique\b":                           "fibrose hépatique",
        r"\bélastométrie\s+hépatique\b":                         "élastométrie hépatique",
        r"\bFibroScan\b":                                        "FibroScan",
        r"\bcapsule\s+de\s+glisson\b":                           "capsule de Glisson",
        r"\btriade\s+portale\b":                                  "triade portale",
        r"\bdôme\s+hépatique\b":                                 "dôme hépatique",
        r"\bsecteur\s+hépatique\b":                              "secteur hépatique",
        r"\baxe\s+porto[- ]mésentérique\b":                      "axe porto-mésentérique",
        r"\bthrombos[e]?\s+portale\b":                           "thrombose portale",
        r"\bcavernome\s+portal\b":                               "cavernome portal",
        r"\bré[- ]?section\s+hépatique\b":                       "résection hépatique",
        r"\bhépatectomie\b":                                     "hépatectomie",

        # ══ PHONÉTIQUE WHISPER : PANCRÉAS AVANCÉ ══
        r"\bpancréatite\s+aigu[eë]\b":                           "pancréatite aiguë",
        r"\bpancréatite\s+chronique\b":                          "pancréatite chronique",
        r"\bpseudo[- ]?kyste\s+pancréatique\b":                  "pseudo-kyste pancréatique",
        r"\bwirsung\s+dilaté\b":                                  "canal de Wirsung dilaté",
        r"\bcollection\s+pancréatique\b":                         "collection pancréatique",
        r"\bnécrose\s+pancréatique\b":                            "nécrose pancréatique",
        r"\btête\s+du\s+pancréas\b":                             "tête du pancréas",
        r"\bcorps\s+du\s+pancréas\b":                            "corps du pancréas",
        r"\bqueue\s+du\s+pancréas\b":                            "queue du pancréas",
        r"\bprocessus\s+unciné\b":                               "processus unciné",
        r"\badénocarcinome\s+pancréatique\b":                    "adénocarcinome pancréatique",
        r"\btumeur\s+kystique\s+pancréatique\b":                 "tumeur kystique pancréatique",
        r"\bIPMN\b":                                             "IPMN",
        r"\btumeur\s+neuro[- ]?endocrine\b":                     "tumeur neuro-endocrine",
        r"\bTNE\b":                                              "TNE",
        r"\bpancréas\s+divisum\b":                               "pancréas divisum",
        r"\bannulaire\s+du\s+pancréas\b":                        "pancréas annulaire",
        r"\bpancréatectomie\b":                                  "pancréatectomie",
        r"\bwhipple\b":                                          "duodéno-pancréatectomie céphalique",
        r"\bDPC\b":                                              "DPC",

        # ══ PHONÉTIQUE WHISPER : REINS AVANCÉ ══
        r"\bkyste\s+rénal\s+simple\b":                           "kyste rénal simple",
        r"\bkyste\s+complexe\b":                                 "kyste complexe",
        r"\bBosniak\s*[IVi]+[abc]?\b":                           lambda m: m.group(0).upper(),
        r"\bkyst[e]?\s+parapyélique\b":                          "kyste parapyélique",
        r"\bcarcinome\s+à\s+cellules\s+claires\b":               "carcinome à cellules claires",
        r"\bcarcinome\s+rénal\b":                                "carcinome rénal",
        r"\bangiomyolipome\b":                                   "angiomyolipome",
        r"\boncocytome\b":                                       "oncocytome",
        r"\bpyélonéphrite\s+aigu[eë]\b":                         "pyélonéphrite aiguë",
        r"\bpyélonéphrite\s+chronique\b":                        "pyélonéphrite chronique",
        r"\bnéphrite\s+interstitielle\b":                        "néphrite interstitielle",
        r"\bnécrose\s+tubulaire\s+aigu[eë]\b":                   "NTA",
        r"\bloge\s+rénale\b":                                    "loge rénale",
        r"\bespace\s+péri[- ]?rénal\b":                          "espace périrénal",
        r"\bthrombose\s+de\s+la\s+veine\s+rénale\b":             "thrombose de la veine rénale",
        r"\btransplant\s+rénal\b":                               "transplant rénal",
        r"\bgreffe\s+rénale\b":                                  "greffe rénale",
        r"\bringer\s+périrénal\b":                               "épanchement périrénal",
        r"\bépanchement\s+périrénal\b":                          "épanchement périrénal",
        r"\bhématome\s+périrénal\b":                             "hématome périrénal",
        r"\bassociation\s+rénale\b":                             "asymétrie rénale",

        # ══ PHONÉTIQUE WHISPER : THORAX AVANCÉ ══
        r"\bpneumonie\s+franche\s+lobaire\s+aigu[eë]\b":         "PFLA",
        r"\bPFLA\b":                                             "PFLA",
        r"\bbronchopneumonie\b":                                 "bronchopneumonie",
        r"\bBPCO\b":                                             "BPCO",
        r"\bbronchectasie[s]?\b":                                "bronchectasie",
        r"\bdilatation\s+des\s+bronches\b":                      "DDB",
        r"\bDDB\b":                                              "DDB",
        r"\baspect\s+en\s+rayon\s+de\s+miel\b":                  "aspect en rayon de miel",
        r"\bfibrose\s+pulmonaire\b":                             "fibrose pulmonaire",
        r"\bFPI\b":                                              "FPI",
        r"\bpneumopathie\s+interstitielle\b":                    "pneumopathie interstitielle diffuse",
        r"\bPID\b":                                              "PID",
        r"\bcondensation\s+alvéolaire\b":                        "condensation alvéolaire",
        r"\bmicronodules?\s+centrolobulaires?\b":                "micronodules centrolobulaires",
        r"\bpiégeage\s+aérique\b":                               "piégeage aérique",
        r"\bair\s+trapping\b":                                   "piégeage aérique",
        r"\btrapping\b":                                         "piégeage aérique",
        r"\baspect\s+en\s+mosaïque\b":                           "aspect en mosaïque",
        r"\bhyperinflation\b":                                   "hyperinflation",
        r"\bhyperaération\b":                                    "hyperaération",
        r"\bbulles?\s+d.emphysème\b":                            "bulles d'emphysème",
        r"\bnodul[e]?\s+solide\b":                               "nodule solide",
        r"\bnodul[e]?\s+subsolide\b":                            "nodule subsolide",
        r"\bnodul[e]?\s+en\s+verre\s+dépoli\b":                  "nodule en verre dépoli",
        r"\bnodul[e]?\s+péri[- ]?scissural\b":                   "nodule périscissural",
        r"\bépanchement\s+pleural\s+de\s+faible\s+abondance\b":  "épanchement pleural de faible abondance",
        r"\bépanchement\s+pleural\s+de\s+grande\s+abondance\b":  "épanchement pleural de grande abondance",
        r"\bpleurésie\b":                                        "pleurésie",
        r"\bpyothorax\b":                                        "pyothorax",
        r"\bhémothorax\b":                                       "hémothorax",
        r"\bchylothorax\b":                                      "chylothorax",
        r"\bépanchement\s+bilatéral\b":                          "épanchement bilatéral",
        r"\bsigne\s+du\s+halo\b":                                "signe du halo",
        r"\bcavitation\b":                                       "cavitation",
        r"\bimage\s+cavitaire\b":                                "image cavitaire",
        r"\btranssudat\b":                                       "transsudat",
        r"\bexsudat\b":                                          "exsudat",
        r"\bplèvre\s+viscérale\b":                               "plèvre viscérale",
        r"\bplèvre\s+pariétale\b":                               "plèvre pariétale",
        r"\bépaississement\s+pleural\b":                         "épaississement pleural",
        r"\bsinus\s+costo[- ]?phrénique\b":                      "sinus costo-phrénique",
        r"\bmésothéliome\b":                                     "mésothéliome",
        r"\blymphangite\s+carcinomateuse\b":                     "lymphangite carcinomateuse",
        r"\binfiltrat\s+péri[- ]?bronchique\b":                  "infiltrat péri-bronchique",
        r"\binfiltrat\s+interstitiel\b":                         "infiltrat interstitiel",
        r"\bbronche\s+souche\s+droite\b":                        "bronche souche droite",
        r"\bbronche\s+souche\s+gauche\b":                        "bronche souche gauche",
        r"\bcarène\s+élargie\b":                                 "carène élargie",
        r"\bopacité\s+en\s+verre\s+dépoli\b":                    "opacité en verre dépoli",
        r"\bopacité\s+réticulaire\b":                            "opacité réticulaire",
        r"\bopacité\s+réticulonodulaire\b":                      "opacité réticulonodulaire",
        r"\bépanchement\s+péricardique\b":                       "épanchement péricardique",
        r"\btamponnade\s+cardiaque\b":                           "tamponnade cardiaque",

        # ══ PHONÉTIQUE WHISPER : MÉDIASTIN ══
        r"\bmasse\s+médiastinale\b":                             "masse médiastinale",
        r"\badénopathies?\s+médiastinales?\b":                   "adénopathies médiastinales",
        r"\bgoitre\s+plongeant\b":                               "goitre plongeant",
        r"\bthymome\b":                                          "thymome",
        r"\btissu\s+thymique\b":                                 "tissu thymique",
        r"\bhernie\s+hiatale\b":                                 "hernie hiatale",

        # ══ PHONÉTIQUE WHISPER : NEUROLOGIE ══
        r"\baccident\s+vasculaire\s+cérébral\b":                 "AVC",
        r"\bAVC\s+ischémique\b":                                 "AVC ischémique",
        r"\bAVC\s+hémorragique\b":                               "AVC hémorragique",
        r"\baccident\s+ischémique\s+transitoire\b":              "AIT",
        r"\bhématome\s+sous[- ]dural\b":                         "hématome sous-dural",
        r"\bhématome\s+extradural\b":                            "hématome extradural",
        r"\bhématome\s+intra[- ]parenchymateux\b":               "hématome intra-parenchymateux",
        r"\bhémorragie\s+sous[- ]arachnoïdienne\b":              "HSA",
        r"\bHSA\b":                                              "HSA",
        r"\bhypertension\s+intracrânienne\b":                    "HTIC",
        r"\bHIC\b":                                              "HTIC",
        r"\bHNORM\b":                                            "HTIC",
        r"\bhydrocéphalie\b":                                    "hydrocéphalie",
        r"\bhidrocéphalie\b":                                    "hydrocéphalie",
        r"\bhydro\s+céphalie\b":                                 "hydrocéphalie",
        r"\bdilatation\s+ventriculaire\b":                       "dilatation ventriculaire",
        r"\bœdème\s+cérébral\b":                                 "œdème cérébral",
        r"\bœdème\s+péri[- ]?lésionnel\b":                       "œdème péri-lésionnel",
        r"\beffacement\s+des\s+sillons\b":                       "effacement des sillons",
        r"\batrophie\s+cérébrale\b":                             "atrophie cérébrale",
        r"\batrophie\s+cortico[- ]sous[- ]corticale\b":          "atrophie cortico-sous-corticale",
        r"\bplaques?\s+de\s+démyélinisation\b":                  "plaques de démyélinisation",
        r"\blésions?\s+de\s+la\s+substance\s+blanche\b":         "lésions de la substance blanche",
        r"\bsclérose\s+en\s+plaques\b":                          "SEP",
        r"\bSEP\b":                                              "SEP",
        r"\bgliome\b":                                           "gliome",
        r"\bglioblastome\b":                                     "glioblastome",
        r"\bméningiome\b":                                       "méningiome",
        r"\bnévrinome\b":                                        "schwannome",
        r"\bépendymome\b":                                       "épendymome",
        r"\bmédulloblastome\b":                                  "médulloblastome",
        r"\bmétastases?\s+cérébrales?\b":                        "métastases cérébrales",
        r"\babcès\s+cérébral\b":                                 "abcès cérébral",
        r"\bencéphalite\b":                                      "encéphalite",
        r"\bméningite\b":                                        "méningite",
        r"\binfarctus\s+cérébral\b":                             "infarctus cérébral",
        r"\bterritoire\s+sylvien\b":                             "territoire sylvien",
        r"\bartère\s+cérébrale\s+moyenne\b":                     "ACM",
        r"\bartère\s+cérébrale\s+antérieure\b":                  "ACA",
        r"\bartère\s+cérébrale\s+postérieure\b":                 "ACP",
        r"\bdissection\s+carotidienne\b":                        "dissection carotidienne",
        r"\banévrisme\s+intracrânien\b":                         "anévrisme intracrânien",
        r"\bmalformation\s+artério[- ]?veineuse\b":              "MAV",
        r"\bMAV\b":                                              "MAV",
        r"\bcavernome\b":                                        "cavernome",
        r"\bhypersignal\s+T2\b":                                 "hypersignal T2",
        r"\bhyposignal\s+T1\b":                                  "hyposignal T1",
        r"\bhypersignal\s+FLAIR\b":                              "hypersignal FLAIR",
        r"\brestriction\s+en\s+diffusion\b":                     "restriction en diffusion",
        r"\bADC\s+abaissé\b":                                    "ADC abaissé",
        r"\bcortex\s+rubanné\b":                                  "cortex rubanné",
        r"\bsigne\s+du\s+ruban\b":                               "signe du ruban cortical",
        r"\bleuco[- ]?araïose\b":                                 "leucoaraïose",
        r"\bleuco[- ]?aïose\b":                                   "leucoaraïose",

        # ══ PHONÉTIQUE WHISPER : RACHIS AVANCÉ ══
        r"\bhernie\s+discale\s+postéro[- ]latérale\b":           "hernie discale postéro-latérale",
        r"\bhernie\s+discale\s+médiane\b":                       "hernie discale médiane",
        r"\bprotrusion\s+discale\b":                             "protrusion discale",
        r"\bextrusion\s+discale\b":                              "extrusion discale",
        r"\bséquestre\s+discal\b":                               "séquestre discal",
        r"\bconflits?\s+foraminaux?\b":                          "conflit foraminal",
        r"\bsténose\s+canalaire\b":                              "sténose canalaire",
        r"\bsténose\s+foraminale\b":                             "sténose foraminale",
        r"\bcanal\s+lombaire\s+étroit\b":                        "canal lombaire étroit",
        r"\bcanal\s+cervical\s+étroit\b":                        "canal cervical étroit",
        r"\bspondylarthrite\b":                                  "spondylarthrite",
        r"\bspondylodiscite\b":                                  "spondylodiscite",
        r"\bdiscite\b":                                          "discite",
        r"\bligament\s+jaune\s+épaissi\b":                       "ligament jaune épaissi",
        r"\btassement\s+vertébral\b":                            "tassement vertébral",
        r"\bfracture\s+vertébrale\b":                            "fracture vertébrale",
        r"\bfracture\s+de\s+contrainte\b":                       "fracture de contrainte",
        r"\bfracture\s+ostéoporotique\b":                        "fracture ostéoporotique",
        r"\bcyphose\b":                                          "cyphose",
        r"\bscoliose\b":                                         "scoliose",
        r"\blordose\s+effacée\b":                                "lordose effacée",
        r"\blordose\s+conservée\b":                              "lordose conservée",
        r"\bantélisthésis\b":                                    "antélisthésis",
        r"\brétrolisthésis\b":                                   "rétrolisthésis",
        r"\bfracture\s+du\s+plateau\s+supérieur\b":              "fracture du plateau supérieur",
        r"\bfracture\s+du\s+plateau\s+inférieur\b":              "fracture du plateau inférieur",
        r"\bhypertrophie\s+des\s+articulaires\b":                "hypertrophie des articulaires postérieures",
        r"\barthrose\s+des\s+articulaires\s+postérieures\b":     "arthrose des articulaires postérieures",

        # ══ PHONÉTIQUE WHISPER : OSTÉO-ARTICULAIRE AVANCÉ ══
        r"\bnécrose\s+aseptique\b":                              "nécrose aseptique",
        r"\bostéonécrose\b":                                     "ostéonécrose",
        r"\bostéonécrose\s+de\s+la\s+tête\s+fémorale\b":         "ostéonécrose de la tête fémorale",
        r"\bcoxarthrose\b":                                      "coxarthrose",
        r"\bgonarthrose\b":                                      "gonarthrose",
        r"\barthrose\s+fémoro[- ]patellaire\b":                  "arthrose fémoro-patellaire",
        r"\bluxation\b":                                         "luxation",
        r"\bsubluxation\b":                                      "subluxation",
        r"\brupture\s+du\s+LCA\b":                               "rupture du LCA",
        r"\brupture\s+du\s+LCP\b":                               "rupture du LCP",
        r"\blésion\s+méniscale\b":                               "lésion méniscale",
        r"\blésion\s+du\s+ménisque\s+interne\b":                 "lésion du ménisque interne",
        r"\blésion\s+du\s+ménisque\s+externe\b":                 "lésion du ménisque externe",
        r"\bcorne\s+antérieure\b":                               "corne antérieure",
        r"\bcorne\s+postérieure\b":                              "corne postérieure",
        r"\btendon\s+rotulien\b":                                "tendon rotulien",
        r"\btendon\s+quadricipital\b":                           "tendon quadricipital",
        r"\brupture\s+partielle\s+de\s+la\s+coiffe\b":           "rupture partielle de la coiffe",
        r"\brupture\s+totale\s+de\s+la\s+coiffe\b":              "rupture totale de la coiffe",
        r"\btendon\s+supra[- ]épineux\b":                        "tendon supra-épineux",
        r"\btendinose\b":                                        "tendinose",
        r"\btendinopathie\s+calcifiante\b":                      "tendinopathie calcifiante",
        r"\bépicondylalgie\b":                                   "épicondylalgie",
        r"\bépitrochlite\b":                                     "épitrochlite",
        r"\bsyndrôme\s+du\s+canal\s+carpien\b":                  "syndrome du canal carpien",
        r"\bsynovite\s+villo[- ]?nodulaire\b":                   "synovite villo-nodulaire",
        r"\barthropathie\s+microcristalline\b":                  "arthropathie microcristalline",
        r"\bchondrocalcinose\b":                                 "chondrocalcinose",

        # ══ PHONÉTIQUE WHISPER : ONCOLOGIE AVANCÉE ══
        r"\bmétastases?\s+hépatiques?\b":                        "métastases hépatiques",
        r"\bmétastases?\s+pulmonaires?\b":                       "métastases pulmonaires",
        r"\bmétastases?\s+osseuses?\b":                          "métastases osseuses",
        r"\bmétastases?\s+surrénaliennes?\b":                    "métastases surrénaliennes",
        r"\bcarcinose\s+péritonéale\b":                          "carcinose péritonéale",
        r"\bascite\s+carcinomateuse\b":                          "ascite carcinomateuse",
        r"\bépanchement\s+malin\b":                              "épanchement malin",
        r"\benvahissement\s+vasculaire\b":                       "envahissement vasculaire",
        r"\benvahissement\s+péri[- ]?neural\b":                  "envahissement péri-neural",
        r"\brupture\s+capsulaire\b":                             "rupture capsulaire",
        r"\bcontact\s+vasculaire\b":                             "contact vasculaire",
        r"\bplan\s+de\s+clivage\b":                              "plan de clivage",
        r"\bréponse\s+au\s+traitement\b":                        "réponse au traitement",
        r"\bréponse\s+partielle\b":                              "réponse partielle",
        r"\bréponse\s+complète\b":                               "réponse complète",
        r"\bstabilité\s+lésionnelle\b":                          "stabilité lésionnelle",
        r"\bprogression\s+lésionnelle\b":                        "progression lésionnelle",
        r"\bRECIST\b":                                           "critères RECIST",
        r"\blésions?\s+cibles?\b":                               "lésion cible",
        r"\blésions?\s+non\s+cibles?\b":                         "lésion non cible",
        r"\bnouvelle\s+lésion\b":                                "nouvelle lésion",

        # ══ PHONÉTIQUE WHISPER : GYNÉCOLOGIE AVANCÉE ══
        r"\bfibrome\s+utérin\b":                                 "fibrome utérin",
        r"\badenomyose\b":                                       "adénomyose",
        r"\bendométriose\b":                                     "endométriose",
        r"\bkyste\s+endométriosique\b":                          "kyste endométriosique",
        r"\bendométriome\b":                                     "endométriome",
        r"\bkyste\s+ovarien\b":                                  "kyste ovarien",
        r"\bkyste\s+dermoïde\b":                                 "kyste dermoïde",
        r"\btératome\b":                                         "tératome",
        r"\bovaire\s+polykystique\b":                            "ovaire polykystique",
        r"\bSOPK\b":                                             "SOPK",
        r"\btorsion\s+ovarienne\b":                              "torsion ovarienne",
        r"\bsalpingite\b":                                       "salpingite",
        r"\bhématosalpinx\b":                                    "hématosalpinx",
        r"\bhydrosalpinx\b":                                     "hydrosalpinx",
        r"\bGEU\b":                                              "GEU",
        r"\bgrossesse\s+extra[- ]?utérine\b":                    "GEU",
        r"\bpolype\s+endométrial\b":                             "polype endométrial",
        r"\bhystérectomie\b":                                    "hystérectomie",
        r"\bsalpingo[- ]?ovariectomie\b":                        "salpingo-ovariectomie",
        r"\bcol\s+utérin\b":                                     "col utérin",
        r"\bcanal\s+cervical\b":                                 "canal cervical",

        # ══ PHONÉTIQUE WHISPER : SÉNOLOGIE AVANCÉE ══
        r"\bmicrocalcifications?\b":                             "microcalcifications",
        r"\bmacrocalcifications?\b":                             "macrocalcifications",
        r"\bdensité\s+mammaire\b":                               "densité mammaire",
        r"\bglandulaire\s+dense\b":                              "glandulaire dense",
        r"\bdistorsion\s+architecturale\b":                      "distorsion architecturale",
        r"\basymétr[i]?e\s+de\s+densité\b":                      "asymétrie de densité",
        r"\bopacité\s+nodulaire\b":                              "opacité nodulaire",
        r"\bopacité\s+stellaire\b":                              "opacité stellaire",
        r"\badénopathie\s+axillaire\b":                          "adénopathie axillaire",
        r"\bimplant\s+mammaire\b":                               "implant mammaire",
        r"\brupture\s+intra[- ]?capsulaire\b":                   "rupture intracapsulaire",
        r"\brupture\s+extra[- ]?capsulaire\b":                   "rupture extracapsulaire",

        # ══ PHONÉTIQUE WHISPER : VASCULAIRE AVANCÉ ══
        r"\bathérosclérose\b":                                   "athérosclérose",
        r"\bplaque\s+calcifiée\b":                               "plaque calcifiée",
        r"\bplaque\s+molle\b":                                   "plaque molle",
        r"\bplaque\s+ulcérée\b":                                 "plaque ulcérée",
        r"\bsténose\s+serrée\b":                                 "sténose serrée",
        r"\bocclusion\s+artérielle\b":                           "occlusion artérielle",
        r"\bocclusion\s+veineuse\b":                             "occlusion veineuse",
        r"\bthrombose\s+veineuse\s+profonde\b":                  "TVP",
        r"\bTVP\b":                                              "TVP",
        r"\bembolie\s+pulmonaire\s+massive\b":                   "embolie pulmonaire massive",
        r"\banévrisme\s+de\s+l.aorte\s+abdominale\b":            "AAA",
        r"\bAAA\b":                                              "AAA",
        r"\bdissection\s+aortique\b":                            "dissection aortique",
        r"\bflap\s+intimal\b":                                   "flap intimal",
        r"\bvrai\s+chenal\b":                                    "vrai chenal",
        r"\bfaux\s+chenal\b":                                    "faux chenal",
        r"\bischémie\s+mésentérique\b":                          "ischémie mésentérique",
        r"\bfistule\s+artério[- ]?veineuse\b":                   "fistule artério-veineuse",
        r"\bsyndrome\s+de\s+leriche\b":                          "syndrome de Leriche",

        # ══ PHONÉTIQUE WHISPER : URO-GÉNITAL MASCULIN ══
        r"\bhyperplasie\s+bénigne\s+de\s+la\s+prostate\b":      "HBP",
        r"\bPI[- ]?RADS\b":                                      "PI-RADS",
        r"\bzone\s+périphérique\b":                              "zone périphérique",
        r"\bzone\s+de\s+transition\b":                           "zone de transition",
        r"\bzone\s+centrale\b":                                   "zone centrale",
        r"\bépididymo[- ]?orchite\b":                            "épididymo-orchite",
        r"\btorsion\s+testiculaire\b":                           "torsion testiculaire",
        r"\bséminome\b":                                         "séminome",
        r"\bnon\s+séminome\b":                                   "non séminome",

        # ══ PHONÉTIQUE WHISPER : FORMULATIONS COMPTE-RENDU ══
        r"\bpar\s+ailleurs\b":                                   "par ailleurs",
        r"\bà\s+noter\s+que\b":                                  "à noter que",
        r"\bpas\s+d.argument\b":                                 "pas d'argument",
        r"\bsous\s+toutes\s+réserves?\b":                        "sous toutes réserves",
        r"\ben\s+l.absence\s+de\b":                              "en l'absence de",
        r"\bà\s+mettre\s+en\s+rapport\s+avec\b":                 "à mettre en rapport avec",
        r"\bsans\s+signe\s+de\b":                                "sans signe de",
        r"\bdans\s+un\s+contexte\s+de\b":                        "dans un contexte de",
        r"\bsur\s+le\s+plan\s+morphologique\b":                  "sur le plan morphologique",
        r"\bchez\s+ce\s+patient\b":                              "chez ce patient",
        r"\bchez\s+cette\s+patiente\b":                          "chez cette patiente",
        r"\ben\s+conclusion\b":                                  "EN CONCLUSION",
        r"\bexamen\s+de\s+bonne\s+qualité\b":                    "examen de bonne qualité",
        r"\bnon\s+interprétable\b":                              "non interprétable",
        r"\bà\s+compléter\s+par\b":                              "à compléter par",
        r"\bà\s+corréler\s+avec\b":                              "à corréler avec",
        r"\bà\s+confronter\s+avec\b":                            "à confronter avec",
        r"\ben\s+rapport\s+avec\b":                              "en rapport avec",
        r"\bévocateur\s+de\b":                                   "évocateur de",
        r"\bfaisant\s+évoquer\b":                                "faisant évoquer",
        r"\bnon\s+exclu[e]?\b":                                  "non exclu",
        r"\bne\s+peut\s+pas\s+être\s+exclu[e]?\b":               "ne peut pas être exclu",
        r"\bmilite\s+en\s+faveur\s+de\b":                        "milite en faveur de",
        r"\bplaide\s+en\s+faveur\s+de\b":                        "plaide en faveur de",
    }

        # Corrections compilées (triées par longueur décroissante pour éviter conflits)
    _compiled = None

    @classmethod
    def _get_compiled(cls):
        if cls._compiled is None:
            cls._compiled = [
                (_re.compile(pattern, _re.IGNORECASE), replacement)
                for pattern, replacement in sorted(
                    cls.CORRECTIONS.items(),
                    key=lambda x: len(x[0]),
                    reverse=True,
                )
            ]
        return cls._compiled

    # ── Corrections phonétiques anglaises ──────────────────────────────
    # Erreurs typiques de Whisper sur le vocabulaire radiologique anglais
    CORRECTIONS_EN = {
            # ══ GALLBLADDER & BILIARY ══
            r"\bultrasound\s+science\b":              "ultrasound signs",
            r"\bultrasound\s+sines?\b":               "ultrasound signs",
            r"\becho\s+science\b":                    "echogenic",
            r"\bno\s+science\b":                      "no signs",
            r"\bgalla?[-\s]?bladder\b":               "gallbladder",
            r"\bgalladder\b":                         "gallbladder",
            r"\bgallbladder\s+wall\s+thick[en]+ing\b":"gallbladder wall thickening",
            r"\bchole[sz]ystitis\b":                  "cholecystitis",
            r"\bcholecist[iy]tis\b":                  "cholecystitis",
            r"\bchole[sz]ystic\b":                    "cholecystic",
            r"\balithia[sz]ic\b":                     "alithiasic",
            r"\bchole[sz]yst[eo]c[eo]le\b":           "cholecystocele",
            r"\bcholeli[td]hiasis\b":                 "cholelithiasis",
            r"\bcommon\s+bile\s+duck\b":              "common bile duct",
            r"\bcommon\s+bile\s+duct\b":              "common bile duct",
            r"\bco[mn]{1,2}[oe]n\s+hepatic\s+duct\b":"common hepatic duct",
            r"\bcyst[iy]c\s+duct\b":                  "cystic duct",
            r"\bch[oe]led[oe]c[ou]s\b":               "choledochus",
            r"\bcholedoch[ou]l[iy]thiasis\b":          "choledocholithiasis",
            r"\bintrahep[ae]tic\s+bil[iy]ary\b":      "intrahepatic biliary",
            r"\bpneumobil[iy]a\b":                    "pneumobilia",
            r"\bbil[iy]ary\s+dil[ae]t[ae]tion\b":    "biliary dilatation",
            r"\bbil[iy]oma\b":                        "biloma",
            r"\bmirr[iy]z[zi]\b":                     "Mirizzi",
            r"\bkl[ae]tsk[iy]n\b":                    "Klatskin",
            r"\bvater[\'s]+\s+ampulla\b":             "ampulla of Vater",
            r"\bampulla\s+of\s+vat[ae]r\b":           "ampulla of Vater",
            r"\bsludge\b":                            "sludge",
            r"\bbil[iy]ary\s+sludge\b":               "biliary sludge",
            r"\bmurphy[\'s]*\s+sign\b":               "Murphy's sign",
            r"\bsonographic\s+murph[yi]\b":           "sonographic Murphy sign",
            r"\bpericholecystic\b":                   "pericholecystic",
            r"\bper[iy]cholecyst[iy]c\b":             "pericholecystic",
            r"\bhydr[oe]ps\s+of\s+gallbladder\b":     "hydrops of gallbladder",
            r"\bempyema\s+gallbladder\b":             "empyema of the gallbladder",
            r"\bgang[gr]en[oe]us\s+cholecystitis\b":  "gangrenous cholecystitis",
            r"\bacalculous\s+cholecystitis\b":        "acalculous cholecystitis",
            r"\bwall[- ]thickening\b":               "wall thickening",

            # ══ LIVER ══
            r"\bhep[ae]tom[ae]galy\b":                "hepatomegaly",
            r"\bhepatomeg[ae]l[iy]\b":                "hepatomegaly",
            r"\bcirrhosis\b":                         "cirrhosis",
            r"\bcir[rh]+osis\b":                      "cirrhosis",
            r"\bst[ae][ae]to[sz]is\b":                "steatosis",
            r"\bhe[ph]atic\s+st[ae][ae]to[sz]is\b":  "hepatic steatosis",
            r"\bfatty\s+liv[eo]r\b":                  "fatty liver",
            r"\bnon[-\s]?alco[hk][oe]lic\s+fatty\b":  "non-alcoholic fatty",
            r"\bNAFLD\b":                             "NAFLD",
            r"\bNASH\b":                              "NASH",
            r"\bhep[ae]toc[ae]llular\s+carc[iy]noma\b":"hepatocellular carcinoma",
            r"\bHCC\b":                               "HCC",
            r"\bchol[ae]ngiocarc[iy]noma\b":          "cholangiocarcinoma",
            r"\bhep[ae]tic\s+abscess\b":              "hepatic abscess",
            r"\bamoeb[iy]c\s+abscess\b":              "amoebic abscess",
            r"\bpyog[ae]nic\s+abscess\b":             "pyogenic abscess",
            r"\bfocal\s+nodular\s+hyp[ae]rplasia\b":  "focal nodular hyperplasia",
            r"\bFNH\b":                               "FNH",
            r"\bhep[ae]tic\s+ad[ae]noma\b":           "hepatic adenoma",
            r"\bhep[ae]tic\s+h[ae]mang[iy]oma\b":    "hepatic haemangioma",
            r"\bhep[ae]tic\s+metast[ae]sis\b":        "hepatic metastasis",
            r"\bhep[ae]tic\s+cyst\b":                 "hepatic cyst",
            r"\bechinococcal\s+cyst\b":               "echinococcal cyst",
            r"\bhydatid\s+cyst\b":                    "hydatid cyst",
            r"\bportal\s+hyp[ae]rt[ae]nsion\b":       "portal hypertension",
            r"\bp[ae]rtal\s+vein\s+thrombosis\b":     "portal vein thrombosis",
            r"\bBudd[-\s]?Chiari\b":                  "Budd-Chiari",
            r"\bhep[ae]tic\s+vein[sz]\b":             "hepatic veins",
            r"\bhep[ae]tic\s+fibrosis\b":             "hepatic fibrosis",
            r"\bhep[ae]tic\s+parench[iy]ma\b":        "hepatic parenchyma",
            r"\bdome\s+of\s+liv[ae]r\b":              "dome of liver",
            r"\bGliss[oe]n[\'s]+\s+caps[ue]le\b":    "Glisson's capsule",
            r"\bcouinaud\b":                          "Couinaud",
            r"\bhep[ae]tic\s+[ae]rt[ae]ry\b":         "hepatic artery",
            r"\bhep[ae]torenal\b":                    "hepatorenal",
            r"\bGliss[oe]n\b":                        "Glisson",
            r"\belast[oe]graphy\b":                   "elastography",
            r"\bfibro[sz]c[ae]n\b":                   "FibroScan",
            r"\bper[iy]portal\b":                     "periportal",

            # ══ PANCREAS ══
            r"\bpancr[ae][ae]s\b":                    "pancreas",
            r"\bpancr[ae]atic\b":                     "pancreatic",
            r"\bpancr[ae]atitis\b":                   "pancreatitis",
            r"\bacute\s+pancr[ae]atitis\b":            "acute pancreatitis",
            r"\bchronic\s+pancr[ae]atitis\b":          "chronic pancreatitis",
            r"\bpseudo[- ]?cyst\b":                   "pseudocyst",
            r"\bpancr[ae]atic\s+pseudo[- ]?cyst\b":   "pancreatic pseudocyst",
            r"\bwirsung\b":                           "Wirsung",
            r"\bduct\s+of\s+wirsung\b":               "duct of Wirsung",
            r"\bsant[oe]r[iy]ni\b":                   "Santorini",
            r"\bpancr[ae]atic\s+duct\b":              "pancreatic duct",
            r"\bpancr[ae]atic\s+n[ae]crosis\b":       "pancreatic necrosis",
            r"\bpancr[ae]atic\s+c[ao]lc[iy]fication\b":"pancreatic calcification",
            r"\bIPMN\b":                              "IPMN",
            r"\bintraductal\s+papill[ae]ry\b":         "intraductal papillary",
            r"\bmucinous\s+cystic\b":                 "mucinous cystic",
            r"\bserous\s+cystic\b":                   "serous cystic",
            r"\bpancr[ae]atic\s+adenocarcinoma\b":    "pancreatic adenocarcinoma",
            r"\buncinate\s+process\b":                "uncinate process",
            r"\bunc[iy]nate\b":                       "uncinate",
            r"\bpancr[ae]atic\s+h[ae]ad\b":           "head of pancreas",
            r"\bpancr[ae]atic\s+body\b":              "body of pancreas",
            r"\bpancr[ae]atic\s+tail\b":              "tail of pancreas",
            r"\bpancr[ae]atic\s+divisum\b":           "pancreas divisum",
            r"\bann[ue]lar\s+pancr[ae]as\b":          "annular pancreas",
            r"\bpancr[ae]atic\s+lipomatosis\b":       "pancreatic lipomatosis",
            r"\bauto[-\s]?immune\s+pancr[ae]atitis\b":"autoimmune pancreatitis",

            # ══ SPLEEN ══
            r"\bspl[ae]nom[ae]galy\b":                "splenomegaly",
            r"\bsplenom[ae]g[ae]l[iy]\b":             "splenomegaly",
        r"\bsplenom[ae]g[ae]l[ae]y\b":               "splenomegaly",
            r"\bspl[ae]en\b":                         "spleen",
            r"\bspl[ae]nic\b":                        "splenic",
            r"\bacc[ae]ssory\s+spl[ae]en\b":          "accessory spleen",
            r"\bsplenunculus\b":                      "splenunculus",
            r"\bspl[ae]nic\s+infarct\b":              "splenic infarct",
            r"\bspl[ae]nic\s+laceration\b":           "splenic laceration",
            r"\bspl[ae]nic\s+rupture\b":              "splenic rupture",
            r"\bspl[ae]nic\s+cyst\b":                 "splenic cyst",
            r"\bspl[ae]nic\s+abscess\b":              "splenic abscess",
            r"\bspl[ae]nic\s+artery\b":               "splenic artery",
            r"\bspl[ae]nic\s+vein\b":                 "splenic vein",
            r"\bhep[ae]to[-\s]spl[ae]nom[ae]galy\b":  "hepatosplenomegaly",
            r"\bspl[ae]nic\s+h[ae]mang[iy]oma\b":    "splenic haemangioma",
            r"\bspl[ae]nic\s+lymphoma\b":             "splenic lymphoma",

            # ══ KIDNEYS & URINARY ══
            r"\bk[iy]dney[sz]?\b":                    "kidneys",
            r"\bren[ae]l\b":                          "renal",
            r"\bren[ae]l\s+parench[iy]ma\b":          "renal parenchyma",
            r"\bcortico[-\s]medullary\b":             "corticomedullary",
            r"\bcorticomed[ue]llary\b":               "corticomedullary",
            r"\bcortex\b":                            "cortex",
            r"\bmed[ue]llary\b":                      "medullary",
            r"\bren[ae]l\s+cortex\b":                 "renal cortex",
            r"\bren[ae]l\s+med[ue]lla\b":             "renal medulla",
            r"\bren[ae]l\s+s[iy]n[ue]s\b":            "renal sinus",
            r"\bren[ae]l\s+p[ae]lvis\b":              "renal pelvis",
            r"\bpyel[oe]n\b":                         "pyelon",
            r"\bpyel[oe]nephritis\b":                 "pyelonephritis",
            r"\bren[ae]l\s+calculus\b":               "renal calculus",
            r"\bnephrolithiasis\b":                   "nephrolithiasis",
            r"\bnephro[Ll]ithiasis\b":                "nephrolithiasis",
            r"\buren[ae]lithiasis\b":                 "ureterolithiasis",
            r"\burolithiasis\b":                      "urolithiasis",
            r"\bhydronephrosis\b":                    "hydronephrosis",
            r"\bhy[dp]ronephrosis\b":                 "hydronephrosis",
            r"\bhydro[ue]r[ae]ter\b":                 "hydroureter",
            r"\buren[ae]t[ae]r\b":                    "ureter",
            r"\buren[ae]t[ae]r[ae]l\b":               "ureteral",
            r"\buren[ae]t[ae]r[oe]c[ae]le\b":         "ureterocele",
            r"\bUPJ\b":                               "UPJ",
            r"\bUVJ\b":                               "UVJ",
            r"\bren[ae]l\s+cyst\b":                   "renal cyst",
            r"\bBosniak\b":                           "Bosniak",
            r"\bbosniak\b":                           "Bosniak",
            r"\bpar[ae]p[ae]lv[iy]c\s+cyst\b":        "parapelvic cyst",
            r"\bangiomyolipoma\b":                    "angiomyolipoma",
            r"\baml\b":                               "AML",
            r"\boncocytoma\b":                        "oncocytoma",
            r"\bren[ae]l\s+cell\s+carc[iy]noma\b":   "renal cell carcinoma",
            r"\bRCC\b":                               "RCC",
            r"\bWilms[\'s]+\s+tu[mn]or\b":            "Wilms tumor",
            r"\bnephrobla[sz]toma\b":                 "nephroblastoma",
            r"\btransit[iy]onal\s+cell\b":            "transitional cell",
            r"\burothelial\b":                        "urothelial",
            r"\bren[ae]l\s+infarct\b":               "renal infarct",
            r"\bren[ae]l\s+artery\s+st[ae]nosis\b":  "renal artery stenosis",
            r"\bren[ae]l\s+vein\s+thrombosis\b":      "renal vein thrombosis",
            r"\bren[ae]l\s+transplant\b":             "renal transplant",
            r"\bnephr[oe]c[ae]l[iy]\b":               "nephrocaly",
            r"\bmedullary\s+sponge\b":               "medullary sponge",
            r"\bpolycystic\s+kidney\b":               "polycystic kidney",
            r"\bPKD\b":                               "PKD",
            r"\baut[oe]somal\s+dominant\b":           "autosomal dominant",
            r"\bauer[- ]?fist[ae]r\b":               "arteriovenous fistula",
            r"\bbl[ae]dder\b":                        "bladder",
            r"\bur[iy]n[ae]ry\s+bl[ae]dder\b":        "urinary bladder",
            r"\bbl[ae]dder\s+wall\b":                 "bladder wall",
            r"\bbl[ae]dder\s+neck\b":                 "bladder neck",
            r"\bbl[ae]dder\s+outl[ae]t\b":            "bladder outlet",
            r"\btr[ae]b[ae]cul[ae]tion\b":            "trabeculation",
            r"\bpost[- ]?void\s+res[iy]du[ae]l\b":    "post-void residual",
            r"\bPVR\b":                               "PVR",
            r"\bur[ae]thra\b":                        "urethra",

            # ══ PROSTATE ══
            r"\bpro[sz]t[ae]t[ae]\b":                "prostate",
            r"\bpro[sz]t[ae]tic\b":                  "prostatic",
            r"\bBPH\b":                              "BPH",
            r"\bb[ae]nign\s+pro[sz]t[ae]tic\s+hyp[ae]rplasia\b": "benign prostatic hyperplasia",
            r"\bPSA\b":                              "PSA",
            r"\bpr[oe][sz]t[ae]te\s+carc[iy]noma\b": "prostate carcinoma",
            r"\bPIRADS\b":                           "PI-RADS",
            r"\bPI[-\s]?RADS\b":                     "PI-RADS",
            r"\bseminal\s+ves[iy]cl[ae]s?\b":        "seminal vesicles",
            r"\bejacul[ae]t[oe]ry\s+duct\b":         "ejaculatory duct",
            r"\bper[iy]ph[ae]ral\s+zone\b":          "peripheral zone",
            r"\btr[ae]ns[iy]tion[ae]l\s+zone\b":     "transitional zone",
            r"\bcent[re]al\s+zone\b":                "central zone",
            r"\bant[ae]rior\s+fibromuscular\b":       "anterior fibromuscular",
            r"\bpr[oe][sz]t[ae]tic\s+ur[ae]thra\b":  "prostatic urethra",

            # ══ GYNECOLOGY / PELVIS ══
            r"\but[ae]r[ue]s\b":                     "uterus",
            r"\but[ae]r[iy]n[ae]\b":                 "uterine",
            r"\bendo[-\s]?metr[iy]um\b":             "endometrium",
            r"\bmy[oe][-\s]?metr[iy]um\b":           "myometrium",
            r"\bparam[ae]tr[iy]um\b":                "parametrium",
            r"\bcerv[iy]x\b":                        "cervix",
            r"\bcerv[iy]cal\b":                      "cervical",
            r"\bov[ae]r[iy]\b":                      "ovary",
            r"\bov[ae]r[iy][ae]n\b":                 "ovarian",
            r"\bov[ae]r[iy][ae]n\s+cyst\b":          "ovarian cyst",
            r"\bfoll[iy]cl[ae]\b":                   "follicle",
            r"\bcorp[ue]s\s+lut[ae]um\b":            "corpus luteum",
            r"\bfallopian\s+tube\b":                 "fallopian tube",
            r"\bhematosalpinx\b":                    "hematosalpinx",
            r"\bpyosalpinx\b":                       "pyosalpinx",
            r"\bhydr[oe]salpinx\b":                  "hydrosalpinx",
            r"\bpelvic\s+inflam[mn][ae]tory\b":       "pelvic inflammatory",
            r"\bPID\b":                              "PID",
            r"\bend[oe]metr[iy][oe]sis\b":           "endometriosis",
            r"\bmy[oe]ma\b":                         "myoma",
            r"\bley[oe]my[oe]ma\b":                  "leiomyoma",
            r"\bfibr[oe]id\b":                       "fibroid",
            r"\bint[ae]rstitial\s+fibr[oe]id\b":     "interstitial fibroid",
            r"\bsubserosal\b":                       "subserosal",
            r"\bsubmucosal\b":                       "submucosal",
            r"\bcalc[iy]fied\s+fibr[oe]id\b":        "calcified fibroid",
            r"\bret[roe]v[ae]rted\b":                "retroverted",
            r"\bant[ae]verted\b":                    "anteverted",
            r"\bDougl[ae]s\b":                       "Douglas",
            r"\bcul[- ]?de[- ]?sac\b":               "cul-de-sac",
            r"\bch[oe]col[ae]te\s+cyst\b":           "chocolate cyst",
            r"\bend[oe]metri[oe]ma\b":               "endometrioma",
            r"\bPCOS\b":                             "PCOS",
            r"\bpolycystic\s+ov[ae]r[iy][ae]n\b":   "polycystic ovarian",
            r"\btorsion\b":                          "torsion",
            r"\bov[ae]r[iy][ae]n\s+torsion\b":       "ovarian torsion",
            r"\bect[oe]pic\s+pr[ae]gn[ae]ncy\b":     "ectopic pregnancy",
            r"\bint[ae]rut[ae]r[iy]n[ae]\b":         "intrauterine",
            r"\bgest[ae]tional\s+sac\b":             "gestational sac",
            r"\bembry[oe]\b":                        "embryo",
            r"\bf[ae]tal\s+heart\b":                 "fetal heart",
            r"\bcrown[- ]?rump\b":                   "crown-rump",
            r"\bCRL\b":                              "CRL",
            r"\bplacenta\b":                         "placenta",
            r"\bplacenta\s+pr[ae]via\b":             "placenta praevia",
            r"\babruption\b":                        "abruption",
            r"\bamni[oe]tic\s+fluid\b":              "amniotic fluid",
            r"\bpoly[-\s]?hydramni[oe]s\b":          "polyhydramnios",
            r"\b[oe]ligo[-\s]?hydramni[oe]s\b":      "oligohydramnios",

            # ══ AORTA & VESSELS ══
            r"\baort[ae]\b":                         "aorta",
            r"\baort[iy]c\b":                        "aortic",
            r"\babdominal\s+aort[ae]\b":              "abdominal aorta",
            r"\bthorac[iy]c\s+aort[ae]\b":           "thoracic aorta",
            r"\bAAA\b":                              "AAA",
            r"\baort[iy]c\s+an[ae]ur[iy]sm\b":       "aortic aneurysm",
            r"\ban[ae]ur[iy]sm\b":                   "aneurysm",
            r"\bcel[iy][ae]c\s+[ae]rt[ae]ry\b":      "celiac artery",
            r"\bcel[iy][ae]c\s+trunk\b":             "celiac trunk",
            r"\bSMA\b":                              "SMA",
            r"\bsup[ae]ri[oe]r\s+mes[ae]nt[ae]ric\s+[ae]rt[ae]ry\b": "superior mesenteric artery",
            r"\bIMA\b":                              "IMA",
            r"\binf[ae]ri[oe]r\s+mes[ae]nt[ae]ric\s+[ae]rt[ae]ry\b":  "inferior mesenteric artery",
            r"\binf[ae]ri[oe]r\s+v[ae]na\s+c[ae]v[ae]\b": "inferior vena cava",
            r"\bIVC\b":                              "IVC",
            r"\bp[oe]rt[ae]l\s+vein\b":              "portal vein",
            r"\bsp[ae]l[ae]nic\s+vein\b":            "splenic vein",
            r"\bSMV\b":                              "SMV",
            r"\bp[oe]rtom[ae]s[ae]nt[ae]ric\b":      "portomesenteric",
            r"\bp[oe]rt[ae]l\s+hyp[ae]rt[ae]nsion\b":"portal hypertension",
            r"\bv[ae]ric[ae]s\b":                    "varices",
            r"\[oe]soph[ae]g[ae]al\s+v[ae]ric[ae]s\b": "oesophageal varices",
            r"\brev[ae]rs[ae]d\s+p[oe]rt[ae]l\s+flow\b": "reversed portal flow",
            r"\bh[ae]patic\s+[ae]rt[ae]ry\b":        "hepatic artery",
            r"\bsp[ae]l[ae]nic\s+[ae]rt[ae]ry\b":    "splenic artery",
            r"\bp[oe]rt[oe][- ]?sys[te]mic\b":       "portosystemic",
            r"\bcavernous\s+transform[ae]tion\b":     "cavernous transformation",
            r"\bp[oe]rt[ae]l\s+cav[ae]rnoma\b":      "portal cavernoma",
            r"\bdissection\b":                       "dissection",
            r"\baort[iy]c\s+dissection\b":           "aortic dissection",
            r"\btrue\s+lum[ae]n\b":                  "true lumen",
            r"\bfalse\s+lum[ae]n\b":                 "false lumen",
            r"\bintim[ae]l\s+flap\b":                "intimal flap",
            r"\bst[ae]nosis\b":                      "stenosis",
            r"\bocclusion\b":                        "occlusion",
            r"\bth[oe]mbosis\b":                     "thrombosis",
            r"\bthromb[oe]sis\b":                    "thrombosis",
            r"\bth[oe]mbus\b":                       "thrombus",
            r"\bcalc[iy]fied\s+plaque\b":            "calcified plaque",
            r"\bather[oe]sclerosis\b":               "atherosclerosis",
            r"\bather[oe]scl[ae]rotic\b":            "atherosclerotic",

            # ══ APPENDIX / BOWEL ══
            r"\bap[pb]end[iy]citis\b":               "appendicitis",
            r"\bap[pb]end[iy]c[iy]t[iy]s\b":         "appendicitis",
            r"\bap[pb]end[iy]x\b":                   "appendix",
            r"\bap[pb]end[iy]col[iy]th\b":           "appendicolith",
            r"\bf[ae]cal[iy]th\b":                   "fecalith",
            r"\bpert[iy]appe[nd]+[iy]c[ae]al\b":     "periappendiceal",
            r"\bp[ae]rf[oe]ration\b":                "perforation",
            r"\bph[ae]gmon[ae]\b":                   "phlegmone",
            r"\babscess\b":                          "abscess",
            r"\bp[ae]r[iy]app[ae]nd[iy]c[ae]al\s+abscess\b": "periappendiceal abscess",
            r"\bbow[ae]l\s+loop[sz]?\b":             "bowel loops",
            r"\bsmall\s+bow[ae]l\b":                 "small bowel",
            r"\blarger?\s+bow[ae]l\b":               "large bowel",
            r"\bdil[ae]t[ae]d\s+bow[ae]l\b":         "dilated bowel",
            r"\bbow[ae]l\s+obstruction\b":           "bowel obstruction",
            r"\bper[iy]stalsis\b":                   "peristalsis",
            r"\bper[iy]staltic\b":                   "peristaltic",
            r"\bpneum[oe]per[iy]ton[ae]um\b":        "pneumoperitoneum",
            r"\bfree\s+air\b":                       "free air",
            r"\bintra[- ]?abdominal\s+air\b":        "intraabdominal air",
            r"\bher[iy]a\b":                         "hernia",
            r"\binguinal\s+hernia\b":                "inguinal hernia",
            r"\bumb[iy]l[iy]cal\s+hernia\b":         "umbilical hernia",
            r"\bepig[ae]stric\s+hernia\b":           "epigastric hernia",
            r"\bincis[iy]onal\s+hernia\b":           "incisional hernia",
            r"\bh[iy]atal\s+hernia\b":               "hiatal hernia",
            r"\bh[iy]atus\s+hernia\b":               "hiatus hernia",
            r"\bintussusception\b":                  "intussusception",
            r"\bvolvulus\b":                         "volvulus",
            r"\bColitis\b":                          "colitis",
            r"\bCrohn[\'s]+\b":                      "Crohn's",
            r"\bMekel[\'s]+\s+diverticulum\b":       "Meckel's diverticulum",
            r"\bdivert[iy]cul[iy]tis\b":             "diverticulitis",
            r"\bdivert[iy]cul[oe]sis\b":             "diverticulosis",
            r"\bmes[ae]nt[ae]r[iy]c\b":              "mesenteric",

            # ══ PERITONEUM / ASCITES ══
            r"\basc[iy]t[ae]s\b":                    "ascites",
            r"\bp[ae]r[iy]t[oe]n[ae]al\s+fluid\b":   "peritoneal fluid",
            r"\bfr[ae][ae]\s+fluid\b":               "free fluid",
            r"\bfr[ae][ae]\s+fl[ou][id][id]\b":      "free fluid",
            r"\bp[ae]r[iy]t[oe]n[ae]al\s+carcinoma[sz]is\b": "peritoneal carcinomatosis",
            r"\bom[ae]nt[ae]l\s+cake\b":             "omental cake",
            r"\bp[ae]r[iy]t[oe]n[ae]al\s+seeding\b": "peritoneal seeding",
            r"\bsubph[ae]r[ae]nic\b":                "subphrenic",
            r"\bsubh[ae]p[ae]tic\b":                 "subhepatic",
            r"\bMorrison[\'s]+\s+pouch\b":           "Morrison's pouch",
            r"\bsp[ae]n[oe]r[ae]ct[ae]l\b":          "splenorenal",
            r"\bsp[ae]n[oe]r[ae]n[ae]l\b":           "splenorenal",

            # ══ LYMPH NODES ══
            r"\blymph\s+nod[ae]s?\b":                "lymph nodes",
            r"\blymph[ae]d[ae]nopathy\b":            "lymphadenopathy",
            r"\breact[iy]ve\s+lymph\s+nod[ae]s?\b":  "reactive lymph nodes",
            r"\bparaaort[iy]c\b":                    "paraaortic",
            r"\bpara[- ]?aort[iy]c\s+lymph\b":       "paraaortic lymph",
            r"\bretroper[iy]ton[ae]al\b":             "retroperitoneal",
            r"\bcel[iy][ae]c\s+lymph\b":             "celiac lymph",
            r"\bhil[ae]r\s+lymph\b":                 "hilar lymph",
            r"\bmes[ae]nt[ae]r[iy]c\s+lymph\b":      "mesenteric lymph",
            r"\binguinal\s+lymph\b":                 "inguinal lymph",
            r"\baxillary\s+lymph\b":                 "axillary lymph",

            # ══ THYROID & NECK ══
            r"\bthyr[oe][iy]d\b":                    "thyroid",
            r"\bthyr[oe][iy]d[iy]tis\b":             "thyroiditis",
            r"\bhashimoto[\'s]+\b":                  "Hashimoto's",
            r"\bgrav[ae]s\b":                        "Graves",
            r"\bthyr[oe][iy]d\s+nod[ue]le\b":        "thyroid nodule",
            r"\bTI[-\s]?RADS\b":                     "TI-RADS",
            r"\bthyroids\b":                         "thyroid",
            r"\bparat[hy]yr[oe][iy]d\b":             "parathyroid",
            r"\bgo[iy]t[ae]r\b":                     "goitre",
            r"\bthyr[oe][iy]d\s+isthmus\b":          "thyroid isthmus",
            r"\bhemithyr[oe][iy]dect[oe]my\b":       "hemithyroidectomy",
            r"\bpap[iy]llary\s+carc[iy]noma\b":      "papillary carcinoma",
            r"\bfoll[iy]cular\s+carc[iy]noma\b":     "follicular carcinoma",
            r"\bmed[ue]llary\s+carc[iy]noma\b":      "medullary carcinoma",
            r"\banaplast[iy]c\s+carc[iy]noma\b":     "anaplastic carcinoma",
            r"\bcervical\s+lymph\b":                 "cervical lymph",
            r"\bsternocleidomast[oe][iy]d\b":        "sternocleidomastoid",
            r"\bjugular\s+vein\b":                   "jugular vein",
            r"\bcarotid\s+[ae]rt[ae]ry\b":           "carotid artery",
            r"\bcommon\s+carotid\b":                 "common carotid",
            r"\bint[ae]rnal\s+carotid\b":            "internal carotid",
            r"\bext[ae]rnal\s+carotid\b":            "external carotid",

            # ══ BREAST / SENOLOGY ══
            r"\bmamm[ae]ry\b":                       "mammary",
            r"\bmamm[ae]ry\s+gl[ae]nd\b":            "mammary gland",
            r"\bBI[-\s]?RADS\b":                     "BI-RADS",
            r"\bLI[-\s]?RADS\b":                     "LI-RADS",
            r"\bLung[-\s]?RADS\b":                   "Lung-RADS",
            r"\bfibroad[ae]noma\b":                  "fibroadenoma",
            r"\bfibrocy[sz]t[iy]c\b":                "fibrocystic",
            r"\bductal\s+carc[iy]noma\b":            "ductal carcinoma",
            r"\blobular\s+carc[iy]noma\b":           "lobular carcinoma",
            r"\bDCIS\b":                             "DCIS",
            r"\bmas[sz]titis\b":                     "mastitis",
            r"\bgalactocoele\b":                     "galactocoele",
            r"\bnipple\s+r[ae]traction\b":           "nipple retraction",
            r"\bax[iy]ll[ae]ry\b":                   "axillary",
            r"\bax[iy]ll[ae]ry\s+lymph\b":           "axillary lymph",
            r"\bimplant\b":                          "implant",
            r"\bbreast\s+implant\b":                 "breast implant",
            r"\bcapsular\s+contracture\b":           "capsular contracture",
            r"\bintracapsular\b":                    "intracapsular",
            r"\bextracapsular\b":                    "extracapsular",
            r"\bsilicone\s+rupture\b":               "silicone rupture",

            # ══ TESTIS / SCROTUM ══
            r"\btest[iy]s\b":                        "testis",
            r"\btest[iy]cl[ae]s?\b":                 "testis",
            r"\bep[iy]d[iy]dymis\b":                 "epididymis",
            r"\borchitis\b":                         "orchitis",
            r"\bep[iy]d[iy]dymo[-\s]?orchitis\b":    "epididymo-orchitis",
            r"\bhyd[roe]c[ae]l[ae]\b":               "hydrocele",
            r"\bvar[iy]c[oe]c[ae]l[ae]\b":           "varicocele",
            r"\bspermat[oe]c[ae]l[ae]\b":            "spermatocele",
            r"\btest[iy]cular\s+torsion\b":          "testicular torsion",
            r"\btest[iy]cular\s+microlithiasis\b":   "testicular microlithiasis",
            r"\btest[iy]cular\s+tumor\b":            "testicular tumor",
            r"\bsemin[oe]ma\b":                      "seminoma",
            r"\bnon[-\s]?semin[oe]ma[t]ous\b":       "non-seminomatous",
            r"\btunica\s+albugin[ae]a\b":            "tunica albuginea",
            r"\bmediastinum\s+testis\b":             "mediastinum testis",

            # ══ MUSCULOSKELETAL ══
            r"\btendon\b":                           "tendon",
            r"\btendin[oe]sis\b":                    "tendinosis",
            r"\btendinop[ae]thy\b":                  "tendinopathy",
            r"\btendinitis\b":                       "tendinitis",
            r"\bt[ae]no[-\s]?synovitis\b":           "tenosynovitis",
            r"\bsynovial\s+sheath\b":                "synovial sheath",
            r"\brotator\s+cuff\b":                   "rotator cuff",
            r"\bsupr[ae]spinatus\b":                 "supraspinatus",
            r"\binfr[ae]spinatus\b":                 "infraspinatus",
            r"\bsubscapularis\b":                    "subscapularis",
            r"\bt[ae]r[ae]s\s+minor\b":              "teres minor",
            r"\bbiceps\s+tendon\b":                  "biceps tendon",
            r"\bachilles\s+tendon\b":                "Achilles tendon",
            r"\bplantar\s+fasci[ae]\b":              "plantar fascia",
            r"\bplant[ae]r\s+fascitis\b":            "plantar fasciitis",
            r"\bmeniscus\b":                         "meniscus",
            r"\bmeniscal\b":                         "meniscal",
            r"\bACL\b":                              "ACL",
            r"\bPCL\b":                              "PCL",
            r"\bant[ae]r[iy]or\s+cr[ue]ciate\b":    "anterior cruciate",
            r"\bpost[ae]r[iy]or\s+cr[ue]ciate\b":   "posterior cruciate",
            r"\bBaker[\'s]+\s+cyst\b":               "Baker's cyst",
            r"\bpopliteal\s+cyst\b":                 "popliteal cyst",
            r"\bsynov[iy]al\s+fluid\b":              "synovial fluid",
            r"\bsynov[iy]tis\b":                     "synovitis",
            r"\barticul[ae]r\s+effu[sz]ion\b":       "articular effusion",
            r"\bjoint\s+effu[sz]ion\b":              "joint effusion",
            r"\bcarpal\s+tunnel\b":                  "carpal tunnel",
            r"\bde\s+Qu[ae]rv[ae]in\b":              "de Quervain",
            r"\bganglia\b":                          "ganglion",
            r"\bganglion\s+cyst\b":                  "ganglion cyst",
            r"\bburs[iy]tis\b":                      "bursitis",
            r"\bsubacr[oe]m[iy]al\s+burs[iy]tis\b":  "subacromial bursitis",
            r"\bsubdelto[iy]d\s+burs[iy]tis\b":      "subdeltoid bursitis",
            r"\bischial\s+burs[iy]tis\b":            "ischial bursitis",
            r"\boste[oe]myelitis\b":                 "osteomyelitis",
            r"\bperiosteal\b":                       "periosteal",
            r"\bcortical\s+break\b":                 "cortical break",

            # ══ CHEST / THORAX ══
            r"\bpneumothorax\b":                     "pneumothorax",
            r"\bpl[ae]ur[ae]l\s+effu[sz]ion\b":      "pleural effusion",
            r"\bpl[ae]ur[ae]l\s+fluid\b":            "pleural fluid",
            r"\bpl[ae]ura\b":                        "pleura",
            r"\bhemothorax\b":                       "haemothorax",
            r"\bpyothorax\b":                        "pyothorax",
            r"\bchylothorax\b":                      "chylothorax",
            r"\bpericardial\s+effu[sz]ion\b":        "pericardial effusion",
            r"\bpericardium\b":                      "pericardium",
            r"\bt[ae]mponade\b":                     "tamponade",
            r"\bmedi[ae]stinum\b":                   "mediastinum",
            r"\bhilum\b":                            "hilum",
            r"\bh[iy]lar\b":                         "hilar",
            r"\blung\s+n[oe]dule\b":                 "lung nodule",
            r"\bpulmon[ae]ry\s+n[oe]dule\b":         "pulmonary nodule",
            r"\bconsolidation\b":                    "consolidation",
            r"\bground\s+glass\b":                   "ground glass",
            r"\br[ae]t[iy]cul[ae]tion\b":            "reticulation",
            r"\bhon[ae]yc[oe]mb[iy]ng\b":            "honeycombing",
            r"\bat[ae]l[ae]ct[ae]sis\b":             "atelectasis",
            r"\bemphy[sz][ae]ma\b":                  "emphysema",
            r"\bfibr[oe]sis\b":                      "fibrosis",
            r"\bpulmon[ae]ry\s+fibr[oe]sis\b":       "pulmonary fibrosis",
            r"\bIPF\b":                              "IPF",
            r"\bbronchi[ae]ct[ae]sis\b":             "bronchiectasis",
            r"\bbronch[iy][ae]l\b":                  "bronchial",
            r"\btr[ae]ch[ae][ae]\b":                 "trachea",
            r"\bcar[iy]n[ae]\b":                     "carina",
            r"\bdiaphragm\b":                        "diaphragm",
            r"\bcost[oe]phren[iy]c\b":               "costophrenic",
            r"\bcost[oe]phrenic\s+angl[ae]\b":       "costophrenic angle",
            r"\bphren[iy]c\b":                       "phrenic",

            # ══ DESCRIPTORS ══
            r"\banem?cho[iy]c\b":                    "anechoic",
            r"\ban[ae]c[oe]ic\b":                    "anechoic",
            r"\bhy[pb]oec[oe]ic\b":                  "hypoechoic",
            r"\bhy[pb]erec[oe]ic\b":                 "hyperechoic",
            r"\bis[oe]ec[oe]ic\b":                   "isoechoic",
            r"\bec[hk][oe]gen[iy]c\b":              "echogenic",
            r"\bec[hk][oe]gen[iy]c[iy]ty\b":        "echogenicity",
            r"\bhomog[ae]n[iy][ou][ou]s\b":          "homogeneous",
            r"\bh[ae]t[ae]rog[ae]n[iy][ou][ou]s\b":  "heterogeneous",
            r"\bhomog[ae]nous\b":                    "homogeneous",
            r"\bh[ae]t[ae]rog[ae]nous\b":            "heterogeneous",
            r"\barm[ou][ou]r[ae]d\s+size\b":         "normal size",
            r"\bsemi[- ]?replacement\b":             "normal size",
            r"\bnorm[ae]l\s+s[iy]ze\b":              "normal size",
            r"\benlarg[ae]d\b":                      "enlarged",
            r"\bslight[ly]+\s+enlarg[ae]d\b":        "slightly enlarged",
            r"\bmoderately\s+enlarg[ae]d\b":         "moderately enlarged",
            r"\bm[ae][ae]sur[iy]ng\b":               "measuring",
            r"\bapprox[iy]mat[ae]ly\b":              "approximately",
            r"\bregular\s+cont[ou][ou]rs\b":         "regular contours",
            r"\bsmooth\s+cont[ou][ou]rs\b":          "smooth contours",
            r"\birregular\s+cont[ou][ou]rs\b":       "irregular contours",
            r"\blobuled\b":                          "lobulated",
            r"\blobul[ae]t[ae]d\b":                  "lobulated",
            r"\bsp[iy]cul[ae]t[ae]d\b":              "spiculated",
            r"\bwell[- ]?defined\b":                 "well-defined",
            r"\bill[- ]?defined\b":                  "ill-defined",
            r"\bpoorly[- ]?defined\b":               "poorly defined",
            r"\bround[ae]d\b":                       "rounded",
            r"\bav[ae]sc[ue]lar\b":                  "avascular",
            r"\bvascularity\b":                      "vascularity",
            r"\bhyperv[ae]sc[ue]lar\b":              "hypervascular",
            r"\bhypov[ae]sc[ue]lar\b":               "hypovascular",
            r"\binternal\s+echos?\b":                "internal echoes",
            r"\bpost[ae]rior\s+enhancement\b":       "posterior enhancement",
            r"\bpost[ae]rior\s+shadow[iy]ng\b":      "posterior shadowing",
            r"\bacoustic\s+shadow[iy]ng\b":          "acoustic shadowing",
            r"\bposterior\s+acoustic\b":             "posterior acoustic",
            r"\btwinkl[iy]ng\s+artifact\b":          "twinkling artifact",
            r"\bcomet[- ]?tail\b":                   "comet-tail",
            r"\bcalc[iy]f[iy]c[ae]tion\b":           "calcification",
            r"\bcalc[iy]f[iy]ed\b":                  "calcified",
            r"\bcalc[ue]lus\b":                      "calculus",
            r"\bcalc[ue]l[iy]\b":                    "calculi",
            r"\bliths[iy][ae]sis\b":                 "lithiasis",
            r"\bmicrolithiasis\b":                   "microlithiasis",
            r"\bshadow[iy]ng\s+calc[ue]lus\b":       "shadowing calculus",
            r"\bnon[- ]?shadow[iy]ng\b":             "non-shadowing",
            r"\bcystic\s+les[iy]on\b":               "cystic lesion",
            r"\bsolid\s+les[iy]on\b":                "solid lesion",
            r"\bmixed\s+les[iy]on\b":                "mixed lesion",
            r"\bf[oe]cal\s+les[iy]on\b":             "focal lesion",
            r"\bsp[ae]ce[- ]?occupying\b":           "space-occupying",
            r"\bcomplex\s+les[iy]on\b":              "complex lesion",
            r"\btarget\s+sign\b":                    "target sign",
            r"\bdouble\s+duct\s+sign\b":             "double duct sign",
            r"\bwhirlpool\s+sign\b":                 "whirlpool sign",
            r"\bsliding\s+sign\b":                   "sliding sign",
            r"\bcrescent\s+sign\b":                  "crescent sign",
            r"\bwall[- ]?echo[- ]?shadow\b":         "wall-echo-shadow",
            r"\bWES\s+sign\b":                       "WES sign",
            r"\bcentimeter[sz]?\b":                  "centimeters",
            r"\bmillimeter[sz]?\b":                  "millimeters",
            r"\bpar[ae]nch[iy]ma\b":                 "parenchyma",
            r"\bpar[ae]nch[iy]m[ae]tous\b":          "parenchymatous",
            r"\bsubcapsular\b":                      "subcapsular",
            r"\bsubph[ae]r[ae]nic\b":                "subphrenic",
            r"\binf[ae]rior\s+vena\b":               "inferior vena",
            r"\bfl[ou][id][id]\s+collection\b":      "fluid collection",
            r"\bper[iy]cholecystic\b":               "pericholecystic",
            r"\bn[oe]n[- ]?compressible\b":          "non-compressible",
            r"\bcompressible\b":                     "compressible",
            r"\btend[ae]rness\b":                    "tenderness",
            r"\bguarding\b":                         "guarding",
            r"\bright\s+iliac\s+fossa\b":            "right iliac fossa",
            r"\bMcBurn[ae]y[\'s]*\b":                "McBurney's",
            r"\bRIF\b":                              "RIF",
            r"\bclock\s+position\b":                 "clock position",
            r"\bq[ue]adrant\b":                      "quadrant",
            r"\bsuperi[oe]r\s+quadrant\b":           "superior quadrant",
            r"\binferi[oe]r\s+quadrant\b":           "inferior quadrant",

            # ══ DOPPLER ══
            r"\bDoppl[ae]r\b":                       "Doppler",
            r"\bcolour\s+Doppl[ae]r\b":              "colour Doppler",
            r"\bpower\s+Doppl[ae]r\b":               "power Doppler",
            r"\bspectral\s+Doppl[ae]r\b":            "spectral Doppler",
            r"\bres[iy]st[iy]ve\s+[iy]nd[ae]x\b":   "resistive index",
            r"\bRI\b":                               "RI",
            r"\bp[ue]ls[ae]t[iy]l[iy]ty\s+[iy]nd[ae]x\b": "pulsatility index",
            r"\bPI\b":                               "PI",
            r"\bend[- ]?d[iy][ae]stolic\b":          "end-diastolic",
            r"\bsystolic\b":                         "systolic",
            r"\bp[ae]ak\s+syst[oe]l[iy]c\b":         "peak systolic",
            r"\bwaveform\b":                         "waveform",
            r"\btr[iy]phas[iy]c\s+waveform\b":       "triphasic waveform",
            r"\bb[iy]phas[iy]c\s+waveform\b":        "biphasic waveform",
            r"\bmonophas[iy]c\s+waveform\b":         "monophasic waveform",
            r"\bv[ae]n[oe]us\s+flow\b":              "venous flow",
            r"\b[ae]rt[ae]r[iy][ae]l\s+flow\b":      "arterial flow",
            r"\bport[ae]l\s+flow\b":                 "portal flow",
            r"\brevers[ae]d\s+flow\b":               "reversed flow",
            r"\babn[oe]rm[ae]l\s+Doppl[ae]r\b":      "abnormal Doppler",
            r"\bnorm[ae]l\s+Doppl[ae]r\b":           "normal Doppler",
        }


    # ── Corrections DE (allemand) ────────────────────────────────────────
    CORRECTIONS_DE = {
            # ══ GALLENBLASE & GALLENWEGE ══
            r"\bGall[ae]nbl[ae]s[ae]\b":             "Gallenblase",
            r"\bGallenbl[ae]se\b":                   "Gallenblase",
            r"\bGallenst[ae]in[ae]?\b":              "Gallenstein",
            r"\bCholelithiasis[ae]?\b":              "Cholelithiasis",
            r"\bChol[ae]zystitis\b":                 "Cholezystitis",
            r"\bChol[ae]zist[iy]tis\b":              "Cholezystitis",
            r"\bChol[ae]doch[ou]s\b":                "Choledochus",
            r"\bChol[ae]docholithiasis\b":           "Choledocholithiasis",
            r"\bGall[ae]ngang\b":                    "Gallengang",
            r"\bGall[ae]ngangs[ae]rw[ae]it[ae]rung\b": "Gallengangserweiterung",
            r"\bintreh[ae]patisch[ae]\b":             "intrahepatisch",
            r"\bGall[ae]nbl[ae]senwand\b":           "Gallenblasenwand",
            r"\bGall[ae]nbl[ae]s[ae]nwand\s+v[ae]rd[iy]ckt\b": "Gallenblasenwand verdickt",
            r"\bPn[ae]umobilie\b":                   "Pneumobilie",
            r"\bHydrop[sz]\b":                       "Hydrops",
            r"\bEmp[iy]em\b":                        "Empyem",
            # ══ LEBER ══
            r"\bL[ae]b[ae]r\b":                      "Leber",
            r"\bH[ae]pat[oe]m[ae]galie\b":           "Hepatomegalie",
            r"\bZ[iy]rrh[oe]se\b":                   "Zirrhose",
            r"\bLeb[ae]rz[iy]rrh[oe]se\b":           "Leberzirrhose",
            r"\bSt[ae][ae]t[oe]se\b":                "Steatose",
            r"\bFettleb[ae]r\b":                     "Fettleber",
            r"\bH[ae]p[ae]tocellul[ae]r[ae]s\s+Karz[iy]nom\b": "Hepatozelluläres Karzinom",
            r"\bHCC\b":                              "HCC",
            r"\bH[ae]m[ae]ngi[oe]m\b":              "Hämangiom",
            r"\bFNH\b":                              "FNH",
            r"\bPfort[ae]derthrombose\b":            "Pfortaderthrombose",
            r"\bPfort[ae]der\b":                     "Pfortader",
            r"\bPfort[ae]derhoch[df]ruck\b":         "Pfortaderhochdruck",
            r"\bLeb[ae]rp[ae]r[ae]nchym\b":          "Leberparenchym",
            r"\bGliss[oe]nk[ae]ps[ae]l\b":           "Glisson-Kapsel",
            r"\bGlisson\b":                          "Glisson",
            # ══ PANKREAS ══
            r"\bP[ae]nkr[ae][ae]s\b":               "Pankreas",
            r"\bP[ae]nkr[ae][ae]titis\b":            "Pankreatitis",
            r"\bP[ae]nkr[ae][ae]sg[ae]ng\b":         "Pankreasgang",
            r"\bPseudozyste\b":                      "Pseudozyste",
            r"\bN[ae]krose\b":                       "Nekrose",
            r"\bP[ae]nkr[ae][ae]skopf\b":            "Pankreaskopf",
            r"\bP[ae]nkr[ae][ae]sk[oe]rp[ae]r\b":   "Pankreaskörper",
            r"\bP[ae]nkr[ae][ae]sschwanz\b":         "Pankreasschwanz",
            r"\bUnzin[ae]tusfortsatz\b":             "Processus uncinatus",
            r"\bPankr[ae][ae]skarz[iy]nom\b":        "Pankreaskarzinom",
            r"\bIPMN\b":                             "IPMN",
            r"\bP[ae]nkr[ae][ae]sdivisum\b":         "Pankreas divisum",
            # ══ MILZ ══
            r"\bM[iy]lz\b":                          "Milz",
            r"\bMilz[ae]r[ae]\b":                    "Milzäre",
            r"\bSpl[ae]n[oe]m[ae]g[ae]lie\b":        "Splenomegalie",
            r"\bMilzinf[ae]rkt\b":                   "Milzinfarkt",
            r"\bH[ae]pato[-\s]?Spl[ae]n[oe]m[ae]galie\b": "Hepatosplenomegalie",
            r"\bN[ae]b[ae]nm[iy]lz\b":               "Nebenmilz",
            # ══ NIEREN & HARNWEGE ══
            r"\bN[iy][ae]r[ae]n\b":                  "Nieren",
            r"\bN[iy]er[ae]nparenchym\b":            "Nierenparenchym",
            r"\bN[iy]er[ae]nrind[ae]\b":             "Nierenrinde",
            r"\bN[iy]er[ae]nmark\b":                 "Nierenmark",
            r"\bkortikomedull[ae]r[ae]\s+Diff[ae]r[ae]nz[iy][ae]rung\b": "kortikomedulläre Differenzierung",
            r"\bkortikomedull[ae]r[ae]\b":           "kortikomedulläre",
            r"\bHydron[ae]phrose\b":                 "Hydronephrose",
            r"\bH[ae]rnl[ae][iy]t[ae]r\b":           "Harnleiter",
            r"\bH[ae]rnbl[ae]se\b":                  "Harnblase",
            r"\bH[ae]rnbl[ae]s[ae]nwand\b":          "Harnblasenwand",
            r"\bN[iy][ae]r[ae]nbeck[ae]n\b":         "Nierenbecken",
            r"\bNephrolith[iy][ae]sis\b":            "Nephrolithiasis",
            r"\bN[iy][ae]r[ae]nstein\b":             "Nierenstein",
            r"\bHarnstein\b":                        "Harnstein",
            r"\bPyel[oe]n[ae]phritis\b":             "Pyelonephritis",
            r"\bN[iy][ae]r[ae]nzyste\b":             "Nierenzyste",
            r"\bBosniak\b":                          "Bosniak",
            r"\bAngioMyolipom\b":                    "Angiomyolipom",
            r"\bAngiomyolipom\b":                    "Angiomyolipom",
            r"\bN[iy]er[ae]nz[ae]llkarz[iy]nom\b":  "Nierenzellkarzinom",
            r"\bRCC\b":                              "RCC",
            r"\bN[iy][ae]r[ae]ntransplant[ae]t\b":  "Nierentransplantat",
            r"\bPolyzyStische\s+N[iy][ae]r[ae]n\b": "Polyzystische Nieren",
            r"\bPKD\b":                              "PKD",
            # ══ PROSTATA ══
            r"\bProst[ae]t[ae]\b":                   "Prostata",
            r"\bProst[ae]tahyp[ae]rplasie\b":        "Prostatahyperplasie",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPr[oe]st[ae]t[ae]karz[iy]nom\b":    "Prostatakarzinom",
            r"\bPI[-\s]?RADS\b":                     "PI-RADS",
            r"\bS[ae]m[iy]n[ae]lbl[ae]schen\b":      "Samenbläschen",
            r"\bP[ae]r[iy]ph[ae]r[ae]\s+Zone\b":     "periphere Zone",
            r"\bTrans[iy]t[iy][oe]ns[ae]zone\b":     "Transitionalzone",
            # ══ GYNÄKOLOGIE ══
            r"\bGeb[ae]rmutter\b":                   "Gebärmutter",
            r"\bUt[ae]rus\b":                        "Uterus",
            r"\bEndometr[iy]um\b":                   "Endometrium",
            r"\bMyometr[iy]um\b":                    "Myometrium",
            r"\bZerv[iy]x\b":                        "Zervix",
            r"\bEi[ae]rstock\b":                     "Eierstock",
            r"\bOvarium\b":                          "Ovarium",
            r"\bOvarialzyste\b":                     "Ovarialzyste",
            r"\bFollik[ae]l\b":                      "Follikel",
            r"\bCorpus\s+lut[ae]um\b":               "Corpus luteum",
            r"\bEileiter\b":                         "Eileiter",
            r"\bMy[oe]m\b":                          "Myom",
            r"\bEndometri[oe]se\b":                  "Endometriose",
            r"\bEndometri[oe]m\b":                   "Endometriom",
            r"\bfreie\s+Fl[üu]ssigkeit\b":           "freie Flüssigkeit",
            r"\bAszites\b":                          "Aszites",
            r"\bDouglas\b":                          "Douglas",
            # ══ SCHILDDRÜSE ══
            r"\bSchilddr[uü]se\b":                   "Schilddrüse",
            r"\bSchilddr[uü]senknoten\b":            "Schilddrüsenknoten",
            r"\bThyr[oe][iy]d[iy]tis\b":             "Thyroiditis",
            r"\bHashim[oe]t[oe]\b":                  "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bStr[ue]ma\b":                        "Struma",
            r"\bTI[-\s]?RADS\b":                     "TI-RADS",
            r"\bNeb[ae]nschilddr[uü]se\b":           "Nebenschilddrüse",
            r"\bP[ae]r[ae]thyr[oe][iy]d[ae][ae]\b":  "Nebenschilddrüse",
            r"\bHalslymphkn[oe]ten\b":               "Halslymphknoten",
            # ══ DESKRIPTOREN ══
            r"\ban[ae]ch[oe]gen\b":                  "anechogen",
            r"\bhy[pb][oe]echogen\b":                "hypoechogen",
            r"\bhy[pb][ae]rechogen\b":               "hyperechogen",
            r"\bis[oe]echogen\b":                    "isoechogen",
            r"\bechog[ae]n\b":                       "echogen",
            r"\bEch[oe]g[ae]n[iy]t[ae]t\b":          "Echogenität",
            r"\bhomog[ae]n\b":                       "homogen",
            r"\bh[ae]t[ae]r[oe]g[ae]n\b":            "heterogen",
            r"\bnormalgroß\b":                       "normalgroß",
        r"\bnormalgro[sß]{1,2}(?:e[sn]?)?\b":    "normalgroß",
        r"\bNormalgr[öo][sß]{1,2}(?:e[sn]?)?\b": "Normalgröße",
            r"\bNormal[gG]r[öo][sß][sß][ae]?\b":     "Normalgröße",
            r"\bvergr[öo][sß]+ert\b":              "vergrößert",
            r"\bregul[ae]r[ae]\b":                   "reguläre",
            r"\bglatt[ae]\b":                        "glatte",
            r"\bKalz[iy]f[iy]z[iy][ae]rung\b":       "Kalzifizierung",
            r"\bKalzinierung\b":                      "Kalzifikation",
            r"\bVerk[ae]lkung\b":                    "Verkalkung",
            r"\bzyStisch\b":                         "zystisch",
            r"\bsolide\b":                           "solide",
            r"\bKnoten\b":                           "Knoten",
            r"\bMass[ae]\b":                         "Masse",
            r"\bR[ae]undh[ae]rd\b":                  "Rundherd",
            r"\bH[ae]rd\b":                          "Herd",
            r"\bZyste\b":                            "Zyste",
            r"\bAbszess\b":                          "Abszess",
            r"\bLymphkn[öo]t[ae]n\b":               "Lymphknoten",
            r"\bfreie\s+Fl[uü]ssigkeit\b":           "freie Flüssigkeit",
            r"\bErg[ou]ss\b":                        "Erguss",
            r"\bDoppl[ae]r\b":                       "Doppler",
            r"\bWiders[ae]nd[sz][iy]ndex\b":          "Widerstandsindex",
            r"\bRI\b":                               "RI",
            r"\bFarb[-\s]?Doppl[ae]r\b":             "Farb-Doppler",
            r"\bPuls[ae]tions[iy]ndex\b":            "Pulsationsindex",
            r"\bVask[ue]l[ae]ris[ae]rung\b":          "Vaskularisierung",
            r"\bgef[ae][ae][sß][ae]rm[ae][sß]ig\b":   "gefäßmäßig",
            r"\bDurch[bB]lut[ue]ng\b":               "Durchblutung",
            r"\bPortovenös\b":                       "Portavenöser",
            r"\bPort[ae]l\b":                        "Portal",
            r"\bVen[ae]nkl[ae]pp[ae]n\b":            "Venenklappen",
            r"\bPerist[ae]ltik\b":                   "Peristaltik",
            r"\bAszites\b":                          "Aszites",
        }


    # ── Corrections ES (espagnol) ────────────────────────────────────────
    CORRECTIONS_ES = {
            # ══ VESÍCULA & VÍAS BILIARES ══
            r"\bves[iy]cula\s+biliar\b":              "vesícula biliar",
            r"\bvesicula\s+biliar\b":                 "vesícula biliar",
            r"\bcolecist[iy]tis\b":                   "colecistitis",
            r"\bcol[ae]doco\b":                       "colédoco",
            r"\bcol[ae]docolitiasis\b":               "coledocolitiasis",
            r"\bcol[ae]litiasis\b":                   "colelitiasis",
            r"\bl[iy]tiasis\s+vesicular\b":           "litiasis vesicular",
            r"\bv[iy][ae]s\s+biliar[ae]s\b":          "vías biliares",
            r"\bvias\s+biliares\b":                   "vías biliares",
            r"\bcolangitis\b":                        "colangitis",
            r"\bneumobilia\b":                        "neumobilia",
            r"\bsigno\s+de\s+murphy\b":               "signo de Murphy",
            r"\bMurphy\b":                            "Murphy",
            r"\bperic[oe]lecist[iy]co\b":             "pericolecístico",
            r"\bperic[oe]lecist[iy]ca\b":             "pericolecística",
            r"\bwes\b":                               "WES",
            # ══ HÍGADO ══
            r"\bhigado\b":                            "hígado",
            r"\bh[iy]gado\b":                         "hígado",
            r"\bhep[ae]tom[ae]galia\b":               "hepatomegalia",
            r"\bcirros[iy]s\b":                       "cirrosis",
            r"\bcirros[iy]s\s+hep[ae]tica\b":         "cirrosis hepática",
            r"\best[ae][ae]tos[iy]s\b":               "esteatosis",
            r"\bhigado\s+graso\b":                    "hígado graso",
            r"\bh[iy]gado\s+graso\b":                 "hígado graso",
            r"\bcarc[iy]noma\s+hep[ae]tocelular\b":   "carcinoma hepatocelular",
            r"\bCHC\b":                               "CHC",
            r"\bHCC\b":                               "HCC",
            r"\bm[ae]tastasis[ae]s?\s+hep[ae]ticas\b": "metástasis hepáticas",
            r"\bhip[ae]rpl[ae]sia\s+nodular\s+focal\b": "hiperplasia nodular focal",
            r"\bHNF\b":                               "HNF",
            r"\bh[ae]m[ae]ngioma\b":                  "hemangioma",
            r"\bqu[iy]ste\s+hep[ae]tico\b":           "quiste hepático",
            r"\bh[iy]datidico\b":                     "hidatídico",
            r"\bqu[iy]ste\s+h[iy]datidico\b":         "quiste hidatídico",
            r"\bh[iy]pertension\s+portal\b":          "hipertensión portal",
            r"\bh[iy]pertensión\s+portal\b":          "hipertensión portal",
            r"\btrombosis\s+portal\b":                "trombosis portal",
            r"\bvena\s+porta\b":                      "vena porta",
            r"\bpar[ae]nquima\s+hep[ae]tico\b":       "parénquima hepático",
            r"\belastografia\b":                      "elastografía",
            # ══ PÁNCREAS ══
            r"\bpancr[ae][ae]s\b":                    "páncreas",
            r"\bpancr[ae]atitis\b":                   "pancreatitis",
            r"\bpancr[ae][ae]s\s+aguda\b":            "pancreatitis aguda",
            r"\bpancr[ae][ae]s\s+cronica\b":          "pancreatitis crónica",
            r"\bpseudoqu[iy]ste\b":                   "pseudoquiste",
            r"\bWirsung\b":                           "Wirsung",
            r"\bIPMN\b":                              "IPMN",
            r"\bproceso\s+uncin[ae]do\b":             "proceso uncinado",
            r"\bcab[ae]za\s+del\s+pancr[ae][ae]s\b":  "cabeza del páncreas",
            r"\bcuerpo\s+del\s+pancr[ae][ae]s\b":     "cuerpo del páncreas",
            r"\bcola\s+del\s+pancr[ae][ae]s\b":       "cola del páncreas",
            # ══ BAZO ══
            r"\bbazo\b":                              "bazo",
            r"\besplenomegalia\b":                    "esplenomegalia",
            r"\binf[ae]rto\s+esp[lae]nico\b":         "infarto esplénico",
            r"\bbazo\s+acc[ae]sorio\b":               "bazo accesorio",
            r"\bhep[ae]to[-\s]?esplenomegalia\b":     "hepatoesplenomegalia",
            # ══ RIÑONES & VÍAS URINARIAS ══
            r"\brino[nt][ae]s?\b":                    "riñones",
            r"\brino[nt]\b":                          "riñón",
            r"\bpar[ae]nquima\s+r[ae]nal\b":          "parénquima renal",
            r"\bdi[ft][ae]r[ae]nciacion\s+cortico[- ]?medular\b": "diferenciación córtico-medular",
            r"\bd[iy]f[ae]r[ae]nciaci[oó]n\s+cortcomedular\b": "diferenciación corticomedular",
            r"\bhidron[ae]frosis\b":                  "hidronefrosis",
            r"\bureter\b":                            "uréter",
            r"\burneter\b":                           "uréter",
            r"\blitiasis\s+r[ae]nal\b":               "litiasis renal",
            r"\bn[ae]frolitiasis\b":                  "nefrolitiasis",
            r"\burolitiasis\b":                       "urolitiasis",
            r"\bpielo[- ]?n[ae]fritis\b":             "pielonefritis",
            r"\bqu[iy]ste\s+r[ae]nal\b":              "quiste renal",
            r"\bBosniak\b":                           "Bosniak",
            r"\bangiomiolipoma\b":                    "angiomiolipoma",
            r"\bcarc[iy]noma\s+r[ae]nal\b":           "carcinoma renal",
            r"\bv[ae]j[iy]ga\b":                      "vejiga",
            r"\bv[ae]j[iy]ga\s+urinaria\b":           "vejiga urinaria",
            r"\br[ae]siduo\s+postmiccional\b":        "residuo postmiccional",
            r"\bprost[ae]ta\b":                       "próstata",
            r"\bPSA\b":                               "PSA",
            r"\bBPH\b":                               "HBP",
            r"\bhip[ae]rpl[ae]sia\s+prost[ae]tica\b": "hiperplasia prostática",
            r"\bPI[-\s]?RADS\b":                      "PI-RADS",
            # ══ GINECOLOGÍA ══
            r"\but[ae]ro\b":                          "útero",
            r"\but[ae]ro\s+r[ae]trov[ae]rtido\b":     "útero retrovertido",
            r"\bendometr[iy]o\b":                     "endometrio",
            r"\bmiometr[iy]o\b":                      "miometrio",
            r"\bov[ae]rio[sz]?\b":                    "ovarios",
            r"\bov[ae]rico\b":                        "ovárico",
            r"\bqu[iy]ste\s+ov[ae]rico\b":            "quiste ovárico",
            r"\btuba\s+[ue]terina\b":                 "tuba uterina",
            r"\btrompa\s+[ue]terina\b":               "trompa uterina",
            r"\bmioma\b":                             "mioma",
            r"\bfibr[oe]ma\b":                        "fibroma",
            r"\bendometrios[iy]s\b":                  "endometriosis",
            r"\bendometri[oe]ma\b":                   "endometrioma",
            r"\bl[iy]quido\s+libre\b":                "líquido libre",
            r"\basc[iy]tis\b":                        "ascitis",
            r"\bDouglas\b":                           "Douglas",
            r"\btorsi[oó]n\b":                        "torsión",
            r"\bembarazo\s+ect[oó]p[iy]co\b":         "embarazo ectópico",
            r"\bgest[ae]c[iy][oó]n\b":               "gestación",
            r"\bpl[ae]c[ae]nta\b":                   "placenta",
            # ══ TIROIDES ══
            r"\btir[oe][iy]des\b":                    "tiroides",
            r"\bn[oó]dulo\s+tir[oe][iy]deo\b":        "nódulo tiroideo",
            r"\btir[oe][iy]d[iy]tis\b":               "tiroiditis",
            r"\bhashimoto\b":                         "Hashimoto",
            r"\bGraves\b":                            "Graves",
            r"\bbolio\b":                             "bocio",
            r"\bbocio\b":                             "bocio",
            r"\bTI[-\s]?RADS\b":                      "TI-RADS",
            r"\bp[ae]ratir[oe][iy]des\b":             "paratiroides",
            r"\bBI[-\s]?RADS\b":                      "BI-RADS",
            r"\bLI[-\s]?RADS\b":                      "LI-RADS",
            # ══ DESCRIPTORES ══
            r"\ban[ae]c[oe]ico\b":                    "anecoico",
            r"\bh[iy]po[ae]c[oe]ico\b":               "hipoecogénico",
            r"\bh[iy]p[ae]r[ae]c[oe]ico\b":           "hiperecogénico",
            r"\bis[oe][ae]c[oe]ico\b":                "isoecogénico",
            r"\bec[oe]g[ae]n[iy]co\b":               "ecogénico",
            r"\bec[oe]g[ae]n[iy]cidad\b":             "ecogenicidad",
            r"\bhomog[ae]n[ae][oe]\b":                "homogéneo",
            r"\bh[ae]t[ae]r[oe]g[ae]n[ae][oe]\b":    "heterogéneo",
            r"\btama[nñ]o\s+normal\b":                "tamaño normal",
            r"\bcontornos\s+regulares\b":             "contornos regulares",
            r"\bcontornos\s+irregulares\b":           "contornos irregulares",
            r"\bcalcificac[iy][oó]n\b":               "calcificación",
            r"\bcalc[ue]lo[sz]?\b":                   "cálculo",
            r"\blitiasis\b":                          "litiasis",
            r"\bmicrolitias[iy]s\b":                  "microlitias",
            r"\bles[iy][oó]n\s+focal\b":              "lesión focal",
            r"\bn[oó]dulo\b":                         "nódulo",
            r"\bquiste\b":                            "quiste",
            r"\babsceso\b":                           "absceso",
            r"\bcolec[cs]i[oó]n\s+l[iy]quida\b":     "colección líquida",
            r"\bDoppler\b":                           "Doppler",
            r"\b[iy]nd[iy]ce\s+de\s+r[ae]s[iy]st[ae]ncia\b": "índice de resistencia",
            r"\bIR\b":                                "IR",
            r"\bvascularizac[iy][oó]n\b":             "vascularización",
            r"\bl[iy]n[uy][ae]s\s+ganglionares\b":    "ganglios linfáticos",
            r"\bganglios\s+linfáticos\b":             "ganglios linfáticos",
            r"\basc[iy]tis\b":                        "ascitis",
            r"\bl[iy]quido\s+libre\b":                "líquido libre",
        }


    # ── Corrections IT (italien) ────────────────────────────────────────
    CORRECTIONS_IT = {
            # ══ COLECISTI & VIE BILIARI ══
            r"\bcist[if][ae]ll[ae]a\b":              "cistifellea",
            r"\bcist[if][ae]ll[ae]a\s+calc[oe]losa\b": "cistifellea calcolosa",
            r"\bcolecist[iy]te\b":                   "colecistite",
            r"\bcol[ae]doco\b":                      "coledoco",
            r"\bcol[ae]docolitiasi\b":               "coledocolitiasi",
            r"\bcolecistolitiasi\b":                 "colecistolitiasi",
            r"\bl[iy]tiasi\s+biliare\b":             "litiasi biliare",
            r"\bvie\s+biliari\b":                    "vie biliari",
            r"\bcolangiti[ce]\b":                    "colangite",
            r"\bPneumobilia\b":                      "Pneumobilia",
            r"\bpneumobilia\b":                      "pneumobilia",
            r"\bsegno\s+di\s+Murphy\b":              "segno di Murphy",
            r"\bMurphy\b":                           "Murphy",
            r"\bp[ae]ricolecistico\b":               "pericolecistico",
            # ══ FEGATO ══
            r"\bf[ae]g[ae]to\b":                     "fegato",
            r"\bep[ae]tom[ae]galia\b":               "epatom egalia",
            r"\bepatom[ae]galia\b":                  "epatomegalia",
            r"\bcirr[oe]si\b":                       "cirrosi",
            r"\bcirrosi\s+ep[ae]tica\b":             "cirrosi epatica",
            r"\bst[ae][ae]tosi\b":                   "steatosi",
            r"\bfegato\s+grasso\b":                  "fegato grasso",
            r"\bcarc[iy]noma\s+ep[ae]tocellulare\b": "carcinoma epatocellulare",
            r"\bHCC\b":                              "HCC",
            r"\bemangi[oe]ma\b":                     "emangioma",
            r"\bFNH\b":                              "FNH",
            r"\bip[ae]rpl[ae]sia\s+nodulare\s+focale\b": "iperplasia nodulare focale",
            r"\btrombo[sz]i\s+portale\b":            "trombosi portale",
            r"\bip[ae]rtensione\s+portale\b":        "ipertensione portale",
            r"\bvena\s+porta\b":                     "vena porta",
            r"\bpar[ae]nchima\s+ep[ae]tico\b":       "parenchima epatico",
            r"\belastografia\b":                     "elastografia",
            r"\bFibroScan\b":                        "FibroScan",
            # ══ PANCREAS ══
            r"\bpancr[ae][ae]s\b":                   "pancreas",
            r"\bpancr[ae]atite\b":                   "pancreatite",
            r"\bpancr[ae]atite\s+acuta\b":           "pancreatite acuta",
            r"\bpancr[ae]atite\s+cronica\b":         "pancreatite cronica",
            r"\bpseudociste\b":                      "pseudociste",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bprocesso\s+uncin[ae]to\b":           "processo uncinato",
            r"\bt[ae]sta\s+del\s+panc[ae]r[ae]s\b":  "testa del pancreas",
            r"\bcorp[oe]\s+del\s+panc[ae]r[ae]s\b":  "corpo del pancreas",
            r"\bcoda\s+del\s+panc[ae]r[ae]s\b":      "coda del pancreas",
            # ══ MILZA ══
            r"\bmilza\b":                            "milza",
            r"\bsp[lae]nom[ae]galia\b":              "splenomegalia",
            r"\binf[ae]rto\s+spl[ae]nico\b":         "infarto splenico",
            r"\bmilza\s+acc[ae]ssoria\b":            "milza accessoria",
            r"\bep[ae]tospl[ae]nom[ae]galia\b":      "epatosplenomegalia",
            # ══ RENI & VIE URINARIE ══
            r"\br[ae]n[ae]\b":                       "rene",
            r"\br[ae]ni\b":                          "reni",
            r"\bpar[ae]nchima\s+r[ae]nale\b":        "parenchima renale",
            r"\bdiff[ae]r[ae]nziazione\s+cortico[- ]?midollare\b": "differenziazione corticomidollare",
            r"\bdiff[ae]r[ae]nziazione\s+corticomed\b": "differenziazione corticomidollare",
            r"\bidr[oe]n[ae]frosi\b":                "idronefrosi",
            r"\bur[ae]t[ae]r[ae]\b":                 "uretere",
            r"\blitiasi\s+r[ae]nale\b":              "litiasi renale",
            r"\bn[ae]frolitiasi\b":                  "nefrolitiasi",
            r"\burolitiasi\b":                       "urolitiasi",
            r"\bpi[ae]lon[ae]frite\b":               "pielonefrite",
            r"\bciste\s+r[ae]nale\b":                "ciste renale",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomiolipoma\b":                   "angiomiolipoma",
            r"\bcarc[iy]noma\s+r[ae]nale\b":         "carcinoma renale",
            r"\bv[ae]scica\b":                       "vescica",
            r"\bv[ae]scica\s+urinaria\b":            "vescica urinaria",
            r"\bv[ae]ssica\b":                       "vescica",
            r"\bprost[ae]ta\b":                      "prostata",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bip[ae]rpl[ae]sia\s+prost[ae]tica\b": "iperplasia prostatica",
            r"\bPI[-\s]?RADS\b":                     "PI-RADS",
            # ══ GINECOLOGIA ══
            r"\but[ae]ro\b":                         "utero",
            r"\bendom[ae]trio\b":                    "endometrio",
            r"\bmi[oe]m[ae]trio\b":                  "miometrio",
            r"\bc[ae]rvic[ae]\b":                    "cervice",
            r"\bov[ae]io\b":                         "ovaio",
            r"\bov[ae]ric[oe]\b":                    "ovarico",
            r"\bcisti\s+ov[ae]rica\b":               "cisti ovarica",
            r"\btuba\s+ut[ae]rina\b":                "tuba uterina",
            r"\bmioma\b":                            "mioma",
            r"\blei[oe]mi[oe]ma\b":                  "leiomioma",
            r"\bendometri[oe]si\b":                  "endometriosi",
            r"\bendometri[oe]ma\b":                  "endometrioma",
            r"\basci[dt][ae]\b":                     "ascite",
            r"\bDouglas\b":                          "Douglas",
            r"\bt[oe]rsi[oe]ne\b":                   "torsione",
            r"\bgravidanza\s+ec[dt]opica\b":         "gravidanza ectopica",
            r"\bplacenta\b":                         "placenta",
            # ══ TIROIDE ══
            r"\btir[oe][iy]de\b":                    "tiroide",
            r"\bnodulo\s+tir[oe][iy]deo\b":          "nodulo tiroideo",
            r"\btir[oe][iy]d[iy]te\b":               "tiroidite",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bGozzO\b":                            "gozzo",
            r"\bgozzo\b":                            "gozzo",
            r"\bTI[-\s]?RADS\b":                     "TI-RADS",
            r"\bpar[ae]tir[oe][iy]d[ae]\b":          "paratiroide",
            r"\bBI[-\s]?RADS\b":                     "BI-RADS",
            r"\bLI[-\s]?RADS\b":                     "LI-RADS",
            # ══ DESCRITTORI ══
            r"\ban[ae]c[oe]geno\b":                  "anecogeno",
            r"\bip[oe]ec[oe]geno\b":                 "ipoecogeno",
            r"\bip[ae]rec[oe]geno\b":                "iperecogeno",
            r"\bis[oe]ec[oe]geno\b":                 "isoecogeno",
            r"\bec[oe]geno\b":                       "ecogeno",
            r"\bec[oe]genic[iy]tà\b":               "ecogenicità",
            r"\bom[oe]g[ae]n[ae][oe]\b":             "omogeneo",
            r"\bet[ae]r[oe]g[ae]n[ae][oe]\b":        "eterogeneo",
            r"\bdimensioni\s+normali\b":             "dimensioni normali",
            r"\bcontorni\s+regolari\b":              "contorni regolari",
            r"\bcontorni\s+irregolari\b":            "contorni irregolari",
            r"\bcalcificazione\b":                   "calcificazione",
            r"\bcalcol[oe]\b":                       "calcolo",
            r"\blitiasi\b":                          "litiasi",
            r"\bmicrolitias[i]\b":                   "microlitias",
            r"\bles[iy]one\s+focale\b":              "lesione focale",
            r"\bnodulo\b":                           "nodulo",
            r"\bciste\b":                            "ciste",
            r"\bascite\b":                           "ascite",
            r"\bvers[ae]mento\b":                    "versamento",
            r"\bers[ae]mento\b":                     "versamento",
            r"\brac[oe]lt[ae]\s+liquida\b":          "raccolta liquida",
            r"\bDoppl[ae]r\b":                       "Doppler",
            r"\b[iy]nd[iy]ce\s+di\s+r[ae]s[iy]st[ae]nza\b": "indice di resistenza",
            r"\bIR\b":                               "IR",
            r"\bvasc[oe]larizzazione\b":             "vascolarizzazione",
            r"\bl[iy]nfonodi\b":                     "linfonodi",
            r"\blinfonod[oe]\b":                     "linfonodo",
            r"\badenom[ae]galia\b":                  "adenomegalia",
        }


    # ── Corrections PT (portugais) ────────────────────────────────────────
    CORRECTIONS_PT = {
            # ══ VESÍCULA & VIAS BILIARES ══
            r"\bves[iy]cula\s+biliar\b":              "vesícula biliar",
            r"\bcolecist[iy]te\b":                   "colecistite",
            r"\bcol[ae]doco\b":                      "colédoco",
            r"\bcol[ae]docolit[iy][ae]se\b":         "coledocolitíase",
            r"\bcoleli[td]h[iy][ae]se\b":            "colelitiase",
            r"\blit[iy][ae]se\s+biliar\b":           "litiase biliar",
            r"\bvias\s+biliares\b":                  "vias biliares",
            r"\bcolangite\b":                        "colangite",
            r"\bpneumobilia\b":                      "pneumobilia",
            r"\bsinal\s+de\s+Murphy\b":              "sinal de Murphy",
            r"\bMurphy\b":                           "Murphy",
            r"\bpericol[ae]c[iy]stico\b":            "pericolecístico",
            # ══ FÍGADO ══
            r"\bf[iy]gado\b":                        "fígado",
            r"\bhep[ae]tom[ae]galia\b":              "hepatomegalia",
            r"\bcirrose\b":                          "cirrose",
            r"\bcirrose\s+hep[ae]tica\b":            "cirrose hepática",
            r"\best[ae][ae]tose\b":                  "esteatose",
            r"\bf[iy]gado\s+gorduroso\b":            "fígado gorduroso",
            r"\bcarc[iy]noma\s+hep[ae]tocelular\b":  "carcinoma hepatocelular",
            r"\bHCC\b":                              "HCC",
            r"\bh[ae]mangi[oe]ma\b":                 "hemangioma",
            r"\bFNH\b":                              "FNH",
            r"\bhip[ae]rpl[ae]sia\s+nodular\s+focal\b": "hiperplasia nodular focal",
            r"\btrombose\s+portal\b":                "trombose portal",
            r"\bhip[ae]rt[ae]nsão\s+portal\b":       "hipertensão portal",
            r"\bhipertensao\s+portal\b":             "hipertensão portal",
            r"\bveia\s+porta\b":                     "veia porta",
            r"\bpar[eê]nquima\s+hep[ae]tico\b":      "parênquima hepático",
            r"\belastografia\b":                     "elastografia",
            # ══ PÂNCREAS ══
            r"\bpancr[ae][ae]s\b":                   "pâncreas",
            r"\bpancr[ae]atite\b":                   "pancreatite",
            r"\bpancr[ae]atite\s+aguda\b":           "pancreatite aguda",
            r"\bpancr[ae]atite\s+cronica\b":         "pancreatite crónica",
            r"\bpseu[dt]ociste\b":                   "pseudocisto",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bprocesso\s+uncin[ae]do\b":           "processo uncinado",
            r"\bcab[ae][cç]a\s+do\s+p[aâ]ncr[ae][ae]s\b": "cabeça do pâncreas",
            r"\bcorp[oe]\s+do\s+p[aâ]ncr[ae][ae]s\b": "corpo do pâncreas",
            r"\bcauda\s+do\s+p[aâ]ncr[ae][ae]s\b":  "cauda do pâncreas",
            # ══ BAÇO ══
            r"\bba[cç]o\b":                          "baço",
            r"\besplen[oe]megalia\b":                "esplenomegalia",
            r"\binfart[oe]\s+espl[eê]nico\b":        "infarto esplénico",
            r"\bba[cç]o\s+ac[ae]ss[oó]rio\b":        "baço acessório",
            r"\bhep[ae]t[oe]splen[oe]megalia\b":     "hepatoesplenomegalia",
            # ══ RINS & VIAS URINÁRIAS ══
            r"\brins?\b":                            "rins",
            r"\bpar[eê]nquima\s+r[ae]nal\b":         "parênquima renal",
            r"\bdi[ft][ae]r[ae]n[cç]ia[cç]ão\s+[cC][oó]rtico[- ]?med[ue]lar\b": "diferenciação córtico-medular",
            r"\bdiferenciacao\s+cortcomedular\b":    "diferenciação corticomedular",
            r"\bhidron[ae]frose\b":                  "hidronefrose",
            r"\bur[ae]t[ae]r\b":                     "ureter",
            r"\blit[iy][ae]se\s+r[ae]nal\b":         "litiase renal",
            r"\bn[ae]froli[td]h[iy][ae]se\b":        "nefrolitíase",
            r"\buroli[dt]h[iy][ae]se\b":             "urolitíase",
            r"\bpi[ae]lon[ae]frite\b":               "pielonefrite",
            r"\bquisto\s+r[ae]nal\b":                "quisto renal",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomiolip[oe]ma\b":                "angiomiolipoma",
            r"\bcarc[iy]noma\s+r[ae]nal\b":          "carcinoma renal",
            r"\bbexiga\b":                           "bexiga",
            r"\bbexiga\s+urin[ae]ria\b":             "bexiga urinária",
            r"\bpr[oó]stata\b":                      "próstata",
            r"\bBPH\b":                              "HBP",
            r"\bPSA\b":                              "PSA",
            r"\bhip[ae]rpl[ae]sia\s+prost[ae]tica\b": "hiperplasia prostática",
            r"\bPI[-\s]?RADS\b":                     "PI-RADS",
            # ══ GINECOLOGIA ══
            r"\but[ae]ro\b":                         "útero",
            r"\bendm[ae][dt]rio\b":                  "endométrio",
            r"\bmiom[ae][dt]rio\b":                  "miométrio",
            r"\bcolo\s+do\s+ut[ae]ro\b":             "colo do útero",
            r"\bov[ae]rio[sz]?\b":                   "ovários",
            r"\bov[ae]rico\b":                       "ovárico",
            r"\bqu[iy]sto\s+ov[ae]rico\b":           "quisto ovárico",
            r"\btrompa\s+ut[ae]rina\b":              "trompa uterina",
            r"\bmi[oe]ma\b":                         "mioma",
            r"\bfibr[oe]ma\b":                       "fibroma",
            r"\bendometri[oe]se\b":                  "endometriose",
            r"\bendometri[oe]ma\b":                  "endometrioma",
            r"\basc[iy]te\b":                        "ascite",
            r"\bl[iy]quido\s+livre\b":               "líquido livre",
            r"\bDouglas\b":                          "Douglas",
            r"\bt[oe]rsão\b":                        "torção",
            r"\bgravidez\s+ec[dt][oó]pica\b":        "gravidez ectópica",
            r"\bplacenta\b":                         "placenta",
            # ══ TIRÓIDE ══
            r"\btir[oó][iy]de\b":                    "tiróide",
            r"\bn[oó]dulo\s+tir[oó][iy]deu\b":       "nódulo tiroideu",
            r"\btir[oó][iy]d[iy]te\b":               "tiroidite",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bb[oó]cio\b":                         "bócio",
            r"\bTI[-\s]?RADS\b":                     "TI-RADS",
            r"\bpar[ae]tir[oó][iy]de\b":             "paratiroide",
            r"\bBI[-\s]?RADS\b":                     "BI-RADS",
            r"\bLI[-\s]?RADS\b":                     "LI-RADS",
            # ══ DESCRITORES ══
            r"\ban[ae]c[oó]ico\b":                   "anecóico",
            r"\bip[oe]ec[oe]g[ae]nico\b":            "hipoecogénico",
            r"\bhip[ae]r[ae]c[oe]g[ae]nico\b":       "hiperecogénico",
            r"\bis[oe][ae]c[oe]ico\b":               "isoecogénico",
            r"\bec[oe]g[ae]nico\b":                  "ecogénico",
            r"\bec[oe]g[ae]nicidade\b":              "ecogenicidade",
            r"\bhomog[eê]n[ae][oe]\b":               "homogéneo",
            r"\bh[ae]t[ae]r[oe]g[eê]n[ae][oe]\b":   "heterogéneo",
            r"\bdimens[oõ][ae]s\s+normais\b":        "dimensões normais",
            r"\bcontornos\s+regulares\b":            "contornos regulares",
            r"\bcalcifica[cç]ão\b":                  "calcificação",
            r"\bc[aá]lculo[sz]?\b":                  "cálculo",
            r"\blit[iy][ae]se\b":                    "litiase",
            r"\bles[aã][oe]\s+focal\b":              "lesão focal",
            r"\bn[oó]dulo\b":                        "nódulo",
            r"\bquisto\b":                           "quisto",
            r"\bascite\b":                           "ascite",
            r"\bderrame\b":                          "derrame",
            r"\bcolec[cç]ão\s+l[iy]quida\b":         "colecção líquida",
            r"\bDoppler\b":                          "Doppler",
        r"\baltera[cç][oõ][ae]s\b":               "alterações",
        r"\bpancr[ae][ae]s\s+sem\b":             "pâncreas sem",
        r"\bpancr[ae][ae]s\s+com\b":             "pâncreas com",
        r"\bpancr[ae][ae]s\s+de\b":              "pâncreas de",
            r"\[iy]nd[iy]ce\s+de\s+r[ae]sist[eê]ncia\b": "índice de resistência",
            r"\bIR\b":                               "IR",
            r"\bvasc[ue]lariza[cç]ão\b":             "vascularização",
            r"\blinfonodo[sz]?\b":                   "linfonodos",
            r"\bg[aâ]nglio[sz]?\s+linf[ae]ticos\b":  "gânglios linfáticos",
        }


    # ── Corrections RU (russe) ────────────────────────────────────────────
    CORRECTIONS_RU = {
            # ══ ЖЕЛЧНЫЙ ПУЗЫРЬ И ЖЕЛЧНЫЕ ПУТИ ══
            r"\bжелчн[ыо][йе]\s+пузыр[ьь]\b":        "желчный пузырь",
            r"\bжелчного\s+пузыря\b":                 "желчного пузыря",
            r"\bжелчном\s+пузыре\b":                  "желчном пузыре",
            r"\bхолецисти[тт]\b":                     "холецистит",
            r"\bхоледо[хx]\b":                        "холедох",
            r"\bхоледохолитиаз\b":                    "холедохолитиаз",
            r"\bжелчнок[ам]менн[аы][яе]\s+болезн[ьи]\b": "желчнокаменная болезнь",
            r"\bЖКБ\b":                               "ЖКБ",
            r"\bколит\b":                             "колит",
            r"\bжелчны[ех]\s+проток[ао][хв]\b":       "желчных протоках",
            r"\bвнутрипечёночн[ыо][ех]\s+протоков\b": "внутрипечёночных протоков",
            r"\bпневмобилия\b":                       "пневмобилия",
            r"\bcимптом\s+Мёрфи\b":                   "симптом Мёрфи",
            r"\bМёрфи\b":                             "Мёрфи",
            r"\bMurphy\b":                            "Мёрфи",
            # ══ ПЕЧЕНЬ ══
            r"\bпечен[ьи]\b":                         "печень",
            r"\bпечени\b":                            "печени",
            r"\bгепатомег[ао]лия\b":                  "гепатомегалия",
            r"\bцирроз\b":                            "цирроз",
            r"\bцирроз[аe]\s+печени\b":               "цирроза печени",
            r"\bстеатоз\b":                           "стеатоз",
            r"\bжировой\s+гепатоз\b":                 "жировой гепатоз",
            r"\bгепатоцеллюлярная\s+карцинома\b":     "гепатоцеллюлярная карцинома",
            r"\bГЦК\b":                               "ГЦК",
            r"\bHCC\b":                               "ГЦК",
            r"\bгемангиома\b":                        "гемангиома",
            r"\bфокальная\s+нодулярная\s+гиперплазия\b": "фокальная нодулярная гиперплазия",
            r"\bФНГ\b":                               "ФНГ",
            r"\bFNH\b":                               "ФНГ",
            r"\bтромбоз\s+воротной\s+вены\b":         "тромбоз воротной вены",
            r"\bпортальная\s+гипертензия\b":          "портальная гипертензия",
            r"\bворотная\s+вена\b":                   "воротная вена",
            r"\bпаренхима\s+печени\b":                "паренхима печени",
            r"\bэластография\b":                      "эластография",
            # ══ ПОДЖЕЛУДОЧНАЯ ЖЕЛЕЗА ══
            r"\bподжелудочная\s+желез[аы]\b":         "поджелудочная железа",
            r"\bподжелудочной\s+железы\b":            "поджелудочной железы",
            r"\bпанкреатит\b":                        "панкреатит",
            r"\bострый\s+панкреатит\b":               "острый панкреатит",
            r"\bхронический\s+панкреатит\b":          "хронический панкреатит",
            r"\bпсевдокиста\b":                       "псевдокиста",
            r"\bвирсунг[ао]в\s+проток\b":             "вирсунгов проток",
            r"\bIPMN\b":                              "ИПМН",
            r"\bголовка\s+поджелудочной\b":           "головка поджелудочной",
            r"\bтело\s+поджелудочной\b":              "тело поджелудочной",
            r"\bхвост\s+поджелудочной\b":             "хвост поджелудочной",
            r"\bкрючковидный\s+отросток\b":           "крючковидный отросток",
            r"\bаденокарцинома\s+поджелудочной\b":    "аденокарцинома поджелудочной",
            # ══ СЕЛЕЗЁНКА ══
            r"\bселез[её]нк[аи]\b":                   "селезёнка",
            r"\bселез[её]нки\b":                      "селезёнки",
            r"\bспленомегалия\b":                     "спленомегалия",
            r"\bинфаркт\s+селез[её]нки\b":            "инфаркт селезёнки",
            r"\bдобавочная\s+селез[её]нка\b":         "добавочная селезёнка",
            r"\bгепатоспленомегалия\b":               "гепатоспленомегалия",
            # ══ ПОЧКИ И МОЧЕВЫЕ ПУТИ ══
            r"\bпочк[аи]\b":                          "почки",
            r"\bпочки\b":                             "почки",
            r"\bпаренхима\s+почек\b":                 "паренхима почек",
            r"\bкорково[- ]?мозговая\s+дифференциация\b": "корково-мозговая дифференциация",
            r"\bкортикомедуллярная\s+дифференциация\b": "кортикомедуллярная дифференциация",
            r"\bгидронефроз\b":                       "гидронефроз",
            r"\bмочеточник\b":                        "мочеточник",
            r"\bмочевой\s+пузырь\b":                  "мочевой пузырь",
            r"\bнефролитиаз\b":                       "нефролитиаз",
            r"\bмочекаменная\s+болезнь\b":            "мочекаменная болезнь",
            r"\bМКБ\b":                               "МКБ",
            r"\bпиелонефрит\b":                       "пиелонефрит",
            r"\bкиста\s+почки\b":                     "киста почки",
            r"\bBosniak\b":                           "Бозняк",
            r"\bангиомиолипома\b":                    "ангиомиолипома",
            r"\bпочечноклеточная\s+карцинома\b":      "почечноклеточная карцинома",
            r"\bПКР\b":                               "ПКР",
            r"\bRCC\b":                               "ПКР",
            r"\bтрансплантат\s+почки\b":              "трансплантат почки",
            r"\bполикистозная\s+болезнь\b":           "поликистозная болезнь",
            r"\bПКБ\b":                               "ПКБ",
            # ══ ПРЕДСТАТЕЛЬНАЯ ЖЕЛЕЗА ══
            r"\bпредстательная\s+желез[аы]\b":        "предстательная железа",
            r"\bпростата\b":                          "предстательная железа",
            r"\bДГПЖ\b":                              "ДГПЖ",
            r"\bBPH\b":                               "ДГПЖ",
            r"\bПСА\b":                               "ПСА",
            r"\bPSA\b":                               "ПСА",
            r"\bPI[-\s]?RADS\b":                      "PI-RADS",
            # ══ ГИНЕКОЛОГИЯ ══
            r"\bматк[аи]\b":                          "матка",
            r"\bэндометри[йи]\b":                     "эндометрий",
            r"\bмиометри[йи]\b":                      "миометрий",
            r"\bшейка\s+матки\b":                     "шейка матки",
            r"\bяичник[аи]\b":                        "яичники",
            r"\bкиста\s+яичника\b":                   "киста яичника",
            r"\bмиома\s+матки\b":                     "миома матки",
            r"\bэндометриоз\b":                       "эндометриоз",
            r"\bэндометриома\b":                      "эндометриома",
            r"\bасцит\b":                             "асцит",
            r"\bсвободная\s+жидкость\b":              "свободная жидкость",
            r"\bДуглас[аов]*\b":                      "Дугласа",
            r"\bторсия\s+яичника\b":                  "торсия яичника",
            r"\bвнематочная\s+беременность\b":        "внематочная беременность",
            r"\bплацента\b":                          "плацента",
            # ══ ЩИТОВИДНАЯ ЖЕЛЕЗА ══
            r"\bщитовидная\s+желез[аы]\b":            "щитовидная железа",
            r"\bузел\s+щитовидной\b":                 "узел щитовидной",
            r"\bтиреоидит\b":                         "тиреоидит",
            r"\bХашимото\b":                          "Хашимото",
            r"\bHashimoto\b":                         "Хашимото",
            r"\bГрейвс\b":                            "Грейвс",
            r"\bGraves\b":                            "Грейвс",
            r"\bзоб\b":                               "зоб",
            r"\bTI[-\s]?RADS\b":                      "TI-RADS",
            r"\bBI[-\s]?RADS\b":                      "BI-RADS",
            r"\bLI[-\s]?RADS\b":                      "LI-RADS",
            r"\bпаращитовидные\s+железы\b":           "паращитовидные железы",
            # ══ ДЕСКРИПТОРЫ ══
            r"\bанэхогенн[ыо][йе]\b":                "анэхогенный",
            r"\bгипоэхогенн[ыо][йе]\b":              "гипоэхогенный",
            r"\bгиперэхогенн[ыо][йе]\b":             "гиперэхогенный",
            r"\bизоэхогенн[ыо][йе]\b":               "изоэхогенный",
            r"\bэхогенн[ыо][йе]\b":                  "эхогенный",
            r"\bэхогенность\b":                       "эхогенность",
            r"\bоднородн[ыо][йе]\b":                  "однородный",
            r"\bнеоднородн[ыо][йе]\b":               "неоднородный",
            r"\bнормальн[ыо][йе]\s+размер\b":         "нормальный размер",
            r"\bнормальных\s+размеров\b":             "нормальных размеров",
            r"\bровн[ыо][йе]\s+контур[ыа]\b":        "ровные контуры",
            r"\bкальцинат[ыа]\b":                     "кальцинаты",
            r"\bкальцификация\b":                     "кальцификация",
            r"\bкальк[ую]л[яе]\b":                   "конкремент",
            r"\bконкремент[ыа]\b":                    "конкременты",
            r"\bлитиаз\b":                            "литиаз",
            r"\bфокальное\s+образование\b":           "фокальное образование",
            r"\bузел[ьь]\b":                          "узел",
            r"\bкиста\b":                             "киста",
            r"\bабсцесс\b":                           "абсцесс",
            r"\bсвободная\s+жидкость\b":              "свободная жидкость",
            r"\bвыпот\b":                             "выпот",
            r"\bжидкостное\s+скопление\b":            "жидкостное скопление",
            r"\bДопплер\b":                           "допплер",
            r"\bдопплерография\b":                    "допплерография",
            r"\bиндекс\s+резистентности\b":           "индекс резистентности",
            r"\bИР\b":                                "ИР",
            r"\bваскуляризация\b":                    "васкуляризация",
            r"\bлимфатические\s+узлы\b":              "лимфатические узлы",
            r"\bлимфоузлы\b":                         "лимфоузлы",
            r"\bасцит\b":                             "асцит",
            r"\bперистальтика\b":                     "перистальтика",
        }


    # ── Corrections RO (roumain) ─────────────────────────────────────────
    CORRECTIONS_RO = {
        # ══ VEZICĂ URINARĂ — variantes phonétiques Whisper ══════════════════
        # "vezică urinare" → "vezică urinară"  (confusion -re/-ră finale)
        r"\bvezic[aă]\s+urinar[eă]\b":              "vezică urinară",
        r"\bvezic[aă]\s+urinar[aă]\b":              "vezică urinară",
        r"\bvezic[aă]\s+urinar\b":                  "vezică urinară",
        r"\bvezica\s+urinare\b":                    "vezică urinară",
        r"\bvesica\s+urinara\b":                    "vezică urinară",
        r"\bvesic[aă]\s+urinar[aă]\b":              "vezică urinară",

        # ══ SEMI-REPLEȚIE — patterns phonétiques Whisper ═══════════════════
        r"\bsemi[- ]?reple[tț][iy][ae][tț][aă]\b":         "semi-replețiată",
        r"\bsemi[- ]?reple[tț]i[ei]\b":                    "semi-repleție",
        r"\bsemi[- ]?reple[tț]i[aă]\b":                    "semi-replețiată",
        r"\bsemi[- ]?reple[tț]ie\b":                       "semi-repleție",
        r"\bsemi[- ]?repl[ae][tț][iy]\b":                  "semi-repleție",
        r"\bsemireple[tț]i[ei]\b":                         "semi-repleție",
        r"\bsemi[- ]?re\s+ple[tț][ae]c\b":                "semi-repleție",
        r"\bsemi[- ]?re\s+plet[ae]c\b":                   "semi-repleție",
        r"\bsemire\s+ple[tț][ae]c\b":                     "semi-repleție",
        r"\bsemire\s+plet[ae]c\b":                        "semi-repleție",
        r"\bsemi\s+repletic\b":                           "semi-repleție",
        r"\bsemi\s+reple[tț]ic\b":                        "semi-repleție",
        r"\bsemi\s+repletie\b":                           "semi-repleție",
        r"\bsemi\s+reple[tț]ie\b":                        "semi-repleție",
        r"\b[iî]n\s+semi[- ]?reple[tț]\w*\b":            "în semi-repleție",
        r"\bsemi[- ]?repl\w{2,8}\b":                      "semi-repleție",

        # ══ CONȚINUT ANECOIC — patterns phonétiques Whisper ════════════════
        r"\bcontinuat[ae][aă]\s+necoj[ae]n[aăe]\b":       "conținut anecoic",
        r"\bcontinuit[aă]\s+necogen[aă]\b":               "conținut anecoic",
        r"\bconjin[uă]t\s+an[ae]coic\b":                  "conținut anecoic",
        r"\bcon[tț]in[uă]t\s+an[ae]coic[aă]?\b":          "conținut anecoic",
        r"\bcon[tț]inut\s+an[ae]coic[aă]?\b":             "conținut anecoic",
        r"\bconjinu[tț]\s+an[ae]coic\b":                  "conținut anecoic",
        r"\bcon[tț]in[uă]t\s+aneco[iy]c\b":               "conținut anecoic",
        r"\bnecoj[ae]n[aăe]\b":                            "anecoic",
        r"\bnecogen[aă]\b":                                "anecoic",
        r"\bnecogen\b":                                    "anecoic",
        r"\bnecog[ae]n[aă]\b":                             "anecoic",
        r"\bn[ae]coj[ae]n\b":                              "anecoic",

        # ══ CONȚINUT — corrections générales ═══════════════════════════════
        r"\bcon[tț]inut\b":                               "conținut",
        r"\bconjin[uă]t\b":                               "conținut",
        r"\bconjinu[tț]\b":                               "conținut",
        r"\bcontinuat[ae][aă]\b":                          "conținut",
        r"\bcontinuata\b":                                 "conținut",
        r"\bcontinuat\b":                                  "conținut",

        # ══ ANECOIC / ECOGENITATE ════════════════════════════════════════════
        r"\ban[ae]coic[aă]\b":                             "anecoică",
        r"\banecoic\b":                                    "anecoic",
        r"\ban[ae]co[iy]c[aă]\b":                          "anecoică",
        r"\bhipoecoic[aă]\b":                              "hipoecoică",
        r"\bhiperecoic[aă]\b":                             "hiperecoică",
        r"\bhipoecoic\b":                                  "hipoecoic",
        r"\bhiperecoic\b":                                 "hiperecoic",
        r"\becogenitate\b":                                "ecogenitate",
        r"\becogenitat[ae]\b":                             "ecogenitate",

        # ══ ORGANE ═══════════════════════════════════════════════════════════
        r"\bvezic[aă]\s+biliar[aă]\b":              "vezică biliară",
        r"\bcolecistit[aă]\b":                       "colecistită",
        r"\bpancreas\b":                             "pancreas",
        r"\bficat\b":                                "ficat",
        r"\bspl[iî]n[aă]\b":                         "splină",
        r"\brinichi\b":                              "rinichi",
        r"\briniuchi\b":                             "rinichi",
        r"\buter\b":                                 "uter",
        r"\bprostat[aă]\b":                          "prostată",
        r"\bprostat[ae]\b":                          "prostată",

        # ══ DIFERENȚIERE ════════════════════════════════════════════════════
        r"\bdiferen[tț]i[eé]rea\s+cortico[- ]?medullar[aă]\b": "diferențierea cortico-medulară",
        r"\bdiferen[tț]iere\s+cortico[- ]?medullar[aă]\b":     "diferențiere cortico-medulară",
        r"\bparenchim\b":                            "parenchim",
        r"\bparenchimal[aă]\b":                      "parenchimatoasă",
        r"\bparenchimul\b":                          "parenchimul",

        # ══ IMAGISTICĂ ══════════════════════════════════════════════════════
        r"\becografie\b":                            "ecografie",
        r"\becografic[aă]\b":                        "ecografică",
        r"\becografic\b":                            "ecografic",
        r"\bRMN\b":                                  "RMN",
        r"\becho?grafie\b":                          "ecografie",

        # ══ PATOLOGIE ═══════════════════════════════════════════════════════
        r"\befuziune\b":                             "efuziune",
        r"\befuziun[ei]\s+intra[- ]?articular[eaă]\b": "efuziune intraarticulară",
        r"\bascit[aă]\b":                            "ascită",
        r"\bhidronefroz[aă]\b":                      "hidronefroză",
        r"\bhepatom[ae]galie\b":                     "hepatomegalie",
        r"\bsplenomegalie\b":                        "splenomegalie",
        r"\bliti[aă]z[aă]\b":                        "litiază",
        r"\bcalcul\b":                               "calcul",
        r"\bchist\b":                                "chist",
        r"\bnodul\b":                                "nodul",
        r"\bsinovit[aă]\b":                          "sinovită",
        r"\bburs[aă]\b":                             "bursă",
        r"\bligament\b":                             "ligament",
        r"\btendon\b":                               "tendon",
        r"\bmenisc\b":                               "menisc",
        r"\bcartilaj\b":                             "cartilaj",
        r"\bcolec[tț]ie\b":                          "colecție",
        r"\bcolec[tț]ie\s+lichidian[aă]\b":         "colecție lichidiană",
        r"\blichidian[aă]\b":                        "lichidiană",

        # ══ ARTICULAȚIE / RECES ═════════════════════════════════════════════
        r"\brecesu[l]?\s+sub[- ]?quadriceps\b":     "recesul subcvadricipital",
        r"\brecesu[l]?\s+subcvadricipital\b":       "recesul subcvadricipital",
        r"\brecesu[l]?\s+subquadricepitar\b":       "recesul subcvadricipital",
        r"\brecesu[l]?\s+suprapatelar\b":           "recesul suprapatelar",
        r"\bintra[- ]?articular[aă]\b":              "intraarticulară",
        r"\bîntr[- ]?articular[aă]\b":               "intraarticulară",

        # ══ DESCRIPȚIE ECOGRAFICĂ ════════════════════════════════════════════
        r"\bomogen[aă]\b":                           "omogenă",
        r"\bomogen\b":                               "omogen",
        r"\beterogen[aă]\b":                         "eterogenă",
        r"\beterogen\b":                             "eterogen",
        r"\bnormal[aă]\b":                           "normală",
        r"\bnormal\b":                               "normal",
        r"\bregulat[eaă]\b":                         "regulate",
        r"\bvizibil[eaă]\b":                         "vizibilă",
        r"\bvizibil\b":                              "vizibil",
        r"\bfuziun[ei]\b":                           "fuziuni",
        r"\bfuzioni\b":                              "fuziuni",
        r"\banomalii\b":                             "anomalii",
        r"\banomalii\s+p[aă]r[tț]i\s+moi\b":      "anomalii ale părților moi",
        r"\bp[aă]r[tț]i\s+moi\b":                  "părți moi",
        r"\bfar[aă]\s+anomalii\b":                  "fără anomalii",
        r"\b[iî]n\s+special\b":                     "în special",
        r"\bausen[tț][aă]\b":                        "absența",
        r"\babsen[tț][aă]\b":                        "absența",
        r"\bRecesiune\b":                            "reces",
        r"\bdilatat[aă]\b":                          "dilatată",
        r"\bdilatat\b":                              "dilatat",
        r"\bîngroșat[aă]\b":                         "îngroșată",
        r"\bîngroș[ae]t\b":                          "îngroșat",
        r"\bperete[l]?\b":                           "perete",
        r"\bpere[tț]i\b":                            "pereți",
        r"\bperete[l]?\s+îngroș[ae]t\b":            "perete îngroșat",

        # ══ HALLUCINATIONS PHONÉTIQUES CONNUES DE WHISPER (RO) ══════════════
        r"\bfara\s+anomali\b":                      "fără anomalii",
        r"\bmorley\b":                               "morbi",
        r"\barsium\b":                               "artrită",
        r"\binspecial\b":                            "în special",
        r"\bfuzioni\b":                              "fuziuni",
        r"\brar\s+3\b":                             "RAR",
        r"\bversiun\b":                              "versiune",
        r"\bmorli\b":                                "",   # bruit — supprimer
        r"\bneculari\b":                             "neregularități",
    }


    CORRECTIONS_ZH = {
            # ══ 胆囊与胆道 ══
            r"\b胆囊\b":                              "胆囊",
            r"\b胆囊炎\b":                            "胆囊炎",
            r"\b急性胆囊炎\b":                        "急性胆囊炎",
            r"\b慢性胆囊炎\b":                        "慢性胆囊炎",
            r"\b胆石症\b":                            "胆石症",
            r"\b胆总管\b":                            "胆总管",
            r"\b胆总管结石\b":                        "胆总管结石",
            r"\b胆管扩张\b":                          "胆管扩张",
            r"\b肝内胆管\b":                          "肝内胆管",
            r"\b胆管炎\b":                            "胆管炎",
            r"\bMurphy征\b":                          "Murphy征",
            r"\b胆囊壁增厚\b":                        "胆囊壁增厚",
            r"\b胆汁淤积\b":                          "胆汁淤积",
            r"\b气体胆道症\b":                        "气体胆道症",
            # ══ 肝脏 ══
            r"\b肝脏\b":                              "肝脏",
            r"\b肝大\b":                              "肝大",
            r"\b肝硬化\b":                            "肝硬化",
            r"\b脂肪肝\b":                            "脂肪肝",
            r"\b肝细胞癌\b":                          "肝细胞癌",
            r"\bHCC\b":                               "HCC",
            r"\b肝血管瘤\b":                          "肝血管瘤",
            r"\b局灶性结节增生\b":                    "局灶性结节增生",
            r"\bFNH\b":                               "FNH",
            r"\b门脉高压\b":                          "门脉高压",
            r"\b门静脉血栓\b":                        "门静脉血栓",
            r"\b门静脉\b":                            "门静脉",
            r"\b肝实质\b":                            "肝实质",
            r"\b弹性成像\b":                          "弹性成像",
            r"\b肝囊肿\b":                            "肝囊肿",
            r"\b棘球蚴囊肿\b":                        "棘球蚴囊肿",
            r"\b腹水\b":                              "腹水",
            # ══ 胰腺 ══
            r"\b胰腺\b":                              "胰腺",
            r"\b急性胰腺炎\b":                        "急性胰腺炎",
            r"\b慢性胰腺炎\b":                        "慢性胰腺炎",
            r"\b胰腺假性囊肿\b":                      "胰腺假性囊肿",
            r"\b主胰管\b":                            "主胰管",
            r"\bIPMN\b":                              "IPMN",
            r"\b胰腺腺癌\b":                          "胰腺腺癌",
            r"\b胰头\b":                              "胰头",
            r"\b胰体\b":                              "胰体",
            r"\b胰尾\b":                              "胰尾",
            r"\b钩突\b":                              "钩突",
            # ══ 脾脏 ══
            r"\b脾脏\b":                              "脾脏",
            r"\b脾大\b":                              "脾大",
            r"\b副脾\b":                              "副脾",
            r"\b肝脾大\b":                            "肝脾大",
            r"\b脾梗死\b":                            "脾梗死",
            # ══ 肾脏与泌尿系统 ══
            r"\b肾脏\b":                              "肾脏",
            r"\b肾实质\b":                            "肾实质",
            r"\b皮髓质分化\b":                        "皮髓质分化",
            r"\b肾积水\b":                            "肾积水",
            r"\b输尿管\b":                            "输尿管",
            r"\b肾结石\b":                            "肾结石",
            r"\b肾盂肾炎\b":                          "肾盂肾炎",
            r"\b肾囊肿\b":                            "肾囊肿",
            r"\bBosniak\b":                           "Bosniak",
            r"\b血管平滑肌脂肪瘤\b":                  "血管平滑肌脂肪瘤",
            r"\b肾细胞癌\b":                          "肾细胞癌",
            r"\b膀胱\b":                              "膀胱",
            r"\b前列腺\b":                            "前列腺",
            r"\b良性前列腺增生\b":                    "良性前列腺增生",
            r"\bPSA\b":                               "PSA",
            r"\bPI-RADS\b":                           "PI-RADS",
            # ══ 妇科 ══
            r"\b子宫\b":                              "子宫",
            r"\b子宫内膜\b":                          "子宫内膜",
            r"\b子宫肌层\b":                          "子宫肌层",
            r"\b卵巢\b":                              "卵巢",
            r"\b卵巢囊肿\b":                          "卵巢囊肿",
            r"\b子宫肌瘤\b":                          "子宫肌瘤",
            r"\b子宫内膜异位症\b":                    "子宫内膜异位症",
            r"\b道格拉斯腔\b":                        "道格拉斯腔",
            r"\b异位妊娠\b":                          "异位妊娠",
            r"\b胎盘\b":                              "胎盘",
            # ══ 甲状腺 ══
            r"\b甲状腺\b":                            "甲状腺",
            r"\b甲状腺结节\b":                        "甲状腺结节",
            r"\b甲状腺炎\b":                          "甲状腺炎",
            r"\b桥本病\b":                            "桥本病",
            r"\bTI-RADS\b":                           "TI-RADS",
            r"\b甲状旁腺\b":                          "甲状旁腺",
            r"\bBI-RADS\b":                           "BI-RADS",
            r"\bLI-RADS\b":                           "LI-RADS",
            # ══ 描述词 ══
            r"\b无回声\b":                            "无回声",
            r"\b低回声\b":                            "低回声",
            r"\b高回声\b":                            "高回声",
            r"\b等回声\b":                            "等回声",
            r"\b均匀\b":                              "均匀",
            r"\b不均匀\b":                            "不均匀",
            r"\b正常大小\b":                          "正常大小",
            r"\b轮廓规则\b":                          "轮廓规则",
            r"\b钙化\b":                              "钙化",
            r"\b结石\b":                              "结石",
            r"\b焦点病变\b":                          "焦点病变",
            r"\b结节\b":                              "结节",
            r"\b囊肿\b":                              "囊肿",
            r"\b积液\b":                              "积液",
            r"\b液性暗区\b":                          "液性暗区",
            r"\b彩色多普勒\b":                        "彩色多普勒",
            r"\b阻力指数\b":                          "阻力指数",
            r"\bRI\b":                                "RI",
            r"\b血流信号\b":                          "血流信号",
            r"\b淋巴结\b":                            "淋巴结",
            r"\b腹膜腔\b":                            "腹膜腔",
            r"\b游离液体\b":                          "游离液体",
        }

    CORRECTIONS_JA = {
            # ══ 胆嚢・胆道 ══
            r"\b胆嚢\b":                              "胆嚢",
            r"\b胆嚢炎\b":                            "胆嚢炎",
            r"\b急性胆嚢炎\b":                        "急性胆嚢炎",
            r"\b慢性胆嚢炎\b":                        "慢性胆嚢炎",
            r"\b胆石症\b":                            "胆石症",
            r"\b総胆管\b":                            "総胆管",
            r"\b総胆管結石\b":                        "総胆管結石",
            r"\b胆管拡張\b":                          "胆管拡張",
            r"\b肝内胆管\b":                          "肝内胆管",
            r"\b胆管炎\b":                            "胆管炎",
            r"\bMurphy徴候\b":                        "Murphy徴候",
            r"\b胆嚢壁肥厚\b":                       "胆嚢壁肥厚",
            r"\bPneumobilia\b":                       "胆道内気腫",
            # ══ 肝臓 ══
            r"\b肝臓\b":                              "肝臓",
            r"\b肝腫大\b":                            "肝腫大",
            r"\b肝硬変\b":                            "肝硬変",
            r"\b脂肪肝\b":                            "脂肪肝",
            r"\b肝細胞癌\b":                          "肝細胞癌",
            r"\bHCC\b":                               "HCC",
            r"\b肝血管腫\b":                          "肝血管腫",
            r"\b限局性結節性過形成\b":                "限局性結節性過形成",
            r"\bFNH\b":                               "FNH",
            r"\b門脈圧亢進\b":                        "門脈圧亢進",
            r"\b門脈血栓\b":                          "門脈血栓",
            r"\b門脈\b":                              "門脈",
            r"\b肝実質\b":                            "肝実質",
            r"\b弾性波\b":                            "弾性波",
            r"\b腹水\b":                              "腹水",
            # ══ 膵臓 ══
            r"\b膵臓\b":                              "膵臓",
            r"\b急性膵炎\b":                          "急性膵炎",
            r"\b慢性膵炎\b":                          "慢性膵炎",
            r"\b膵仮性嚢胞\b":                        "膵仮性嚢胞",
            r"\b膵管\b":                              "膵管",
            r"\bIPMN\b":                              "IPMN",
            r"\b膵頭部\b":                            "膵頭部",
            r"\b膵体部\b":                            "膵体部",
            r"\b膵尾部\b":                            "膵尾部",
            r"\b鉤状突起\b":                          "鉤状突起",
            # ══ 脾臓 ══
            r"\b脾臓\b":                              "脾臓",
            r"\b脾腫\b":                              "脾腫",
            r"\b副脾\b":                              "副脾",
            r"\b肝脾腫\b":                            "肝脾腫",
            # ══ 腎臓・尿路 ══
            r"\b腎臓\b":                              "腎臓",
            r"\b腎実質\b":                            "腎実質",
            r"\b皮髄境界\b":                          "皮髄境界",
            r"\b水腎症\b":                            "水腎症",
            r"\b尿管\b":                              "尿管",
            r"\b腎結石\b":                            "腎結石",
            r"\b腎盂腎炎\b":                          "腎盂腎炎",
            r"\b腎嚢胞\b":                            "腎嚢胞",
            r"\bBosniak\b":                           "Bosniak",
            r"\b血管筋脂肪腫\b":                      "血管筋脂肪腫",
            r"\b腎細胞癌\b":                          "腎細胞癌",
            r"\b膀胱\b":                              "膀胱",
            r"\b前立腺\b":                            "前立腺",
            r"\b良性前立腺肥大\b":                    "良性前立腺肥大",
            r"\bPSA\b":                               "PSA",
            r"\bPI-RADS\b":                           "PI-RADS",
            # ══ 婦人科 ══
            r"\b子宮\b":                              "子宮",
            r"\b子宮内膜\b":                          "子宮内膜",
            r"\b子宮筋層\b":                          "子宮筋層",
            r"\b卵巣\b":                              "卵巣",
            r"\b卵巣嚢胞\b":                          "卵巣嚢胞",
            r"\b子宮筋腫\b":                          "子宮筋腫",
            r"\b子宮内膜症\b":                        "子宮内膜症",
            r"\bダグラス窩\b":                        "ダグラス窩",
            r"\b子宮外妊娠\b":                        "子宮外妊娠",
            r"\b胎盤\b":                              "胎盤",
            # ══ 甲状腺 ══
            r"\b甲状腺\b":                            "甲状腺",
            r"\b甲状腺結節\b":                        "甲状腺結節",
            r"\b甲状腺炎\b":                          "甲状腺炎",
            r"\b橋本病\b":                            "橋本病",
            r"\bTI-RADS\b":                           "TI-RADS",
            r"\bBI-RADS\b":                           "BI-RADS",
            r"\bLI-RADS\b":                           "LI-RADS",
            # ══ 記述子 ══
            r"\b無エコー\b":                          "無エコー",
            r"\b低エコー\b":                          "低エコー",
            r"\b高エコー\b":                          "高エコー",
            r"\b等エコー\b":                          "等エコー",
            r"\b均一\b":                              "均一",
            r"\b不均一\b":                            "不均一",
            r"\b正常サイズ\b":                        "正常サイズ",
            r"\b石灰化\b":                            "石灰化",
            r"\b結石\b":                              "結石",
            r"\b焦点病変\b":                          "焦点病変",
            r"\b嚢胞\b":                              "嚢胞",
            r"\b腹水\b":                              "腹水",
            r"\b液体貯留\b":                          "液体貯留",
            r"\bカラードプラ\b":                      "カラードプラ",
            r"\b抵抗指数\b":                          "抵抗指数",
            r"\bRI\b":                                "RI",
            r"\b血流信号\b":                          "血流信号",
            r"\bリンパ節\b":                          "リンパ節",
            r"\b遊離液体\b":                          "遊離液体",
        }

    CORRECTIONS_KO = {
            # ══ 담낭 및 담도 ══
            r"\b담낭\b":                              "담낭",
            r"\b담낭염\b":                            "담낭염",
            r"\b급성\s*담낭염\b":                     "급성 담낭염",
            r"\b만성\s*담낭염\b":                     "만성 담낭염",
            r"\b담석증\b":                            "담석증",
            r"\b총담관\b":                            "총담관",
            r"\b총담관결석\b":                        "총담관결석",
            r"\b담관확장\b":                          "담관확장",
            r"\b간내담관\b":                          "간내담관",
            r"\b담관염\b":                            "담관염",
            r"\bMurphy징후\b":                        "Murphy징후",
            r"\b담낭벽비후\b":                        "담낭벽비후",
            # ══ 간 ══
            r"\b간\b":                                "간",
            r"\b간비대\b":                            "간비대",
            r"\b간경변\b":                            "간경변",
            r"\b지방간\b":                            "지방간",
            r"\b간세포암\b":                          "간세포암",
            r"\bHCC\b":                               "HCC",
            r"\b간혈관종\b":                          "간혈관종",
            r"\b국소결절과증식\b":                    "국소결절과증식",
            r"\bFNH\b":                               "FNH",
            r"\b문맥압항진증\b":                      "문맥압항진증",
            r"\b문맥혈전증\b":                        "문맥혈전증",
            r"\b문맥\b":                              "문맥",
            r"\b간실질\b":                            "간실질",
            r"\b복수\b":                              "복수",
            # ══ 췌장 ══
            r"\b췌장\b":                              "췌장",
            r"\b급성췌장염\b":                        "급성췌장염",
            r"\b만성췌장염\b":                        "만성췌장염",
            r"\b가성낭종\b":                          "가성낭종",
            r"\b췌관\b":                              "췌관",
            r"\bIPMN\b":                              "IPMN",
            r"\b췌두부\b":                            "췌두부",
            r"\b췌체부\b":                            "췌체부",
            r"\b췌미부\b":                            "췌미부",
            # ══ 비장 ══
            r"\b비장\b":                              "비장",
            r"\b비비대\b":                            "비비대",
            r"\b부비장\b":                            "부비장",
            r"\b간비비대\b":                          "간비비대",
            # ══ 신장 및 요로 ══
            r"\b신장\b":                              "신장",
            r"\b신실질\b":                            "신실질",
            r"\b피질수질분화\b":                      "피질수질분화",
            r"\b수신증\b":                            "수신증",
            r"\b요관\b":                              "요관",
            r"\b신결석\b":                            "신결석",
            r"\b신우신염\b":                          "신우신염",
            r"\b신낭종\b":                            "신낭종",
            r"\bBosniak\b":                           "Bosniak",
            r"\b혈관근지방종\b":                      "혈관근지방종",
            r"\b신세포암\b":                          "신세포암",
            r"\b방광\b":                              "방광",
            r"\b전립선\b":                            "전립선",
            r"\b전립선비대증\b":                      "전립선비대증",
            r"\bPSA\b":                               "PSA",
            r"\bPI-RADS\b":                           "PI-RADS",
            # ══ 산부인과 ══
            r"\b자궁\b":                              "자궁",
            r"\b자궁내막\b":                          "자궁내막",
            r"\b자궁근층\b":                          "자궁근층",
            r"\b난소\b":                              "난소",
            r"\b난소낭종\b":                          "난소낭종",
            r"\b자궁근종\b":                          "자궁근종",
            r"\b자궁내막증\b":                        "자궁내막증",
            r"\b더글러스와\b":                        "더글러스와",
            r"\b자궁외임신\b":                        "자궁외임신",
            r"\b태반\b":                              "태반",
            # ══ 갑상선 ══
            r"\b갑상선\b":                            "갑상선",
            r"\b갑상선결절\b":                        "갑상선결절",
            r"\b갑상선염\b":                          "갑상선염",
            r"\b하시모토병\b":                        "하시모토병",
            r"\bTI-RADS\b":                           "TI-RADS",
            r"\bBI-RADS\b":                           "BI-RADS",
            r"\bLI-RADS\b":                           "LI-RADS",
            # ══ 설명자 ══
            r"\b무에코\b":                            "무에코",
            r"\b저에코\b":                            "저에코",
            r"\b고에코\b":                            "고에코",
            r"\b등에코\b":                            "등에코",
            r"\b균질\b":                              "균질",
            r"\b불균질\b":                            "불균질",
            r"\b정상크기\b":                          "정상 크기",
            r"\b석회화\b":                            "석회화",
            r"\b결석\b":                              "결석",
            r"\b낭종\b":                              "낭종",
            r"\b복수\b":                              "복수",
            r"\b액체저류\b":                          "액체저류",
            r"\b컬러도플러\b":                        "컬러 도플러",
            r"\b저항지수\b":                          "저항지수",
            r"\bRI\b":                                "RI",
            r"\b림프절\b":                            "림프절",
            r"\b유리액체\b":                          "유리 액체",
        }

    CORRECTIONS_TR = {
            # ══ SAFRA KESESİ & SAFRA YOLLARI ══
            r"\bsafra\s+kesesi\b":                   "safra kesesi",
            r"\bkolesi[sz]tit\b":                    "kolesistit",
            r"\bakut\s+kolesi[sz]tit\b":             "akut kolesistit",
            r"\bkronik\s+kolesi[sz]tit\b":           "kronik kolesistit",
            r"\bkol[ae]litias[iy]s\b":               "kolelitiasis",
            r"\bkoled[oe]k\b":                       "koledok",
            r"\bkoled[oe]k[oe]litias[iy]s\b":        "koledokolitiasis",
            r"\bsafra\s+kanal[iy]\b":                "safra kanalı",
            r"\bsafra\s+yollar[iy]\b":               "safra yolları",
            r"\bkolanjiit\b":                        "kolanjit",
            r"\bpn[öo]mobili\b":                     "pnömobili",
            r"\bMurphy\s+belirtisi\b":               "Murphy belirtisi",
            r"\bMurphy\b":                           "Murphy",
            r"\bperikol[ae]sistik\b":                "perikolesistik",
            # ══ KARACİĞER ══
            r"\bkaraci[gğ][ae]r\b":                  "karaciğer",
            r"\bhep[ae]tom[ae]gali\b":               "hepatomegali",
            r"\bsi[rr]oz\b":                         "siroz",
            r"\bk[ae]r[ae]ci[gğ][ae]r\s+si[rr]ozu\b": "karaciğer sirozu",
            r"\bst[ae][ae]toz\b":                    "steatoz",
            r"\by[ae][gğ]li\s+kar[ae]ci[gğ][ae]r\b": "yağlı karaciğer",
            r"\bhep[ae]tos[ae]ll[üu]ler\s+kars[iy]nom\b": "hepatosellüler karsinom",
            r"\bHCC\b":                              "HCC",
            r"\bh[ae]m[ae]njiom\b":                  "hemanjiyom",
            r"\bFNH\b":                              "FNH",
            r"\bp[oe]rtal\s+h[iy]pert[ae]nsiyon\b":  "portal hipertansiyon",
            r"\bp[oe]rtal\s+v[ae]n\s+trombozu\b":    "portal ven trombozu",
            r"\bp[oe]rtal\s+v[ae]n\b":               "portal ven",
            r"\bkar[ae]ci[gğ][ae]r\s+par[ae]nkim\b":  "karaciğer parankimi",
            r"\belastografi\b":                      "elastografi",
            r"\basit\b":                             "asit",
            # ══ PANKREAS ══
            r"\bpankre[ae]s\b":                      "pankreas",
            r"\bpankre[ae]tit\b":                    "pankreatit",
            r"\bakut\s+pankre[ae]tit\b":             "akut pankreatit",
            r"\bkronik\s+pankre[ae]tit\b":           "kronik pankreatit",
            r"\bps[öo][yd][oe]kist\b":               "psödokist",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bpankre[ae]s\s+ba[sş][iy]\b":         "pankreas başı",
            r"\bpankre[ae]s\s+g[öo]vd[ae]si\b":      "pankreas gövdesi",
            r"\bpankre[ae]s\s+kuyru[gğ]u\b":         "pankreas kuyruğu",
            r"\bunsinat\s+[çc][iy]kıntı\b":          "unsinat çıkıntı",
            # ══ DALAK ══
            r"\bdalak\b":                            "dalak",
            r"\bsp[aè]lenomeg[ae]li\b":              "splenomegali",
            r"\bek[sz]tr[ae]\s+dalak\b":             "ekstra dalak",
            r"\bhep[ae]tosplen[oe]meg[ae]li\b":      "hepatosplenomegali",
            # ══ BÖBREKLER ══
            r"\bb[öo]brek\b":                        "böbrek",
            r"\bb[öo]brek\s+par[ae]nkim\b":          "böbrek parankimi",
            r"\bkortikomedull[ae]r\b":               "kortikomedüller",
            r"\bhidr[oe]n[ae]froz\b":                "hidronefroz",
            r"\b[üu]r[ae]t[ae]r\b":                  "üreter",
            r"\bn[ae]frolitas[iy][ae]z\b":            "nefrolitiyaz",
            r"\bp[iy][ae]lon[ae]frit\b":              "pyelonefrit",
            r"\bb[öo]brek\s+kisti\b":                "böbrek kisti",
            r"\bBosniak\b":                          "Bosniak",
            r"\banjiomiyolipom\b":                   "anjiyomiyolipom",
            r"\bb[öo]brek\s+h[üu]creli\s+kars[iy]nom\b": "böbrek hücreli karsinom",
            r"\bmes[ae]ne\b":                        "mesane",
            r"\bprostat\b":                          "prostat",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GİNEKOLOJİ ══
            r"\buter[üu]s\b":                        "uterus",
            r"\bend[oe]metr[iy]um\b":                "endometrium",
            r"\bmiy[oe]metr[iy]um\b":                "miyometrium",
            r"\bover\b":                             "over",
            r"\bover\s+kisti\b":                     "over kisti",
            r"\bmiy[oe]m\b":                         "miyom",
            r"\bend[oe]metriy[oe]z\b":               "endometriozis",
            r"\bend[oe]metri[oe]m\b":                "endometrioma",
            r"\bserbest\s+s[iy]v[iy]\b":             "serbest sıvı",
            r"\basit\b":                             "asit",
            r"\bDouglas\b":                          "Douglas",
            r"\bekt[oe]pik\s+gebelik\b":             "ektopik gebelik",
            r"\bplasen[dt]a\b":                      "plasenta",
            # ══ TİROİD ══
            r"\btir[oe][iy]d\b":                     "tiroid",
            r"\btir[oe][iy]d\s+nod[üu]l[üu]\b":      "tiroid nodülü",
            r"\btir[oe][iy]dit\b":                   "tiroidit",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bguatr\b":                            "guatr",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ TANIMLAYICILAR ══
            r"\ban[ae]k[oe][iy]k\b":                 "anekoik",
            r"\bh[iy]p[oe][ae]k[oe][iy]k\b":         "hipoekoik",
            r"\bh[iy]p[ae]r[ae]k[oe][iy]k\b":        "hiperekoik",
            r"\b[iy]z[oe][ae]k[oe][iy]k\b":          "izoekoik",
            r"\bek[oe]jenik\b":                      "ekojenite",
            r"\bh[oe]m[oe]jen\b":                    "homojen",
            r"\bhet[ae]r[oe]jen\b":                  "heterojen",
            r"\bn[oe]rmal\s+b[üu]y[üu]kl[üu]k\b":   "normal büyüklük",
            r"\bd[üu]zenli\s+kontur\b":              "düzenli kontur",
            r"\bkals[iy]fikasy[oe]n\b":              "kalsifikasyon",
            r"\bt[ae][sş]\b":                        "taş",
            r"\blitas[iy][ae]z\b":                   "litiazis",
            r"\bfokal\s+lezyon\b":                   "fokal lezyon",
            r"\bnod[üu]l\b":                         "nodül",
            r"\bkist\b":                             "kist",
            r"\basit\b":                             "asit",
            r"\bs[iy]v[iy]\s+koleksiyon\b":           "sıvı koleksiyonu",
            r"\bDoppler\b":                          "Doppler",
            r"\bdirenç\s+[iy]ndeks[iy]\b":           "direnç indeksi",
            r"\bRI\b":                               "RI",
            r"\bvaskülarizasyon\b":                  "vaskülarizasyon",
            r"\blenf\s+nodu\b":                      "lenf nodu",
            r"\bserbest\s+s[iy]v[iy]\b":             "serbest sıvı",
        }

    CORRECTIONS_NL = {
            # ══ GALBLAAS & GALWEGEN ══
            r"\bgalblaas\b":                         "galblaas",
            r"\bcholecystitis\b":                    "cholecystitis",
            r"\bacute\s+cholecystitis\b":            "acute cholecystitis",
            r"\bchronische\s+cholecystitis\b":       "chronische cholecystitis",
            r"\bgalsteenziekte\b":                   "galsteenziekte",
            r"\bgalsteen[en]*\b":                    "galstenen",
            r"\bcholedoch[ue]s\b":                   "choledochus",
            r"\bcholedocholithiasis\b":              "choledocholithiasis",
            r"\bgalweg[en]*\b":                      "galwegen",
            r"\bcholangitis\b":                      "cholangitis",
            r"\bpneumobilie\b":                      "pneumobilie",
            r"\bMurphy[\'s]*\s*teken\b":             "teken van Murphy",
            r"\bpericholecystitis\b":                "pericholecystitis",
            r"\bgalblaaswand\s+verdikt\b":           "galblaaswand verdikt",
            # ══ LEVER ══
            r"\blev[ae]r\b":                         "lever",
            r"\bhepatomeg[ae]lie\b":                 "hepatomegalie",
            r"\bcirrose\b":                          "cirrose",
            r"\blevercirrose\b":                     "levercirrose",
            r"\bst[ae]atose\b":                      "steatose",
            r"\bvette\s+lever\b":                    "vette lever",
            r"\bhep[ae]tocellul[ae]ir\s+carcinoom\b": "hepatocellulair carcinoom",
            r"\bHCC\b":                              "HCC",
            r"\bh[ae]m[ae]ngioom\b":                 "hemangioom",
            r"\bFNH\b":                              "FNH",
            r"\bportale\s+hypert[ae]nsie\b":         "portale hypertensie",
            r"\bpoortader\b":                        "poortader",
            r"\bleverparenchym\b":                   "leverparenchym",
            r"\belastografie\b":                     "elastografie",
            r"\bascites\b":                          "ascites",
            # ══ PANCREAS ══
            r"\bpancr[ae][ae]s\b":                   "pancreas",
            r"\bpancreatitis\b":                     "pancreatitis",
            r"\bacute\s+pancreatitis\b":             "acute pancreatitis",
            r"\bchronische\s+pancreatitis\b":        "chronische pancreatitis",
            r"\bpseudocyste\b":                      "pseudocyste",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bpancreash[oo]fd\b":                  "pancreashoofd",
            r"\bpancre[ae]slichaam\b":               "pancreaslichaam",
            r"\bpancre[ae]sstaart\b":                "pancreasstaart",
            r"\buncin[ae]t[ae]\s+proc[ae]s\b":       "uncinaat proces",
            # ══ MILT ══
            r"\bmilt\b":                             "milt",
            r"\bspl[ae]nom[ae]galie\b":              "splenomegalie",
            r"\bextramilt\b":                        "bijmilt",
            r"\bhep[ae]tospl[ae]nom[ae]galie\b":     "hepatosplenomegalie",
            # ══ NIEREN & URINEWEGEN ══
            r"\bnier[en]*\b":                        "nieren",
            r"\bnierparenchym\b":                    "nierparenchym",
            r"\bcortical[e]?\s+med[ue]ll[ae]ire\b":  "corticomedullair",
            r"\bhydronefrose\b":                     "hydronefrose",
            r"\bur[ae]t[ae]r[s]?\b":                 "ureter",
            r"\bnefrol[iy]thiasis\b":                "nefrolithiasis",
            r"\bpyelonefritis\b":                    "pyelonefritis",
            r"\bniercyste\b":                        "niercyste",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomyolipoom\b":                   "angiomyolipoom",
            r"\bniercelcarcinoom\b":                 "niercelcarcinoom",
            r"\bblaas\b":                            "blaas",
            r"\bprost[ae]a?\b":                      "prostaat",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GYNAECOLOGIE ══
            r"\but[ae]r[ue]s\b":                     "uterus",
            r"\bend[oe]m[ae]trium\b":                "endometrium",
            r"\bmy[oe]m[ae]trium\b":                 "myometrium",
            r"\beier[sz]tok[ken]*\b":                "eierstokken",
            r"\beier[sz]tokcyste\b":                 "eiersttokcyste",
            r"\bmy[oe]om\b":                         "myoom",
            r"\bendometri[oe]se\b":                  "endometriose",
            r"\bdouglas\b":                          "Douglas",
            r"\bect[oe]pische\s+zwangerschap\b":     "ectopische zwangerschap",
            r"\bplacenta\b":                         "placenta",
            # ══ SCHILDKLIER ══
            r"\bschildklier\b":                      "schildklier",
            r"\bschildkliernodus\b":                 "schildkliernodus",
            r"\bthyreoiditis\b":                     "thyreoiditis",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bstruma\b":                           "struma",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESCRIPTOREN ══
            r"\ban[ae]chog[ae]en\b":                 "anechogeen",
            r"\bhyp[oe]echog[ae]en\b":               "hypoechogeen",
            r"\bhyp[ae]rechog[ae]en\b":              "hyperechogeen",
            r"\bis[oe]echog[ae]en\b":                "isoechogeen",
            r"\bechog[ae]en\b":                      "echogeen",
            r"\bhom[oe]g[ae]en\b":                   "homogeen",
            r"\bh[ae]t[ae]r[oe]g[ae]en\b":           "heterogeen",
            r"\bnormale\s+grootte\b":                "normale grootte",
            r"\bregelmatige\s+contouren\b":          "regelmatige contouren",
            r"\bkalk\b":                             "kalk",
            r"\bcalcificatie\b":                     "calcificatie",
            r"\bsteen[en]*\b":                       "stenen",
            r"\bfocale\s+laesie\b":                  "focale laesie",
            r"\bnodul[ae]\b":                        "nodule",
            r"\bcyste\b":                            "cyste",
            r"\bascites\b":                          "ascites",
            r"\bvloeistofcollectie\b":               "vloeistofcollectie",
            r"\bDoppler\b":                          "Doppler",
            r"\bweerstandsindex\b":                  "weerstandsindex",
            r"\bRI\b":                               "RI",
            r"\bvascularisatie\b":                   "vascularisatie",
            r"\blymfklier[en]*\b":                   "lymfklieren",
            r"\bvrije\s+vloeistof\b":                "vrije vloeistof",
        }

    CORRECTIONS_SV = {
            # ══ GALLBLÅSA & GALLVÄGAR ══
            r"\bgallbl[åa]sa\b":                     "gallblåsa",
            r"\bkole[sz]ystit\b":                    "kolecystit",
            r"\bakut\s+kole[sz]ystit\b":             "akut kolecystit",
            r"\bkronisk\s+kole[sz]ystit\b":          "kronisk kolecystit",
            r"\bkolelitiasis\b":                     "kolelitiasis",
            r"\bgallsten[ar]*\b":                    "gallsten",
            r"\bkol[ae]dok[ue]s\b":                  "koledokus",
            r"\bkol[ae]d[oe]kolitias[iy]s\b":        "koledokolitiasis",
            r"\bgallv[äa]gar\b":                     "gallvägar",
            r"\bkolangit\b":                         "kolangit",
            r"\bpn[äe]umobilia\b":                   "pneumobilia",
            r"\bMurphy[\'s]*\s+tecken\b":            "Murphys tecken",
            r"\bMurphy\b":                           "Murphy",
            # ══ LEVER ══
            r"\blever\b":                            "lever",
            r"\bhep[ae]tom[ae]gali\b":               "hepatomegali",
            r"\bcirros\b":                           "cirros",
            r"\blevercirros\b":                      "levercirros",
            r"\bst[ae][ae]tos\b":                    "steatos",
            r"\bfettlever\b":                        "fettlever",
            r"\bhep[ae]tocellul[äe]rt\s+karcinom\b": "hepatocellulärt karcinom",
            r"\bHCC\b":                              "HCC",
            r"\bh[äe]mangiom\b":                     "hemangiom",
            r"\bFNH\b":                              "FNH",
            r"\bportal\s+hypert[ae]nsion\b":         "portal hypertension",
            r"\bportav[ae]n\b":                      "portaven",
            r"\bleverparenkym\b":                    "leverparenkym",
            r"\belastografi\b":                      "elastografi",
            r"\bascites\b":                          "ascites",
            # ══ PANKREAS ══
            r"\bpankre[ae]s\b":                      "pankreas",
            r"\bpankre[ae]tit\b":                    "pankreatit",
            r"\bakut\s+pankre[ae]tit\b":             "akut pankreatit",
            r"\bkronisk\s+pankre[ae]tit\b":          "kronisk pankreatit",
            r"\bpseud[oe]cysta\b":                   "pseudocysta",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bpankre[ae]sh[äe]vud\b":              "pankreashuvud",
            r"\bpankre[ae]skropp\b":                 "pankreaskropp",
            r"\bpankre[ae]ssvans\b":                 "pankreassvans",
            # ══ MJÄLTE ══
            r"\bmj[äa]lte\b":                        "mjälte",
            r"\bspl[ae]nom[ae]gali\b":               "splenomegali",
            r"\bextramj[äa]lte\b":                   "extra mjälte",
            r"\bhep[ae]tospl[ae]nom[ae]gali\b":       "hepatosplenomegali",
            # ══ NJURAR & URINVÄGAR ══
            r"\bnjure[n]?\b":                        "njure",
            r"\bnjurpar[ae]nkym\b":                  "njurparenkym",
            r"\bkortiko[- ]?medull[äe]r\b":          "kortikomedullärt",
            r"\bhydron[ae]fros\b":                   "hydronefros",
            r"\bur[ae]t[äe]r\b":                     "uretär",
            r"\bn[ae]frolitias[iy]s\b":              "nefrolitiasis",
            r"\bpyel[oe]n[ae]frit\b":                "pyelonefrit",
            r"\bnjurcysta\b":                        "njurcysta",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomyolipom\b":                    "angiomyolipom",
            r"\bnjurcellskarcinom\b":                "njurcellskarcinom",
            r"\burbl[åa]sa\b":                       "urinblåsa",
            r"\bprost[ae]ta\b":                      "prostata",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GYNEKOLOGI ══
            r"\blivmodern?\b":                       "livmodern",
            r"\bend[oe]m[ae]trium\b":                "endometrium",
            r"\bmyom[ae]trium\b":                    "myometrium",
            r"\b[äa]ggstock[ar]*\b":                 "äggstockar",
            r"\b[äa]ggstockscysta\b":                "äggstockscysta",
            r"\bmyom\b":                             "myom",
            r"\bend[oe]m[ae]tri[oe]s\b":             "endometrios",
            r"\bDouglasfick[ae]n\b":                 "Douglasfickan",
            r"\bekt[oe]pisk\s+graviditet\b":         "ektopisk graviditet",
            r"\bplacenta\b":                         "placenta",
            # ══ SKÖLDKÖRTEL ══
            r"\bsk[öo]ldk[öo]rtl[ae]n\b":            "sköldkörteln",
            r"\bsk[öo]ldk[öo]rtel\s+nod[ue]l\b":     "sköldkörtelnodule",
            r"\btyr[oe][iy]deit\b":                  "tyreoideit",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bstruma\b":                           "struma",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESKRIPTORER ══
            r"\ban[ae]k[oe]gen\b":                   "anekogen",
            r"\bhyp[oe][ae]k[oe]gen\b":              "hypoekogen",
            r"\bhyp[ae]r[ae]k[oe]gen\b":             "hyperekogen",
            r"\bis[oe][ae]k[oe]gen\b":               "isoekogen",
            r"\b[ae]k[oe]gen\b":                     "ekogen",
            r"\bhom[oe]gen\b":                       "homogen",
            r"\bh[ae]t[ae]r[oe]gen\b":               "heterogen",
            r"\bnormal\s+stor[ae]k\b":               "normal storlek",
            r"\bregelbundna\s+konturer\b":           "regelbundna konturer",
            r"\bkalk\b":                             "kalk",
            r"\bkalkifiering\b":                     "kalkifiering",
            r"\bsten[ar]*\b":                        "sten",
            r"\bfokal\s+lesion\b":                   "fokal lesion",
            r"\bnod[ue]l\b":                         "nodule",
            r"\bcysta\b":                            "cysta",
            r"\bascites\b":                          "ascites",
            r"\bv[äa]tskesamling\b":                 "vätskeansamling",
            r"\bDoppler\b":                          "Doppler",
            r"\bmotståndsindex\b":                   "motståndsindex",
            r"\bRI\b":                               "RI",
            r"\bvaskularisering\b":                  "vaskularisering",
            r"\blymfknutor\b":                       "lymfknutor",
            r"\bfri\s+v[äa]tska\b":                  "fri vätska",
        }

    CORRECTIONS_NO = {
            # ══ GALLEBLÆRE & GALLEVEIER ══
            r"\bgalleb[lL][æe]re\b":                 "galleblære",
            r"\bkole[sz]ystitt\b":                   "kolecystitt",
            r"\bgallesten[er]*\b":                   "gallestener",
            r"\bkol[ae]dokus\b":                     "koledokus",
            r"\bgalleveier\b":                       "galleveier",
            r"\bkolangitt\b":                        "kolangitt",
            r"\bpneumobilia\b":                      "pneumobilia",
            r"\bMurphy[\'s]*\s+tegn\b":              "Murphys tegn",
            # ══ LEVER ══
            r"\blever\b":                            "lever",
            r"\bhepatom[ae]gali\b":                  "hepatomegali",
            r"\bcirrose\b":                          "cirrose",
            r"\bst[ae]atose\b":                      "steatose",
            r"\bfettlever\b":                        "fettlever",
            r"\bHCC\b":                              "HCC",
            r"\bh[ae]mangiom\b":                     "hemangiom",
            r"\bFNH\b":                              "FNH",
            r"\bportal\s+hypertensjon\b":            "portal hypertensjon",
            r"\bportaven[e]*\b":                     "portavene",
            r"\bleverparenkym\b":                    "leverparenkym",
            r"\belastografi\b":                      "elastografi",
            r"\bascites\b":                          "ascites",
            # ══ BUKSPYTTKJERTEL ══
            r"\bbukspyttkj[ae]rtel\b":               "bukspyttkjertel",
            r"\bpankreatitt\b":                      "pankreatitt",
            r"\bpseudocyste\b":                      "pseudocyste",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            # ══ MILT ══
            r"\bmilt\b":                             "milt",
            r"\bspl[ae]nom[ae]gali\b":               "splenomegali",
            r"\bhep[ae]tospl[ae]nom[ae]gali\b":       "hepatosplenomegali",
            # ══ NYRER & URINVEIER ══
            r"\bnyre[n]?\b":                         "nyre",
            r"\bnyreparenkym\b":                     "nyreparenkym",
            r"\bkortikomedull[ae]r\b":               "kortikomeduller",
            r"\bhydronefrose\b":                     "hydronefrose",
            r"\bur[ae]t[ae]r\b":                     "ureter",
            r"\bnefrolitiasis\b":                    "nefrolitiasis",
            r"\bpyelonefritt\b":                     "pyelonefritt",
            r"\bnyrecyste\b":                        "nyrecyste",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomyolipom\b":                    "angiomyolipom",
            r"\bnyrecellekarcinom\b":                "nyrecellekarcinom",
            r"\burinbl[æe]re\b":                     "urinblære",
            r"\bprostatakj[ae]rtel\b":               "prostatakjertel",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GYNEKOLOGI ══
            r"\blivmoren?\b":                        "livmor",
            r"\bendometrium\b":                      "endometrium",
            r"\bmyometrium\b":                       "myometrium",
            r"\beggstokk[er]*\b":                    "eggstokker",
            r"\beggstokkcyste\b":                    "eggstokkcyste",
            r"\bmyom\b":                             "myom",
            r"\bendometriose\b":                     "endometriose",
            r"\bDouglas\b":                          "Douglas",
            r"\bekt[oe]pisk\s+graviditet\b":         "ektopisk graviditet",
            r"\bplacenta\b":                         "placenta",
            # ══ SKJOLDBRUSKKJERTEL ══
            r"\bskjoldbruskkj[ae]rtel\b":            "skjoldbruskkjertel",
            r"\btyreoiditt\b":                       "tyreoiditt",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bstruma\b":                           "struma",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESKRIPTORER ══
            r"\ban[ae]kkogen\b":                     "anekogen",
            r"\bhyp[oe][ae]kkogen\b":                "hypoekogen",
            r"\bhyp[ae]r[ae]kkogen\b":               "hyperekogen",
            r"\bis[oe][ae]kkogen\b":                 "isoekogen",
            r"\bhomogens?\b":                        "homogen",
            r"\bheterogen\b":                        "heterogen",
            r"\bnormal\s+st[oe]rrelse\b":            "normal størrelse",
            r"\bregulære\s+konturer\b":              "regulære konturer",
            r"\bkalsifikasjon\b":                    "kalsifikasjon",
            r"\bstein[er]*\b":                       "steiner",
            r"\bfokal\s+lesjon\b":                   "fokal lesjon",
            r"\bnodule\b":                           "nodule",
            r"\bcyste\b":                            "cyste",
            r"\bascites\b":                          "ascites",
            r"\bDoppler\b":                          "Doppler",
            r"\bRI\b":                               "RI",
            r"\blymfeknuter\b":                      "lymfeknuter",
            r"\bfri\s+v[æe]ske\b":                   "fri væske",
        }

    CORRECTIONS_DA = {
            # ══ GALDEBLÆRE & GALDEVEJE ══
            r"\bgaldebla[æe]re\b":                   "galdeblære",
            r"\bkolecystitis?\b":                    "kolecystitis",
            r"\bgaldesten[e]*\b":                    "galdestenene",
            r"\bkol[ae]dokus\b":                     "koledokus",
            r"\bgaldevej[e]*\b":                     "galdeveje",
            r"\bkolangitis?\b":                      "kolangitis",
            r"\bpneumobilia\b":                      "pneumobilia",
            r"\bMurphy[\'s]*\s+tegn\b":              "Murphys tegn",
            # ══ LEVER ══
            r"\blever\b":                            "lever",
            r"\bhepatom[ae]gali\b":                  "hepatomegali",
            r"\bcirrose\b":                          "cirrose",
            r"\bst[ae]atose\b":                      "steatose",
            r"\bfedtlever\b":                        "fedtlever",
            r"\bHCC\b":                              "HCC",
            r"\bh[ae]mangiom\b":                     "hæmangiom",
            r"\bFNH\b":                              "FNH",
            r"\bportal\s+hypert[ae]nsion\b":         "portal hypertension",
            r"\bportalvene\b":                       "portalvene",
            r"\bleverpar[ae]nkym\b":                 "leverparenkym",
            r"\belastografi\b":                      "elastografi",
            r"\bascites\b":                          "ascites",
            # ══ BUGSPYTKIRTEL ══
            r"\bbugspytkirt[ae]l\b":                 "bugspytkirtel",
            r"\bpankreatitis?\b":                    "pankreatitis",
            r"\bpseudocyste\b":                      "pseudocyste",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            # ══ MILT ══
            r"\bmilt\b":                             "milt",
            r"\bspl[ae]nom[ae]gali\b":               "splenomegali",
            r"\bhep[ae]tospl[ae]nom[ae]gali\b":       "hepatosplenomegali",
            # ══ NYRER & URINVEJE ══
            r"\bnyre[r]*\b":                         "nyrerne",
            r"\bnyrepar[ae]nkym\b":                  "nyreparenkym",
            r"\bkortikomedull[ae]r\b":               "kortikomedullar",
            r"\bhydronefrose\b":                     "hydronefrose",
            r"\bur[ae]t[ae]r\b":                     "ureter",
            r"\bnefrolitas[iy]s\b":                  "nefrolitiasis",
            r"\bpyelonefritis?\b":                   "pyelonefritis",
            r"\bnyrecyste\b":                        "nyrecyste",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomyolipom\b":                    "angiomyolipom",
            r"\bnyrecellekarcinom\b":                "nyrecellekarcinom",
            r"\burinarybl[æe]re\b":                  "urinblæren",
            r"\bprostatakirt[ae]l\b":                "prostatakirtlen",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GYNÆKOLOGI ══
            r"\blivmoder\b":                         "livmoderen",
            r"\bendometrium\b":                      "endometrium",
            r"\bmyometrium\b":                       "myometrium",
            r"\b[ae]ggst[oe]k[k]*[e]*\b":            "æggestokkene",
            r"\bov[ae]riecyste\b":                   "ovariecyste",
            r"\bmyom\b":                             "myom",
            r"\bend[oe]metriose\b":                  "endometriose",
            r"\bDouglas\b":                          "Douglas",
            r"\bekt[oe]pisk\s+graviditet\b":         "ektopisk graviditet",
            r"\bplacenta\b":                         "placenta",
            # ══ SKJOLDBRUSKKIRTEL ══
            r"\bskjoldbr[ue]skkirt[ae]l\b":          "skjoldbruskkirtlen",
            r"\btyreoidiitis?\b":                    "thyreoiditis",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bstruma\b":                           "struma",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESKRIPTORER ══
            r"\ban[ae]kkogen\b":                     "anekogen",
            r"\bhyp[oe][ae]kkogen\b":                "hypoekogen",
            r"\bhyp[ae]r[ae]kkogen\b":               "hyperekogen",
            r"\bis[oe][ae]kkogen\b":                 "isoekogen",
            r"\bhomogen\b":                          "homogen",
            r"\bheterogen\b":                        "heterogen",
            r"\bnormal\s+st[oe]rrelse\b":            "normal størrelse",
            r"\bkalsificering\b":                    "kalsificering",
            r"\bsten[e]*\b":                         "sten",
            r"\bfokal\s+l[ae]sion\b":                "fokal læsion",
            r"\bnod[ue]l\b":                         "nodul",
            r"\bcyste\b":                            "cyste",
            r"\bascites\b":                          "ascites",
            r"\bDoppler\b":                          "Doppler",
            r"\bRI\b":                               "RI",
            r"\blymfeknuder\b":                      "lymfeknuder",
            r"\bfri\s+v[æe]ske\b":                   "fri væske",
        }

    CORRECTIONS_PL = {
            # ══ PĘCHERZYK ŻÓŁCIOWY ══
            r"\bp[eę]cherzyk\s+[żz][oó][łl]ciowy\b": "pęcherzyk żółciowy",
            r"\bpecherzyk\s+zolciowy\b":             "pęcherzyk żółciowy",
            r"\bzapalenie\s+p[eę]cherzy[kc]a\b":     "zapalenie pęcherzyka",
            r"\bcholes[iy]stitis?\b":                "zapalenie pęcherzyka",
            r"\bkamica\s+[żz][oó][łl]ciowa\b":        "kamica żółciowa",
            r"\bprzewód\s+[żz][oó][łl]ciowy\b":       "przewód żółciowy",
            r"\bprzew[oó]d\s+wspólny\b":              "przewód żółciowy wspólny",
            r"\bdr[oó]gi\s+[żz][oó][łl]ciowe\b":      "drogi żółciowe",
            r"\bzapalenie\s+dróg\s+[żż]ółciowych\b":  "zapalenie dróg żółciowych",
            r"\bMurphy[\'s]*\s+objaw\b":              "objaw Murphy'ego",
            r"\bMurphy\b":                           "Murphy",
            # ══ WĄTROBA ══
            r"\bw[ąa]troba\b":                       "wątroba",
            r"\bpowi[eę]kszenie\s+w[ąa]troby\b":     "powiększenie wątroby",
            r"\bhepatomeg[ae]lia\b":                  "hepatomegalia",
            r"\bmarsko[sść]\s+w[ąa]troby\b":          "marskość wątroby",
            r"\bst[ł]uszczenie\s+w[ąa]troby\b":       "stłuszczenie wątroby",
            r"\bst[ae]atoza\b":                       "steatoza",
            r"\brak\s+w[ąa]trobowokom[oó]rkowy\b":    "rak wątrobowokomórkowy",
            r"\bHCC\b":                               "HCC",
            r"\bnaczyniaki\b":                        "naczyniaki",
            r"\bFNH\b":                               "FNH",
            r"\bnadci[sś]nienie\s+wrotne\b":          "nadciśnienie wrotne",
            r"\bzakrzepica\s+[żż]y[łl]y\s+wrotnej\b": "zakrzepica żyły wrotnej",
            r"\b[żż]y[łl]a\s+wrotna\b":               "żyła wrotna",
            r"\bmi[ąa][żż]sz\s+w[ąa]troby\b":         "miąższ wątroby",
            r"\belastografia\b":                      "elastografia",
            r"\bwodobrzusze\b":                       "wodobrzusze",
            # ══ TRZUSTKA ══
            r"\btrzustka\b":                          "trzustka",
            r"\bzapalenie\s+trzustki\b":              "zapalenie trzustki",
            r"\bpankre[ae]titis?\b":                  "zapalenie trzustki",
            r"\btorbiel\s+rzekoma\b":                 "torbiel rzekoma",
            r"\bprzewód\s+Wirsunga\b":                "przewód Wirsunga",
            r"\bWirsung\b":                           "Wirsung",
            r"\bIPMN\b":                              "IPMN",
            r"\bgłowa\s+trzustki\b":                  "głowa trzustki",
            r"\btrzon\s+trzustki\b":                  "trzon trzustki",
            r"\bogon\s+trzustki\b":                   "ogon trzustki",
            # ══ ŚLEDZIONA ══
            r"\b[sś]ledziona\b":                      "śledziona",
            r"\bsplenomeg[ae]lia\b":                  "splenomegalia",
            r"\bdodatkowa\s+[sś]ledziona\b":          "dodatkowa śledziona",
            r"\bhepato[- ]?splenomeg[ae]lia\b":        "hepatosplenomegalia",
            # ══ NERKI I UKŁAD MOCZOWY ══
            r"\bnerki\b":                             "nerki",
            r"\bnerka\b":                             "nerka",
            r"\bmi[ąa][żż]sz\s+nerki\b":              "miąższ nerki",
            r"\bróżnicowanie\s+korowo[- ]?rdzenio\b":  "różnicowanie korowo-rdzeniowe",
            r"\bwodoner[cz]\b":                       "wodonercze",
            r"\bkamica\s+nerkowa\b":                  "kamica nerkowa",
            r"\bnefrolitas[iy]s\b":                   "nefrolitiasis",
            r"\bzapalenie\s+miedniczek\b":            "odmiedniczkowe zapalenie nerek",
            r"\btorbiel\s+nerki\b":                   "torbiel nerki",
            r"\bBosniak\b":                           "Bosniak",
            r"\bangiomyolipoma\b":                    "angiomyolipoma",
            r"\brak\s+nerkowokom[oó]rkowy\b":          "rak nerkowokomórkowy",
            r"\bpęcherz\s+moczowy\b":                 "pęcherz moczowy",
            r"\bgruczo[łl]\s+krokowy\b":              "gruczoł krokowy",
            r"\bprostata\b":                          "prostata",
            r"\bBPH\b":                               "BPH",
            r"\bPSA\b":                               "PSA",
            r"\bPI-RADS\b":                           "PI-RADS",
            # ══ GINEKOLOGIA ══
            r"\bmacica\b":                            "macica",
            r"\bbłona\s+[sś]luzowa\s+macicy\b":       "błona śluzowa macicy",
            r"\bendometrium\b":                       "endometrium",
            r"\bmyometrium\b":                        "myometrium",
            r"\bjajniki\b":                           "jajniki",
            r"\btorbiel\s+jajnika\b":                 "torbiel jajnika",
            r"\bmioma\b":                             "mięśniak",
            r"\bendometrioza\b":                      "endometrioza",
            r"\bzatoka\s+Douglasa\b":                 "zatoka Douglasa",
            r"\bci[ąa][żż]a\s+pozamaciczna\b":        "ciąża pozamaciczna",
            r"\b[lł]o[żż]ysko\b":                     "łożysko",
            # ══ TARCZYCA ══
            r"\btarczyca\b":                          "tarczyca",
            r"\bguzek\s+tarczycy\b":                  "guzek tarczycy",
            r"\bzapalenie\s+tarczycy\b":              "zapalenie tarczycy",
            r"\bHashimoto\b":                         "Hashimoto",
            r"\bGraves\b":                            "Graves",
            r"\bwole\b":                              "wole",
            r"\bTI-RADS\b":                           "TI-RADS",
            r"\bBI-RADS\b":                           "BI-RADS",
            r"\bLI-RADS\b":                           "LI-RADS",
            # ══ DESKRYPTORY ══
            r"\bbezeche\b":                           "bezechowy",
            r"\bbezechowy\b":                         "bezechowy",
            r"\bhipo[ae]chogeniczny\b":               "hipoechogeniczny",
            r"\bhiper[ae]chogeniczny\b":              "hiperechogeniczny",
            r"\biz[oe]echogeniczny\b":                "izoechogeniczny",
            r"\bechogeniczny\b":                      "echogeniczny",
            r"\bjednorodny\b":                        "jednorodny",
            r"\bniejednorodny\b":                     "niejednorodny",
            r"\bprawid[lł]owej\s+wielko[sś]ci\b":     "prawidłowej wielkości",
            r"\bregularne\s+zarysy\b":                "regularne zarysy",
            r"\bzwapnienie\b":                        "zwapnienie",
            r"\bkamien[ień]*\b":                      "kamień",
            r"\bogniskowa\s+zmiana\b":                "ogniskowa zmiana",
            r"\bguzek\b":                             "guzek",
            r"\btorbiel\b":                           "torbiel",
            r"\bwodobrzusze\b":                       "wodobrzusze",
            r"\bDoppler\b":                           "Doppler",
            r"\bRI\b":                                "RI",
            r"\bwęz[lł]y\s+ch[lł]onne\b":             "węzły chłonne",
            r"\bwolny\s+p[lł]yn\b":                   "wolny płyn",
        }

    CORRECTIONS_HI = {
            # ══ पित्ताशय ══
            r"\bपित्ताशय\b":                         "पित्ताशय",
            r"\bपित्त\s*थैली\b":                     "पित्त थैली",
            r"\bपित्ताशयशोथ\b":                      "पित्ताशयशोथ",
            r"\bतीव्र\s+पित्ताशयशोथ\b":             "तीव्र पित्ताशयशोथ",
            r"\bपित्त\s*पथरी\b":                     "पित्त पथरी",
            r"\bसामान्य\s+पित्त\s+नलिका\b":          "सामान्य पित्त नलिका",
            r"\bपित्त\s+नलिका\b":                    "पित्त नलिका",
            r"\bMurphy\s+संकेत\b":                   "Murphy संकेत",
            # ══ यकृत ══
            r"\bयकृत\b":                             "यकृत",
            r"\bजिगर\b":                             "यकृत",
            r"\bहेपेटोमेगाली\b":                     "हेपेटोमेगाली",
            r"\bयकृत\s+वृद्धि\b":                    "यकृत वृद्धि",
            r"\bसिरोसिस\b":                          "सिरोसिस",
            r"\bवसा\s+यकृत\b":                       "वसायुक्त यकृत",
            r"\bHCC\b":                               "HCC",
            r"\bFNH\b":                               "FNH",
            r"\bपोर्टल\s+उच्च\s+रक्तचाप\b":          "पोर्टल उच्च रक्तचाप",
            r"\bपोर्टल\s+शिरा\b":                    "पोर्टल शिरा",
            r"\bजलोदर\b":                            "जलोदर",
            r"\bइलास्टोग्राफी\b":                    "इलास्टोग्राफी",
            # ══ अग्न्याशय ══
            r"\bअग्न्याशय\b":                        "अग्न्याशय",
            r"\bपैंक्रियाटाइटिस\b":                  "अग्न्याशयशोथ",
            r"\bपैंक्रियास\b":                       "अग्न्याशय",
            r"\bIPMN\b":                              "IPMN",
            # ══ प्लीहा ══
            r"\bप्लीहा\b":                           "प्लीहा",
            r"\bतिल्ली\b":                           "प्लीहा",
            r"\bस्प्लेनोमेगाली\b":                   "स्प्लेनोमेगाली",
            # ══ गुर्दे ══
            r"\bगुर्दे\b":                           "गुर्दे",
            r"\bगुर्दा\b":                           "गुर्दा",
            r"\bकॉर्टिकोमेडुलरी\s+विभेदन\b":          "कॉर्टिकोमेडुलरी विभेदन",
            r"\bहाइड्रोनेफ्रोसिस\b":                "हाइड्रोनेफ्रोसिस",
            r"\bमूत्रवाहिनी\b":                      "मूत्रवाहिनी",
            r"\bगुर्दे\s+की\s+पथरी\b":               "गुर्दे की पथरी",
            r"\bपायलोनेफ्राइटिस\b":                  "पायलोनेफ्राइटिस",
            r"\bगुर्दे\s+की\s+पुटी\b":               "गुर्दे की पुटी",
            r"\bBosniak\b":                           "Bosniak",
            r"\bमूत्राशय\b":                         "मूत्राशय",
            r"\bप्रोस्टेट\b":                        "प्रोस्टेट",
            r"\bBPH\b":                               "BPH",
            r"\bPSA\b":                               "PSA",
            r"\bPI-RADS\b":                           "PI-RADS",
            # ══ स्त्री रोग ══
            r"\bगर्भाशय\b":                          "गर्भाशय",
            r"\bएंडोमेट्रियम\b":                     "एंडोमेट्रियम",
            r"\bमायोमेट्रियम\b":                     "मायोमेट्रियम",
            r"\bअंडाशय\b":                           "अंडाशय",
            r"\bअंडाशय\s+पुटी\b":                    "अंडाशय पुटी",
            r"\bगर्भाशय\s+फाइब्रॉइड\b":              "गर्भाशय फाइब्रॉइड",
            r"\bएंडोमेट्रियोसिस\b":                  "एंडोमेट्रियोसिस",
            r"\bडगलस\s+पाउच\b":                      "डगलस पाउच",
            r"\bअस्थानिक\s+गर्भावस्था\b":             "अस्थानिक गर्भावस्था",
            r"\bनाल\b":                              "नाल",
            # ══ थायराइड ══
            r"\bथायराइड\b":                          "थायराइड",
            r"\bथायराइड\s+गांठ\b":                   "थायराइड गांठ",
            r"\bथायराइडाइटिस\b":                     "थायराइडाइटिस",
            r"\bहाशिमोटो\b":                         "हाशिमोटो",
            r"\bTI-RADS\b":                           "TI-RADS",
            r"\bBI-RADS\b":                           "BI-RADS",
            r"\bLI-RADS\b":                           "LI-RADS",
            # ══ वर्णनकर्ता ══
            r"\bअनेकोइक\b":                          "अनेकोइक",
            r"\bहाइपोइकोइक\b":                       "हाइपोइकोइक",
            r"\bहाइपरइकोइक\b":                       "हाइपरइकोइक",
            r"\bइकोजेनिक\b":                         "इकोजेनिक",
            r"\bसमांगी\b":                           "समांगी",
            r"\bविषमांगी\b":                          "विषमांगी",
            r"\bसामान्य\s+आकार\b":                    "सामान्य आकार",
            r"\bकैल्सीफिकेशन\b":                     "कैल्सीफिकेशन",
            r"\bपथरी\b":                             "पथरी",
            r"\bफोकल\s+घाव\b":                       "फोकल घाव",
            r"\bनोड्यूल\b":                          "नोड्यूल",
            r"\bपुटी\b":                             "पुटी",
            r"\bजलोदर\b":                            "जलोदर",
            r"\bडॉप्लर\b":                           "डॉप्लर",
            r"\bRI\b":                                "RI",
            r"\bलसीका\s+ग्रंथि\b":                    "लसीका ग्रंथि",
            r"\bमुक्त\s+द्रव\b":                      "मुक्त द्रव",
        }

    CORRECTIONS_ID = {
            # ══ KANDUNG EMPEDU ══
            r"\bkandung\s+empedu\b":                 "kandung empedu",
            r"\bkolesistitis\b":                     "kolesistitis",
            r"\bkolesistitis\s+akut\b":              "kolesistitis akut",
            r"\bkolelitias[iy]s\b":                  "kolelitiasis",
            r"\bkol[ae]dokus\b":                     "koledokus",
            r"\bsaluran\s+empedu\b":                 "saluran empedu",
            r"\bkolangitis\b":                       "kolangitis",
            r"\btanda\s+Murphy\b":                   "tanda Murphy",
            r"\bMurphy\b":                           "Murphy",
            r"\bperikolesistik\b":                   "perikolesistik",
            # ══ HATI ══
            r"\bhati\b":                             "hati",
            r"\bhepatomeg[ae]li\b":                  "hepatomegali",
            r"\bsirosis\b":                          "sirosis",
            r"\bsirosis\s+hati\b":                   "sirosis hati",
            r"\bst[ae]atosis\b":                     "steatosis",
            r"\bhati\s+berlemak\b":                  "hati berlemak",
            r"\bkarsinoma\s+hepatoselular\b":         "karsinoma hepatoseluler",
            r"\bHCC\b":                              "HCC",
            r"\bhemangioma\b":                       "hemangioma",
            r"\bFNH\b":                              "FNH",
            r"\bhip[ae]rt[ae]nsi\s+portal\b":         "hipertensi portal",
            r"\bvena\s+porta\b":                     "vena porta",
            r"\bpar[ae]nkim\s+hati\b":               "parenkim hati",
            r"\belastografi\b":                      "elastografi",
            r"\baskites\b":                          "asites",
            # ══ PANKREAS ══
            r"\bpankre[ae]s\b":                      "pankreas",
            r"\bpankreatitis\b":                     "pankreatitis",
            r"\bpankreatitis\s+akut\b":              "pankreatitis akut",
            r"\bpankreatitis\s+kronis\b":             "pankreatitis kronis",
            r"\bpseudokista\b":                      "pseudokista",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bkaput\s+pankre[ae]s\b":              "kaput pankreas",
            # ══ LIMPA ══
            r"\blimpa\b":                            "limpa",
            r"\bspl[ae]nom[ae]gali\b":               "splenomegali",
            r"\bhep[ae]tospl[ae]nom[ae]gali\b":       "hepatosplenomegali",
            r"\blimpa\s+aksesoris\b":                "limpa aksesoris",
            # ══ GINJAL & SALURAN KEMIH ══
            r"\bginjal\b":                           "ginjal",
            r"\bpar[ae]nkim\s+ginjal\b":             "parenkim ginjal",
            r"\bdiff[ae]r[ae]nsiasi\s+kortiko[- ]?m[ae]duler\b": "diferensiasi kortikomeduler",
            r"\bhidron[ae]frosis\b":                 "hidronefrosis",
            r"\bur[ae]t[ae]r\b":                     "ureter",
            r"\bnefrolitiasis\b":                    "nefrolitiasis",
            r"\bpi[ae]lon[ae]fritis\b":              "pielonefritis",
            r"\bkista\s+ginjal\b":                   "kista ginjal",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomiolipoma\b":                   "angiomiolipoma",
            r"\bkarsinoma\s+sel\s+ginjal\b":          "karsinoma sel ginjal",
            r"\bk[ae]ndung\s+kemih\b":               "kandung kemih",
            r"\bprostat\b":                          "prostat",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GINEKOLOGI ══
            r"\buterus\b":                           "uterus",
            r"\bend[oe]metrium\b":                   "endometrium",
            r"\bmi[oe]metrium\b":                    "miometrium",
            r"\bovarium\b":                          "ovarium",
            r"\bkista\s+ovarium\b":                  "kista ovarium",
            r"\bmioma\b":                            "mioma",
            r"\bend[oe]metriosis\b":                 "endometriosis",
            r"\bpouch\s+of\s+Douglas\b":             "kantong Douglas",
            r"\bkantong\s+Douglas\b":                "kantong Douglas",
            r"\bhamil\s+di\s+luar\s+kandungan\b":    "kehamilan ektopik",
            r"\bkehamilan\s+ektopik\b":              "kehamilan ektopik",
            r"\bplacenta\b":                         "plasenta",
            # ══ TIROID ══
            r"\btiroid\b":                           "tiroid",
            r"\bnodul\s+tiroid\b":                   "nodul tiroid",
            r"\btir[oe]iditis\b":                    "tiroiditis",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bgoiter\b":                           "gondok",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESKRIPTOR ══
            r"\bane[ck][oe]ik\b":                    "anekoid",
            r"\bhip[oe]ek[oe]ik\b":                  "hipoekoid",
            r"\bhip[ae]rek[oe]ik\b":                 "hiperekoid",
            r"\bek[oe]gen[iy]k\b":                   "ekogenik",
            r"\bhom[oe]gen\b":                       "homogen",
            r"\bh[ae]t[ae]r[oe]gen\b":               "heterogen",
            r"\bukuran\s+normal\b":                  "ukuran normal",
            r"\bkontur\s+reguler\b":                 "kontur reguler",
            r"\bk[ae]lsifikasi\b":                   "kalsifikasi",
            r"\bbatu\b":                             "batu",
            r"\blitiasis\b":                         "litiasis",
            r"\blesion\s+fokal\b":                   "lesi fokal",
            r"\bnodul\b":                            "nodul",
            r"\bkista\b":                            "kista",
            r"\basites\b":                           "asites",
            r"\bcairan\s+bebas\b":                   "cairan bebas",
            r"\bDoppl[ae]r\b":                       "Doppler",
            r"\bindeks\s+resistensi\b":              "indeks resistensi",
            r"\bRI\b":                               "RI",
            r"\bvaskularisasi\b":                    "vaskularisasi",
            r"\bkelenjar\s+getah\s+bening\b":        "kelenjar getah bening",
            r"\bcairan\s+bebas\b":                   "cairan bebas",
        }

    CORRECTIONS_TH = {
            # ══ ถุงน้ำดี ══
            r"\bถุงน้ำดี\b":                         "ถุงน้ำดี",
            r"\bถุงน้ำดีอักเสบ\b":                   "ถุงน้ำดีอักเสบ",
            r"\bนิ่วในถุงน้ำดี\b":                   "นิ่วในถุงน้ำดี",
            r"\bท่อน้ำดี\b":                         "ท่อน้ำดี",
            r"\bMurphy\b":                           "Murphy",
            # ══ ตับ ══
            r"\bตับ\b":                              "ตับ",
            r"\bตับโต\b":                            "ตับโต",
            r"\bตับแข็ง\b":                          "ตับแข็ง",
            r"\bไขมันพอกตับ\b":                      "ไขมันพอกตับ",
            r"\bHCC\b":                               "HCC",
            r"\bFNH\b":                               "FNH",
            r"\bความดันพอร์ทัล\b":                   "ความดันพอร์ทัล",
            r"\bหลอดเลือดดำพอร์ทัล\b":               "หลอดเลือดดำพอร์ทัล",
            r"\bน้ำในช่องท้อง\b":                    "น้ำในช่องท้อง",
            # ══ ตับอ่อน ══
            r"\bตับอ่อน\b":                          "ตับอ่อน",
            r"\bตับอ่อนอักเสบ\b":                    "ตับอ่อนอักเสบ",
            r"\bIPMN\b":                              "IPMN",
            # ══ ม้าม ══
            r"\bม้าม\b":                             "ม้าม",
            r"\bม้ามโต\b":                           "ม้ามโต",
            # ══ ไต ══
            r"\bไต\b":                               "ไต",
            r"\bไตโต\b":                             "ไตโต",
            r"\bน้ำคั่งในไต\b":                      "น้ำคั่งในไต",
            r"\bท่อไต\b":                            "ท่อไต",
            r"\bนิ่วในไต\b":                         "นิ่วในไต",
            r"\bกรวยไตอักเสบ\b":                     "กรวยไตอักเสบ",
            r"\bถุงน้ำในไต\b":                       "ถุงน้ำในไต",
            r"\bBosniak\b":                           "Bosniak",
            r"\bกระเพาะปัสสาวะ\b":                   "กระเพาะปัสสาวะ",
            r"\bต่อมลูกหมาก\b":                      "ต่อมลูกหมาก",
            r"\bBPH\b":                               "BPH",
            r"\bPSA\b":                               "PSA",
            r"\bPI-RADS\b":                           "PI-RADS",
            # ══ นรีเวช ══
            r"\bมดลูก\b":                            "มดลูก",
            r"\bเยื่อบุโพรงมดลูก\b":                 "เยื่อบุโพรงมดลูก",
            r"\bรังไข่\b":                           "รังไข่",
            r"\bถุงน้ำในรังไข่\b":                   "ถุงน้ำในรังไข่",
            r"\bเนื้องอกมดลูก\b":                    "เนื้องอกมดลูก",
            r"\bเยื่อบุโพรงมดลูกเจริญผิดที่\b":     "เยื่อบุโพรงมดลูกเจริญผิดที่",
            r"\bตั้งครรภ์นอกมดลูก\b":               "ตั้งครรภ์นอกมดลูก",
            r"\bรก\b":                               "รก",
            # ══ ต่อมไทรอยด์ ══
            r"\bต่อมไทรอยด์\b":                     "ต่อมไทรอยด์",
            r"\bก้อนไทรอยด์\b":                     "ก้อนไทรอยด์",
            r"\bไทรอยด์อักเสบ\b":                   "ไทรอยด์อักเสบ",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ คำอธิบาย ══
            r"\bไม่มีเสียงสะท้อน\b":                 "ไม่มีเสียงสะท้อน",
            r"\bเสียงสะท้อนต่ำ\b":                   "เสียงสะท้อนต่ำ",
            r"\bเสียงสะท้อนสูง\b":                   "เสียงสะท้อนสูง",
            r"\bเสียงสะท้อนเท่ากัน\b":               "เสียงสะท้อนเท่ากัน",
            r"\bสม่ำเสมอ\b":                         "สม่ำเสมอ",
            r"\bไม่สม่ำเสมอ\b":                      "ไม่สม่ำเสมอ",
            r"\bขนาดปกติ\b":                         "ขนาดปกติ",
            r"\bหินปูน\b":                           "หินปูน",
            r"\bนิ่ว\b":                             "นิ่ว",
            r"\bรอยโรค\b":                           "รอยโรค",
            r"\bก้อนน้ำ\b":                          "ก้อนน้ำ",
            r"\bน้ำในช่องท้อง\b":                    "น้ำในช่องท้อง",
            r"\bดอปเปลอร์\b":                        "ดอปเปลอร์",
            r"\bRI\b":                                "RI",
            r"\bต่อมน้ำเหลือง\b":                    "ต่อมน้ำเหลือง",
            r"\bน้ำในช่องท้อง\b":                    "น้ำในช่องท้อง",
        }

    CORRECTIONS_MS = {
            # ══ PUNDI HEMPEDU ══
            r"\bpundi\s+hempedu\b":                  "pundi hempedu",
            r"\bkolesi[sz]titis\b":                  "kolesistitis",
            r"\bkolesitiasis\b":                     "kolelitiasis",
            r"\bduktus\s+kol[ae]dokus\b":             "duktus koledokus",
            r"\bsalur[ae]n\s+hempedu\b":              "saluran hempedu",
            r"\bkolangitis\b":                       "kolangitis",
            r"\btanda\s+Murphy\b":                   "tanda Murphy",
            r"\bMurphy\b":                           "Murphy",
            r"\bperikolesistik\b":                   "perikolesistik",
            # ══ HATI ══
            r"\bhati\b":                             "hati",
            r"\bhepatom[ae]gali\b":                  "hepatomegali",
            r"\bsir[oe]sis\b":                       "sirosis",
            r"\bst[ae]atosis\b":                     "steatosis",
            r"\bhati\s+berlemak\b":                  "hati berlemak",
            r"\bkarsinoma\s+hepatoselular\b":         "karsinoma hepatoseluler",
            r"\bHCC\b":                              "HCC",
            r"\bhemangioma\b":                       "hemangioma",
            r"\bFNH\b":                              "FNH",
            r"\bhip[ae]rt[ae]nsi\s+portal\b":         "hipertensi portal",
            r"\bvena\s+portal\b":                    "vena portal",
            r"\bpar[ae]nkim\s+hati\b":               "parenkim hati",
            r"\belastografi\b":                      "elastografi",
            r"\baskites\b":                          "asites",
            # ══ PANKREAS ══
            r"\bpankre[ae]s\b":                      "pankreas",
            r"\bpankreatitis\b":                     "pankreatitis",
            r"\bpseudosista\b":                      "pseudosista",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            # ══ LIMPA ══
            r"\blimpa\b":                            "limpa",
            r"\bspl[ae]nom[ae]gali\b":               "splenomegali",
            r"\bhep[ae]tospl[ae]nom[ae]gali\b":       "hepatosplenomegali",
            # ══ BUAH PINGGANG & SALUR KENCING ══
            r"\bbuah\s+pinggang\b":                  "buah pinggang",
            r"\bpar[ae]nkim\s+buah\s+pinggang\b":    "parenkim buah pinggang",
            r"\bpembez[ae]an\s+kortikomedular\b":     "pembezaan kortikomedullar",
            r"\bhidr[oe]n[ae]frosis\b":              "hidronefrosis",
            r"\bur[ae]t[ae]r\b":                     "ureter",
            r"\bbatu\s+buah\s+pinggang\b":           "batu buah pinggang",
            r"\bpielonefritis\b":                    "pielonefritis",
            r"\bsista\s+buah\s+pinggang\b":          "sista buah pinggang",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomiolipoma\b":                   "angiomiolipoma",
            r"\bpundi\s+kencing\b":                  "pundi kencing",
            r"\bprostat\b":                          "prostat",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GINEKOLOGI ══
            r"\buterus\b":                           "uterus",
            r"\bend[oe]metrium\b":                   "endometrium",
            r"\bmi[oe]metrium\b":                    "miometrium",
            r"\bovari\b":                            "ovari",
            r"\bsista\s+ovari\b":                    "sista ovari",
            r"\bmioma\b":                            "mioma",
            r"\bend[oe]metriosis\b":                 "endometriosis",
            r"\bpouch\s+of\s+Douglas\b":             "kantung Douglas",
            r"\bkantung\s+Douglas\b":                "kantung Douglas",
            r"\bhamil\s+ek[dt]opik\b":               "kehamilan ektopik",
            r"\bplasen[dt]a\b":                      "plasenta",
            # ══ TIROID ══
            r"\btiroid\b":                           "tiroid",
            r"\bnod[ue]l\s+tiroid\b":                "nodul tiroid",
            r"\btiroiditis\b":                       "tiroiditis",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bgoiter\b":                           "goiter",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESKRIPTOR ══
            r"\bane[ck][oe]ik\b":                    "anekoid",
            r"\bhip[oe]ek[oe]ik\b":                  "hipoekoid",
            r"\bhip[ae]rek[oe]ik\b":                 "hiperekoid",
            r"\bek[oe]genik\b":                      "ekogenik",
            r"\bhom[oe]gen\b":                       "homogen",
            r"\bh[ae]t[ae]r[oe]gen\b":               "heterogen",
            r"\bsaiz\s+normal\b":                    "saiz normal",
            r"\bkontur\s+r[ae]gular\b":              "kontur reguler",
            r"\bkalsifikasi\b":                      "kalsifikasi",
            r"\bbatu\b":                             "batu",
            r"\blitiasis\b":                         "litiasis",
            r"\blesyen\s+fokal\b":                   "lesyen fokal",
            r"\bnodul\b":                            "nodul",
            r"\bsista\b":                            "sista",
            r"\baskites\b":                          "asites",
            r"\bcairan\s+bebas\b":                   "cecair bebas",
            r"\bDoppl[ae]r\b":                       "Doppler",
            r"\bRI\b":                               "RI",
            r"\bnod[ue]\s+limfa\b":                  "nod limfa",
            r"\bcecair\s+bebas\b":                   "cecair bebas",
        }

    CORRECTIONS_EL = {
            # ══ ΧΟΛΟΔΟΧΟΣ ΚΥΣΤΗ ══
            r"\bχολοδόχος\s+κύστη\b":                "χολοδόχος κύστη",
            r"\bχολοκυστίτιδα\b":                    "χολοκυστίτιδα",
            r"\bχολολιθίαση\b":                      "χολολιθίαση",
            r"\bχοληδόχος\s+πόρος\b":                "χοληδόχος πόρος",
            r"\bχοληδοχολιθίαση\b":                  "χοληδοχολιθίαση",
            r"\bχολαγγειίτιδα\b":                    "χολαγγειίτιδα",
            r"\bπνευμοχολία\b":                      "πνευμοχολία",
            r"\bσημείο\s+Murphy\b":                  "σημείο Murphy",
            r"\bMurphy\b":                           "Murphy",
            r"\bπεριχολοκυστικ[οα]\b":               "περιχολοκυστικό",
            # ══ ΗΠΑΡ ══
            r"\bήπαρ\b":                             "ήπαρ",
            r"\bηπατομεγαλία\b":                     "ηπατομεγαλία",
            r"\bκίρρωση\b":                          "κίρρωση",
            r"\bηπατική\s+κίρρωση\b":                "ηπατική κίρρωση",
            r"\bστεάτωση\b":                         "στεάτωση",
            r"\bλιπώδης\s+διήθηση\b":                "λιπώδης διήθηση",
            r"\bηπατοκυτταρικό\s+καρκίνωμα\b":       "ηπατοκυτταρικό καρκίνωμα",
            r"\bHCC\b":                              "HCC",
            r"\bαιμαγγείωμα\b":                      "αιμαγγείωμα",
            r"\bFNH\b":                              "FNH",
            r"\bπυλαία\s+υπέρταση\b":                "πυλαία υπέρταση",
            r"\bπυλαία\s+φλέβα\b":                   "πυλαία φλέβα",
            r"\bηπατικό\s+παρέγχυμα\b":              "ηπατικό παρέγχυμα",
            r"\bελαστογραφία\b":                     "ελαστογραφία",
            r"\bασκίτης\b":                          "ασκίτης",
            # ══ ΠΑΓΚΡΕΑΣ ══
            r"\bπάγκρεας\b":                         "πάγκρεας",
            r"\bπαγκρεατίτιδα\b":                    "παγκρεατίτιδα",
            r"\bοξεία\s+παγκρεατίτιδα\b":            "οξεία παγκρεατίτιδα",
            r"\bχρόνια\s+παγκρεατίτιδα\b":           "χρόνια παγκρεατίτιδα",
            r"\bψευδοκύστη\b":                       "ψευδοκύστη",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            r"\bκεφαλή\s+παγκρέατος\b":              "κεφαλή παγκρέατος",
            r"\bσώμα\s+παγκρέατος\b":                "σώμα παγκρέατος",
            r"\bουρά\s+παγκρέατος\b":                "ουρά παγκρέατος",
            # ══ ΣΠΛΗΝΑ ══
            r"\bσπλήνα\b":                           "σπλήνα",
            r"\bσπληνομεγαλία\b":                    "σπληνομεγαλία",
            r"\bηπατοσπληνομεγαλία\b":               "ηπατοσπληνομεγαλία",
            r"\bαξεσουάρ\s+σπλήνα\b":               "επιπλέον σπλήνα",
            # ══ ΝΕΦΡΟΙ & ΟΥΡΟΦΟΡΟΙ ΟΔΟΙ ══
            r"\bνεφροί\b":                           "νεφροί",
            r"\bνεφρικό\s+παρέγχυμα\b":              "νεφρικό παρέγχυμα",
            r"\bφλοιομυελώδης\s+διαφοροποίηση\b":    "φλοιομυελώδης διαφοροποίηση",
            r"\bυδρονέφρωση\b":                      "υδρονέφρωση",
            r"\bουρητήρας\b":                        "ουρητήρας",
            r"\bνεφρολιθίαση\b":                     "νεφρολιθίαση",
            r"\bπυελονεφρίτιδα\b":                   "πυελονεφρίτιδα",
            r"\bνεφρική\s+κύστη\b":                  "νεφρική κύστη",
            r"\bBosniak\b":                          "Bosniak",
            r"\bαγγειομυολίπωμα\b":                  "αγγειομυολίπωμα",
            r"\bνεφροκυτταρικό\s+καρκίνωμα\b":       "νεφροκυτταρικό καρκίνωμα",
            r"\bουροδόχος\s+κύστη\b":                "ουροδόχος κύστη",
            r"\bπροστάτης\b":                        "προστάτης",
            r"\bαδένωμα\s+προστάτη\b":               "αδένωμα προστάτη",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ ΓΥΝΑΙΚΟΛΟΓΙΑ ══
            r"\bμήτρα\b":                            "μήτρα",
            r"\bενδομήτριο\b":                       "ενδομήτριο",
            r"\bμυομήτριο\b":                        "μυομήτριο",
            r"\bτράχηλος\s+μήτρας\b":                "τράχηλος μήτρας",
            r"\bωοθήκες\b":                          "ωοθήκες",
            r"\bωοθηκική\s+κύστη\b":                 "ωοθηκική κύστη",
            r"\bίνωμα\b":                            "ίνωμα",
            r"\bενδομητρίωση\b":                     "ενδομητρίωση",
            r"\bεκτοπική\s+κύηση\b":                 "εκτοπική κύηση",
            r"\bπλακούντας\b":                       "πλακούντας",
            r"\bΔούγλας\b":                          "Δούγλας",
            # ══ ΘΥΡΕΟΕΙΔΗΣ ══
            r"\bθυρεοειδής\b":                       "θυρεοειδής",
            r"\bθυρεοειδικό\s+οζίδιο\b":             "θυρεοειδικό οζίδιο",
            r"\bθυρεοειδίτιδα\b":                    "θυρεοειδίτιδα",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bβρογχοκήλη\b":                       "βρογχοκήλη",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ ΠΕΡΙΓΡΑΦΙΚΟΙ ΟΡΟΙ ══
            r"\bαν[εη]χο[ιί]κ[οόα]\b":               "ανηχοϊκό",
            r"\bυπ[εη]χ[οω]γεν[εη][σς]\b":           "υποηχογενής",
            r"\bυπ[εη]ρ[εη]χ[οω]γεν[εη][σς]\b":      "υπερηχογενής",
            r"\b[ιί]σ[οω][εη]χ[οω]γεν[εη][σς]\b":    "ισοηχογενής",
            r"\b[εη]χ[οω]γεν[εη][σς]\b":             "ηχογενής",
            r"\b[εη]χ[οω]γεν[ιί]κ[οόα]\b":           "ηχογενικό",
            r"\b[οο]μ[οό]γεν[εη][σς]\b":             "ομογενής",
            r"\b[αα]ν[οό]μ[οο]ιογεν[εη][σς]\b":      "ανομοιογενής",
            r"\bφυσιολογικ[οό]\s+μέγεθος\b":          "φυσιολογικό μέγεθος",
            r"\bκανονικά\s+όρια\b":                   "κανονικά όρια",
            r"\bαπ[οο]τιτάνωση\b":                   "αποτιτάνωση",
            r"\bλιθίαση\b":                          "λιθίαση",
            r"\bεστιακή\s+βλάβη\b":                  "εστιακή βλάβη",
            r"\bοζίδιο\b":                           "οζίδιο",
            r"\bκύστη\b":                            "κύστη",
            r"\bασκίτης\b":                          "ασκίτης",
            r"\bΔόπλερ\b":                           "Doppler",
            r"\bDoppler\b":                          "Doppler",
            r"\bδείκτης\s+αντίστασης\b":              "δείκτης αντίστασης",
            r"\bRI\b":                               "RI",
            r"\bαγγείωση\b":                         "αγγείωση",
            r"\bλεμφαδένες\b":                       "λεμφαδένες",
            r"\bελεύθερο\s+υγρό\b":                  "ελεύθερο υγρό",
        }

    CORRECTIONS_TL = {
            # ══ GALLBLADDER ══
            r"\bapdo\b":                             "apdo",
            r"\bpneu[mn]onia\s+ng\s+apdo\b":         "kolecistitis",
            r"\bkolesi[sz]titis\b":                  "kolecistitis",
            r"\bbato\s+sa\s+apdo\b":                 "bato sa apdo",
            r"\bkol[ae]dokus\b":                     "koledokus",
            r"\btubong\s+apdo\b":                    "tubong apdo",
            r"\bkolangitis\b":                       "kolangitis",
            r"\btanda\s+ng\s+Murphy\b":              "tanda ng Murphy",
            r"\bMurphy\b":                           "Murphy",
            # ══ ATAY ══
            r"\batay\b":                             "atay",
            r"\bpalaking\s+atay\b":                  "palaking atay",
            r"\bhep[ae]tom[ae]galia\b":              "hepatomegalia",
            r"\bsirosis\b":                          "sirosis",
            r"\bst[ae]atosis\b":                     "steatosis",
            r"\bmataba\s+na\s+atay\b":               "mataba na atay",
            r"\bHCC\b":                              "HCC",
            r"\bFNH\b":                              "FNH",
            r"\bp[oe]rtal\s+hiper[td][ae]nsyon\b":   "portal hipertensiyon",
            r"\bp[oe]rtal\s+na\s+ugat\b":            "portal na ugat",
            r"\bpar[ae]nkima\s+ng\s+atay\b":          "parenkima ng atay",
            r"\bel[ae]stograpiya\b":                 "elastograpiya",
            r"\baskites\b":                          "askites",
            # ══ PANCREAS ══
            r"\bpankre[ae]s\b":                      "pankreas",
            r"\bpankre[ae]titis\b":                  "pankreatitis",
            r"\bWirsung\b":                          "Wirsung",
            r"\bIPMN\b":                             "IPMN",
            # ══ PALI ══
            r"\bpali\b":                             "pali",
            r"\bspl[ae]nom[ae]galia\b":              "splenomegalia",
            r"\bhep[ae]tospl[ae]nom[ae]galia\b":      "hepatosplenomegalia",
            # ══ BATO ══
            r"\bbato\b":                             "bato",
            r"\bpar[ae]nkima\s+ng\s+bato\b":          "parenkima ng bato",
            r"\bhydron[ae]phrosis\b":                "hidronephrosis",
            r"\bur[ae]t[ae]r\b":                     "ureter",
            r"\bbato\s+sa\s+kidney\b":               "bato sa bato",
            r"\bpi[ae]lon[ae]phritis\b":             "pielonephritis",
            r"\bkiste\s+ng\s+bato\b":                "kiste ng bato",
            r"\bBosniak\b":                          "Bosniak",
            r"\bangiomiol[iy]poma\b":                "angiomyolipoma",
            r"\bpantog\s+ihi\b":                     "pantog ihi",
            r"\bpr[oe]stata\b":                      "prostata",
            r"\bBPH\b":                              "BPH",
            r"\bPSA\b":                              "PSA",
            r"\bPI-RADS\b":                          "PI-RADS",
            # ══ GYNECOLOGY ══
            r"\bmatris\b":                           "matris",
            r"\bend[oe]metrium\b":                   "endometrium",
            r"\bmy[oe]metrium\b":                    "myometrium",
            r"\bowari[oe]s?\b":                      "obaryo",
            r"\bkiste\s+ng\s+obaryo\b":              "kiste ng obaryo",
            r"\bmiy[oe]ma\b":                        "myoma",
            r"\bend[oe]metri[oe]sis\b":              "endometriosis",
            r"\bek[dt][oe]pik\s+pagbubuntis\b":       "ektopikong pagbubuntis",
            r"\bplasenta\b":                         "plasenta",
            # ══ THYROID ══
            r"\btayroid\b":                          "tayroid",
            r"\btayroid\s+nod[ue]l\b":               "tayroid nodule",
            r"\btayroid[iy]t[iy]s\b":                "tayroiditis",
            r"\bHashimoto\b":                        "Hashimoto",
            r"\bGraves\b":                           "Graves",
            r"\bgoiter\b":                           "goiter",
            r"\bTI-RADS\b":                          "TI-RADS",
            r"\bBI-RADS\b":                          "BI-RADS",
            r"\bLI-RADS\b":                          "LI-RADS",
            # ══ DESCRIPTORS ══
            r"\ban[ae]k[oe]ik\b":                    "anekoid",
            r"\bhip[oe][ae]k[oe]ik\b":               "hipoekoid",
            r"\bhip[ae]r[ae]k[oe]ik\b":              "hiperekoik",
            r"\bek[oe]genikong\b":                   "ekogenik",
            r"\bhom[oe]hen[iy][oe]s\b":              "homohenyos",
            r"\bhet[ae]r[oe]hen[iy][oe]s\b":          "heterohenyo",
            r"\bnormal\s+sukat\b":                   "normal na sukat",
            r"\bregular\s+na\s+hangganan\b":         "regular na hangganan",
            r"\bkalsip[iy]kasyon\b":                 "kalsipikasyon",
            r"\bbato\b":                             "bato",
            r"\bfokal\s+lesyon\b":                   "fokal na lesyon",
            r"\bnod[ue]l\b":                         "nodule",
            r"\bkiste\b":                            "kiste",
            r"\baskites\b":                          "askites",
            r"\bDoppler\b":                          "Doppler",
            r"\bRI\b":                               "RI",
            r"\blimpa\s+nod[ue]l\b":                 "limp na nodule",
            r"\bmalayang\s+likido\b":                "malayang likido",
        }

    _compiled_en = None
    _compiled_de = None
    _compiled_es = None
    _compiled_it = None
    _compiled_pt = None
    _compiled_ru = None
    _compiled_ro = None
    _compiled_zh = None
    _compiled_ja = None
    _compiled_ko = None
    _compiled_tr = None
    _compiled_nl = None
    _compiled_sv = None
    _compiled_no = None
    _compiled_da = None
    _compiled_pl = None
    _compiled_hi = None
    _compiled_id = None
    _compiled_th = None
    _compiled_ms = None
    _compiled_el = None
    _compiled_tl = None

    @classmethod
    def _compile_dict(cls, d):
        """Compile un dictionnaire de corrections (patterns triés du plus long au plus court)."""
        return [
            (_re.compile(pat, _re.IGNORECASE), repl)
            for pat, repl in sorted(d.items(), key=lambda x: len(x[0]), reverse=True)
        ]

    @classmethod
    def _get_compiled_en(cls):
        if cls._compiled_en is None:
            cls._compiled_en = cls._compile_dict(cls.CORRECTIONS_EN)
        return cls._compiled_en

    @classmethod
    def _get_compiled_de(cls):
        if cls._compiled_de is None:
            cls._compiled_de = cls._compile_dict(cls.CORRECTIONS_DE)
        return cls._compiled_de

    @classmethod
    def _get_compiled_es(cls):
        if cls._compiled_es is None:
            cls._compiled_es = cls._compile_dict(cls.CORRECTIONS_ES)
        return cls._compiled_es

    @classmethod
    def _get_compiled_it(cls):
        if cls._compiled_it is None:
            cls._compiled_it = cls._compile_dict(cls.CORRECTIONS_IT)
        return cls._compiled_it

    @classmethod
    def _get_compiled_pt(cls):
        if cls._compiled_pt is None:
            cls._compiled_pt = cls._compile_dict(cls.CORRECTIONS_PT)
        return cls._compiled_pt

    @classmethod
    def _get_compiled_ru(cls):
        if cls._compiled_ru is None:
            cls._compiled_ru = cls._compile_dict(cls.CORRECTIONS_RU)
        return cls._compiled_ru

    @classmethod
    def _get_compiled_ro(cls):
        if cls._compiled_ro is None:
            cls._compiled_ro = cls._compile_dict(cls.CORRECTIONS_RO)
        return cls._compiled_ro

    @classmethod
    def _get_compiled_zh(cls):
        if cls._compiled_zh is None:
            cls._compiled_zh = cls._compile_dict(cls.CORRECTIONS_ZH)
        return cls._compiled_zh

    @classmethod
    def _get_compiled_ja(cls):
        if cls._compiled_ja is None:
            cls._compiled_ja = cls._compile_dict(cls.CORRECTIONS_JA)
        return cls._compiled_ja

    @classmethod
    def _get_compiled_ko(cls):
        if cls._compiled_ko is None:
            cls._compiled_ko = cls._compile_dict(cls.CORRECTIONS_KO)
        return cls._compiled_ko

    @classmethod
    def _get_compiled_tr(cls):
        if cls._compiled_tr is None:
            cls._compiled_tr = cls._compile_dict(cls.CORRECTIONS_TR)
        return cls._compiled_tr

    @classmethod
    def _get_compiled_nl(cls):
        if cls._compiled_nl is None:
            cls._compiled_nl = cls._compile_dict(cls.CORRECTIONS_NL)
        return cls._compiled_nl

    @classmethod
    def _get_compiled_sv(cls):
        if cls._compiled_sv is None:
            cls._compiled_sv = cls._compile_dict(cls.CORRECTIONS_SV)
        return cls._compiled_sv

    @classmethod
    def _get_compiled_no(cls):
        if cls._compiled_no is None:
            cls._compiled_no = cls._compile_dict(cls.CORRECTIONS_NO)
        return cls._compiled_no

    @classmethod
    def _get_compiled_da(cls):
        if cls._compiled_da is None:
            cls._compiled_da = cls._compile_dict(cls.CORRECTIONS_DA)
        return cls._compiled_da

    @classmethod
    def _get_compiled_pl(cls):
        if cls._compiled_pl is None:
            cls._compiled_pl = cls._compile_dict(cls.CORRECTIONS_PL)
        return cls._compiled_pl

    @classmethod
    def _get_compiled_hi(cls):
        if cls._compiled_hi is None:
            cls._compiled_hi = cls._compile_dict(cls.CORRECTIONS_HI)
        return cls._compiled_hi

    @classmethod
    def _get_compiled_id(cls):
        if cls._compiled_id is None:
            cls._compiled_id = cls._compile_dict(cls.CORRECTIONS_ID)
        return cls._compiled_id

    @classmethod
    def _get_compiled_th(cls):
        if cls._compiled_th is None:
            cls._compiled_th = cls._compile_dict(cls.CORRECTIONS_TH)
        return cls._compiled_th

    @classmethod
    def _get_compiled_ms(cls):
        if cls._compiled_ms is None:
            cls._compiled_ms = cls._compile_dict(cls.CORRECTIONS_MS)
        return cls._compiled_ms

    @classmethod
    def _get_compiled_el(cls):
        if cls._compiled_el is None:
            cls._compiled_el = cls._compile_dict(cls.CORRECTIONS_EL)
        return cls._compiled_el

    @classmethod
    def _get_compiled_tl(cls):
        if cls._compiled_tl is None:
            cls._compiled_tl = cls._compile_dict(cls.CORRECTIONS_TL)
        return cls._compiled_tl

    # Mapping UI name → Whisper code (fallback si PyArmor passe le nom complet)
    _UI_LANG_CODES = {
        'Français': 'fr', 'English': 'en', 'Deutsch': 'de',
        'Español': 'es', 'Italiano': 'it', 'Português': 'pt',
        'Русский': 'ru', 'Nederlands': 'nl', 'Polski': 'pl',
        'Svenska': 'sv', 'Norsk': 'no', 'Dansk': 'da',
        'Türkçe': 'tr', '中文': 'zh', '日本語': 'ja',
        '한국어': 'ko', 'Ελληνικά': 'el', 'Română': 'ro',
        'हिन्दी': 'hi', 'Bahasa Indonesia': 'id', 'ไทย': 'th',
        'Bahasa Melayu': 'ms', 'Filipino': 'tl',
    }

    @classmethod
    def correct(cls, text: str, language: str = "fr") -> str:
        """
        Applique les corrections post-transcription selon la langue.
        Accepte un code ISO ('en', 'fr'…) ou un nom UI ('English', 'Français'…).
        """
        result = text

        # Normaliser : accepter noms UI en plus des codes ISO (robustesse PyArmor)
        lang = cls._UI_LANG_CODES.get(language, language)

        # Appliquer les corrections selon la langue
        lang_map = {
            "fr":    cls._get_compiled,
            "fr-FR": cls._get_compiled,
            "en":    cls._get_compiled_en,
            "en-US": cls._get_compiled_en,
            "en-GB": cls._get_compiled_en,
            "de":    cls._get_compiled_de,
            "es":    cls._get_compiled_es,
            "it":    cls._get_compiled_it,
            "pt":    cls._get_compiled_pt,
            "ru":    cls._get_compiled_ru,
            "ro":    cls._get_compiled_ro,
            "zh":    cls._get_compiled_zh,
            "ja":    cls._get_compiled_ja,
            "ko":    cls._get_compiled_ko,
            "tr":    cls._get_compiled_tr,
            "nl":    cls._get_compiled_nl,
            "sv":    cls._get_compiled_sv,
            "no":    cls._get_compiled_no,
            "da":    cls._get_compiled_da,
            "pl":    cls._get_compiled_pl,
            "hi":    cls._get_compiled_hi,
            "id":    cls._get_compiled_id,
            "th":    cls._get_compiled_th,
            "ms":    cls._get_compiled_ms,
            "el":    cls._get_compiled_el,
            "tl":    cls._get_compiled_tl,
        }
        getter = lang_map.get(lang)
        if getter:
            for pattern, replacement in getter():
                result = pattern.sub(replacement, result)

        # Capitalisation universelle (toutes langues sauf CJK et RTL)
        _no_capitalize = {"zh", "ja", "ko", "ar", "th", "hi"}
        if lang not in _no_capitalize:
            result = _re.sub(
                r'([.!?]\s+)([a-zàâçéèêëîïôùûüœæäöüßáéíóúñ])',
                lambda m: m.group(1) + m.group(2).upper(),
                result
            )
            if result and result[0].islower():
                result = result[0].upper() + result[1:]

        return result.strip()
