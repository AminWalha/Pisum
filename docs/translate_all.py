"""
translate_all.py — PISUM translation updater

USAGE
-----
  # Add only missing keys (default — uses FORCE_KEYS_* below as persistent defaults)
  python translate_all.py

  # ── Change detection workflow ──────────────────────────────────────────────
  # STEP 1 (first time, or after a git commit): init snapshot from last commit
  python translate_all.py --snapshot-from-git

  # STEP 2: translate changed + new keys, then save new snapshot
  python translate_all.py --detect-changes --save-snapshot

  # After each subsequent edit to en.json, just repeat STEP 2.
  # ──────────────────────────────────────────────────────────────────────────

  # Force-retranslate specific keys (overrides FORCE_KEYS_* for this run)
  python translate_all.py --force faq.ans_1 faq.ans_2
  python translate_all.py --force dashboard.js          # whole sub-object
  python translate_all.py --force faq                   # entire section

  # Full rebuild — retranslate everything (also fixes corrupted HTML in translations)
  python translate_all.py --all
  python translate_all.py --all --lang tr               # fix corrupted Turkish only

  # Filters
  python translate_all.py --lang fr de es               # specific languages only
  python translate_all.py --dir saas                    # saas/frontend/translations only
  python translate_all.py --dir main                    # docs/translations only

  # Combine
  python translate_all.py --detect-changes --save-snapshot --lang fr --dir saas
"""

import argparse
import json
import subprocess
import time
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import google.generativeai as genai
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────────
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise SystemExit("ERROR: GEMINI_API_KEY not found in .env")

genai.configure(api_key=API_KEY)
_print_lock = threading.Lock()
model = genai.GenerativeModel(
    "gemini-2.5-flash-lite",
    generation_config=genai.GenerationConfig(max_output_tokens=65536),
)

# ── Config ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent
SNAPSHOT_FILE = ".en_baseline.json"

TRANSLATION_DIRS = [
    BASE / "translations",
    BASE / "saas" / "frontend" / "translations",
]

LANGUAGES = {
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ru": "Russian",
    "tr": "Turkish",
    "sv": "Swedish",
    "pl": "Polish",
    "el": "Greek",
    "zh": "Chinese (Simplified)",
    "no": "Norwegian",
    "da": "Danish",
    "ja": "Japanese",
    "ko": "Korean",
    "hi": "Hindi",
    "id": "Indonesian",
    "th": "Thai",
    "ms": "Malay",
    "tl": "Filipino (Tagalog)",
    "ro": "Romanian",
}

# ── Persistent force-key defaults (used when --force is NOT passed on CLI) ─────
# Format: { "section": ["key1", "key2"] }
# Use "*" as the only element to force-retranslate the entire section.
# Clear these once the run has been applied and pushed.

FORCE_KEYS_MAIN = {
    # "faq": ["*"],   # example: retranslate entire faq section
}

FORCE_KEYS_SAAS = {
    # "dashboard": ["js"],   # example: retranslate dashboard.js sub-object
}

# ── Prompt ─────────────────────────────────────────────────────────────────────
PROMPT = """\
You are a professional medical software translator specializing in radiology.

Translate the JSON values below from English into {lang_name} ({lang_code}).

STRICT RULES:
1. Translate VALUES only — never touch the keys.
2. Keep ALL @@N@@ placeholders (e.g. @@0@@, @@1@@, @@12@@) EXACTLY as-is — \
they represent HTML tags and must be copied verbatim into the correct position \
in your translation. Never modify, remove, merge, or translate them.
3. Keep ALL HTML entities verbatim: &amp; &lt; &gt; &#x27; etc.
4. Do NOT translate: PISUM, PDF, MRI, CT, AI, GDPR, LAN, NAS, SMB, AES-256, PCI, Windows.
5. Do NOT translate: email addresses (you@clinic.com), prices (€79/mo), domain names.
6. Keep symbols exactly as-is: ✕ ✓ — · ← ▼ 👋 ⭐ 🔥 🏥 🖥️ 🔐 ⚡
7. Keep "••••••••" exactly as-is.
8. The values "CANCEL" and "DELETE" (confirmation words users must type) \
— translate to the natural {lang_name} uppercase equivalent.
9. Return ONLY raw valid JSON. No markdown, no code fences, no explanation.

JSON to translate:
{json_content}"""


# ── HTML placeholder protection ────────────────────────────────────────────────
def protect_html(chunk: dict) -> tuple:
    """Replace all HTML tags in JSON string values with @@N@@ placeholders.

    This prevents Gemini from corrupting or numbering HTML tags.
    Returns (protected_chunk, tag_map) where tag_map maps @@N@@ back to original tags.
    """
    counter = [0]
    tag_map = {}

    def _protect(obj):
        if isinstance(obj, str):
            result = obj
            for tag in re.findall(r"<[^>]+>", obj):
                ph = f"@@{counter[0]}@@"
                counter[0] += 1
                tag_map[ph] = tag
                result = result.replace(tag, ph, 1)
            return result
        if isinstance(obj, dict):
            return {k: _protect(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_protect(v) for v in obj]
        return obj

    return _protect(chunk), tag_map


def restore_html(chunk: dict, tag_map: dict) -> dict:
    """Restore @@N@@ placeholders back to original HTML tags."""
    def _restore(obj):
        if isinstance(obj, str):
            for ph, tag in tag_map.items():
                obj = obj.replace(ph, tag)
            return obj
        if isinstance(obj, dict):
            return {k: _restore(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_restore(v) for v in obj]
        return obj
    return _restore(chunk)


# ── Snapshot helpers ───────────────────────────────────────────────────────────
def load_snapshot(trans_dir: Path) -> dict:
    """Load the saved English baseline for change detection."""
    path = trans_dir / SNAPSHOT_FILE
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_snapshot(trans_dir: Path, en_data: dict) -> None:
    """Save current en.json as the baseline for future --detect-changes runs."""
    path = trans_dir / SNAPSHOT_FILE
    with open(path, "w", encoding="utf-8") as f:
        json.dump(en_data, f, ensure_ascii=False, indent=2)
    print(f"  [snapshot] saved → {path.name}\n")


def snapshot_from_git(trans_dir: Path) -> bool:
    """Initialize snapshot from the last committed version of en.json in git.

    This is the correct way to bootstrap change detection: the committed version
    is what was translated before, so any diff against current en.json represents
    what needs retranslation.
    Returns True on success, False on failure.
    """
    en_path = trans_dir / "en.json"
    # Find git root
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=str(trans_dir),
    )
    if r.returncode != 0:
        print(f"  [snapshot-from-git] ERROR: not a git repo? {r.stderr.strip()}")
        return False
    git_root = Path(r.stdout.strip())
    try:
        rel_path = en_path.relative_to(git_root).as_posix()
    except ValueError:
        print(f"  [snapshot-from-git] ERROR: {en_path} is outside git root {git_root}")
        return False

    r = subprocess.run(
        ["git", "show", f"HEAD:{rel_path}"],
        capture_output=True, text=True, cwd=str(git_root),
    )
    if r.returncode != 0:
        print(f"  [snapshot-from-git] ERROR: could not read HEAD:{rel_path}\n  {r.stderr.strip()}")
        return False

    try:
        committed_en = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        print(f"  [snapshot-from-git] ERROR: invalid JSON in HEAD:{rel_path}: {e}")
        return False

    save_snapshot(trans_dir, committed_en)
    print(f"  [snapshot-from-git] initialized from HEAD commit → ready for --detect-changes\n")
    return True


# ── Helpers ────────────────────────────────────────────────────────────────────
def parse_force_args(force_args: list) -> dict:
    """
    Convert CLI --force arguments into a force_keys dict.

    Formats accepted:
      "section"        → entire section (all keys)
      "section.key"    → specific key inside section
      "section.a.b"    → nested key path (dot-separated)

    Returns: { "section": ["key1", "*", ...] }
    Use "*" to mean "entire section".
    """
    result = {}
    for arg in (force_args or []):
        parts = arg.split(".", 1)
        section = parts[0]
        key = parts[1] if len(parts) > 1 else "*"
        result.setdefault(section, [])
        if "*" not in result[section]:
            result[section].append(key)
    return result


def call_gemini(chunk: dict, lang_code: str, lang_name: str, retries: int = 3) -> dict:
    protected_chunk, tag_map = protect_html(chunk)
    prompt = PROMPT.format(
        lang_name=lang_name,
        lang_code=lang_code,
        json_content=json.dumps(protected_chunk, ensure_ascii=False, indent=2),
    )
    for attempt in range(1, retries + 1):
        try:
            response = model.generate_content(prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
            result = json.loads(raw.strip())
            return restore_html(result, tag_map)
        except json.JSONDecodeError as e:
            print(f"\n      JSON parse error (attempt {attempt}/{retries}): {e}")
            if attempt == retries:
                raise
            time.sleep(3 * attempt)
        except Exception as e:
            print(f"\n      API error (attempt {attempt}/{retries}): {e}")
            if attempt == retries:
                raise
            time.sleep(5 * attempt)


def compute_delta(en_section: dict, existing_section: dict,
                  forced_keys: list, force_all: bool,
                  changed_keys: list = None) -> dict:
    """
    Return only the keys that need to be (re)translated:
      - Missing keys (never translated before)
      - Keys listed in forced_keys
      - Keys whose English value changed since last snapshot (changed_keys)
      - All keys if force_all=True or forced_keys==['*']
    """
    if force_all or forced_keys == ["*"]:
        return dict(en_section)
    changed_keys = changed_keys or []
    delta = {}
    for k, v in en_section.items():
        if k not in existing_section or k in forced_keys or k in changed_keys:
            delta[k] = v
    return delta


def update_translation_file(en_data: dict, out_path: Path, lang_code: str,
                             lang_name: str, force_keys: dict,
                             force_all: bool = False,
                             snapshot: dict = None) -> None:
    def _log(msg: str) -> None:
        with _print_lock:
            print(msg, flush=True)

    if out_path.exists():
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)
        is_new = False
    else:
        existing = {}
        is_new = True

    snapshot = snapshot or {}
    any_change = False

    for section_key, en_section in en_data.items():
        existing_section = existing.get(section_key, {})
        forced = force_keys.get(section_key, [])
        snapshot_section = snapshot.get(section_key, {})

        # Keys whose English value changed since last snapshot (exist in translation but stale)
        changed_in_section = [
            k for k, v in en_section.items()
            if k in snapshot_section and snapshot_section[k] != v
        ] if snapshot_section else []

        delta = compute_delta(en_section, existing_section, forced, force_all,
                              changed_in_section)

        if not delta:
            continue

        # Build a readable label
        new_in_delta     = [k for k in delta if k not in existing_section]
        changed_in_delta = [k for k in delta if k in changed_in_section and k in existing_section]
        forced_only      = [k for k in delta
                            if k in forced and k not in new_in_delta and k not in changed_in_delta]
        label_parts = []
        if force_all:
            label_parts = [f"{len(delta)} (full rebuild)"]
        else:
            if new_in_delta:
                label_parts.append(f"{len(new_in_delta)} new")
            if changed_in_delta:
                label_parts.append(f"{len(changed_in_delta)} changed")
            if forced_only:
                label_parts.append(f"{len(forced_only)} forced")

        section_prefix = f"      section '{section_key}': {', '.join(label_parts) or str(len(delta))} key(s) ..."

        try:
            result = call_gemini({section_key: delta}, lang_code, lang_name)
            existing.setdefault(section_key, {}).update(result[section_key])
            _log(section_prefix + " ✓")
            any_change = True
        except Exception as e:
            _log(section_prefix + f" ✗ ({e}) — keeping English fallback")
            existing.setdefault(section_key, {}).update(delta)
            any_change = True

        time.sleep(1)

    if any_change:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        action = "created" if is_new else "updated"
        _log(f"  [{lang_code}] {action} ✓")
    else:
        _log(f"  [{lang_code}] already up-to-date — no changes")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Update PISUM translation JSON files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--force", nargs="+", metavar="SECTION[.KEY]",
        help=(
            "Force-retranslate specific keys even if they already exist. "
            "Format: 'section.key' or 'section' (entire section). "
            "Overrides FORCE_KEYS_* defaults for this run."
        ),
    )
    parser.add_argument(
        "--all", dest="force_all", action="store_true",
        help="Full rebuild — retranslate ALL keys in ALL sections.",
    )
    parser.add_argument(
        "--lang", nargs="+", metavar="CODE",
        help="Only process these language codes (e.g. fr de es).",
    )
    parser.add_argument(
        "--dir", choices=["main", "saas", "both"], default="both",
        help=(
            "Which translation directory to process. "
            "'main' = docs/translations/, "
            "'saas' = saas/frontend/translations/, "
            "'both' = all (default)."
        ),
    )
    parser.add_argument(
        "--detect-changes", dest="detect_changes", action="store_true",
        help=(
            "Detect and retranslate keys whose English value changed since "
            "the last --save-snapshot run. Requires a saved baseline."
        ),
    )
    parser.add_argument(
        "--save-snapshot", dest="save_snapshot", action="store_true",
        help=(
            "Save current en.json as the baseline after the run. "
            "Use together with --detect-changes for the standard update workflow: "
            "  python translate_all.py --detect-changes --save-snapshot"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=4, metavar="N",
        help="Number of parallel translation threads (default: 4).",
    )
    parser.add_argument(
        "--snapshot-from-git", dest="snapshot_from_git", action="store_true",
        help=(
            "Bootstrap the snapshot from the last git commit of en.json. "
            "Run this ONCE before using --detect-changes for the first time. "
            "The committed version represents what was last translated, so any "
            "diff against the current en.json is exactly what needs retranslation."
        ),
    )
    args = parser.parse_args()

    # CLI --force overrides FORCE_KEYS_* (when provided)
    cli_force = parse_force_args(args.force) if args.force else None

    # Language filter
    if args.lang:
        unknown = [l for l in args.lang if l not in LANGUAGES]
        if unknown:
            raise SystemExit(f"ERROR: Unknown language code(s): {', '.join(unknown)}")
        langs = {k: v for k, v in LANGUAGES.items() if k in args.lang}
    else:
        langs = LANGUAGES

    # Directory filter
    if args.dir == "main":
        dirs = [TRANSLATION_DIRS[0]]
    elif args.dir == "saas":
        dirs = [TRANSLATION_DIRS[1]]
    else:
        dirs = TRANSLATION_DIRS

    # --snapshot-from-git: bootstrap only, no translation
    if args.snapshot_from_git:
        for trans_dir in dirs:
            en_path = trans_dir / "en.json"
            if not en_path.exists():
                print(f"\nSKIP — no en.json in: {trans_dir}")
                continue
            print(f"\nSource: {en_path}")
            snapshot_from_git(trans_dir)
        print("Snapshot(s) initialized. Now run:")
        print("  python translate_all.py --detect-changes --save-snapshot")
        return

    # Summary header
    mode_label = "FULL REBUILD" if args.force_all else ("FORCE UPDATE" if cli_force else "UPDATE (missing + defaults)")
    if args.detect_changes:
        mode_label += " + DETECT CHANGES"
    if args.save_snapshot:
        mode_label += " + SAVE SNAPSHOT"
    print(f"\n{'='*60}")
    print(f"Mode   : {mode_label}")
    if args.lang:
        print(f"Langs  : {', '.join(args.lang)}")
    if cli_force:
        for sec, keys in cli_force.items():
            print(f"  Force [{sec}]: {', '.join(keys)}")
    elif not args.force_all:
        for label, fk in [("main", FORCE_KEYS_MAIN), ("saas", FORCE_KEYS_SAAS)]:
            for sec, keys in fk.items():
                print(f"  Default force [{label}/{sec}]: {', '.join(keys)}")
    print(f"{'='*60}")

    for trans_dir in dirs:
        en_path = trans_dir / "en.json"
        if not en_path.exists():
            print(f"\nSKIP — no en.json in: {trans_dir}")
            continue

        with open(en_path, encoding="utf-8") as f:
            en_data = json.load(f)

        # Load snapshot for change detection
        snapshot = {}
        if args.detect_changes:
            snapshot = load_snapshot(trans_dir)
            if not snapshot:
                print(f"\n  [warn] No snapshot in {trans_dir.name} — "
                      f"run with --save-snapshot first to enable change detection.")

        # Choose force_keys: CLI > persistent defaults
        if cli_force is not None or args.force_all:
            force_keys = cli_force or {}
        else:
            is_saas = "saas" in str(trans_dir)
            force_keys = FORCE_KEYS_SAAS if is_saas else FORCE_KEYS_MAIN

        print(f"\nSource: {en_path}")
        print(f"Sections: {', '.join(en_data.keys())}\n")

        def _process(lang_code: str, lang_name: str) -> None:
            out_path = trans_dir / f"{lang_code}.json"
            with _print_lock:
                print(f"  [{lang_code}] {lang_name}", flush=True)
            update_translation_file(
                en_data, out_path, lang_code, lang_name,
                force_keys, force_all=args.force_all,
                snapshot=snapshot,
            )

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(_process, lc, ln): lc
                for lc, ln in langs.items()
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    lc = futures[future]
                    with _print_lock:
                        print(f"  [{lc}] THREAD ERROR: {e}\n", flush=True)

        if args.save_snapshot:
            save_snapshot(trans_dir, en_data)

    print("All done.")


if __name__ == "__main__":
    main()
