import json
import time
import re
import os
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
model = genai.GenerativeModel(
    "gemini-2.5-flash-lite",
    generation_config=genai.GenerationConfig(max_output_tokens=65536),
)

# ── Config ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent

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

PROMPT = """\
You are a professional medical software translator specializing in radiology.

Translate the JSON values below from English into {lang_name} ({lang_code}).

STRICT RULES:
1. Translate VALUES only — never touch the keys.
2. Keep ALL HTML tags verbatim: <strong>, <br/>, <span style="...">, etc.
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


def call_gemini(chunk: dict, lang_code: str, lang_name: str, retries: int = 3) -> dict:
    prompt = PROMPT.format(
        lang_name=lang_name,
        lang_code=lang_code,
        json_content=json.dumps(chunk, ensure_ascii=False, indent=2),
    )
    for attempt in range(1, retries + 1):
        try:
            response = model.generate_content(prompt)
            raw = response.text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```\s*$", "", raw, flags=re.MULTILINE)
            return json.loads(raw.strip())
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


def main():
    for trans_dir in TRANSLATION_DIRS:
        en_path = trans_dir / "en.json"
        if not en_path.exists():
            print(f"\nSKIP — no en.json in: {trans_dir}")
            continue

        with open(en_path, encoding="utf-8") as f:
            en_data = json.load(f)

        top_keys = list(en_data.keys())
        print(f"\n{'='*60}")
        print(f"Source  : {en_path}")
        print(f"Sections: {', '.join(top_keys)}")
        print(f"{'='*60}")

        for lang_code, lang_name in LANGUAGES.items():
            out_path = trans_dir / f"{lang_code}.json"

            if out_path.exists():
                print(f"  [{lang_code}] already exists — skipping")
                continue

            print(f"  [{lang_code}] {lang_name}")
            translated_data = {}

            for i, key in enumerate(top_keys, 1):
                print(f"      section {i}/{len(top_keys)}: {key} ...", end=" ", flush=True)
                try:
                    result = call_gemini({key: en_data[key]}, lang_code, lang_name)
                    translated_data[key] = result[key]
                    print("✓")
                except Exception as e:
                    print(f"✗ ({e}) — keeping original")
                    translated_data[key] = en_data[key]
                time.sleep(1)

            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(translated_data, f, ensure_ascii=False, indent=2)
            print(f"  [{lang_code}] saved ✓\n")
            time.sleep(2)

    print("All done.")


if __name__ == "__main__":
    main()
