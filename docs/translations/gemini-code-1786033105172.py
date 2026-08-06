import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from deep_translator import GoogleTranslator

# Détection automatique du dossier du script (docs/translations)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Exceptions de codes ISO pour Google Translate
LANG_MAPPING = {
    "zh": "zh-CN",
    "tl": "tl",
}

def get_target_lang(filename):
    code = filename.replace('.json', '')
    return LANG_MAPPING.get(code, code)

def flatten_dict(d, parent_key=''):
    """Aplatit le dictionnaire JSON imbriqué en paires 'chemin.cle': 'valeur'."""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}.{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key).items())
        else:
            items.append((new_key, v))
    return dict(items)

def set_nested_key(d, path, value):
    """Reconstruit la structure JSON imbriquée."""
    keys = path.split('.')
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value

def translate_text(translator, path, text):
    """Effectue la traduction avec un micro-délai pour éviter le rate-limit."""
    try:
        time.sleep(0.05)
        translated = translator.translate(text)
        return path, translated
    except Exception as e:
        print(f"  ❌ Erreur sur '{path}': {e}")
        return path, text

def process_file(file_path, en_flat, modified_paths, max_key_workers=5):
    filename = os.path.basename(file_path)
    
    # Exclut les fichiers cachés (.en_baseline.json, etc.)
    if filename.startswith('.'):
        return

    lang_code = get_target_lang(filename)
    print(f"🔄 Traitement de {filename} ({lang_code})...")

    target_data = {}
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                target_data = json.load(f)
        except json.JSONDecodeError:
            print(f"  ⚠️ Fichier {filename} corrompu, recréation complète.")

    target_flat = flatten_dict(target_data)

    # Détection des clés à traduire :
    # 1. Clé absente du fichier langue
    # 2. Clé présente mais valeur vide
    # 3. Clé dont le texte source dans en.json a été modifié (par rapport à la baseline)
    to_translate = []
    for path, en_text in en_flat.items():
        is_missing = path not in target_flat or not target_flat[path]
        is_modified = path in modified_paths

        if is_missing or is_modified:
            to_translate.append((path, en_text))

    if not to_translate:
        print(f"  ✅ {filename} est déjà parfaitement synchronisé.")
        return

    print(f"  ⚡ {len(to_translate)} clés à traduire/mettre à jour dans {filename}...")

    translator = GoogleTranslator(source='en', target=lang_code)

    # Multi-threading sécurisé (5 workers max par fichier)
    with ThreadPoolExecutor(max_workers=max_key_workers) as executor:
        futures = [
            executor.submit(translate_text, translator, path, text)
            for path, text in to_translate
        ]
        for future in as_completed(futures):
            path, translated_text = future.result()
            target_flat[path] = translated_text

    # Reconstruction propre selon l'ordre strict de en.json
    new_target_data = {}
    for path in en_flat.keys():
        set_nested_key(new_target_data, path, target_flat.get(path, en_flat[path]))

    # Sauvegarde du fichier langue mis à jour en UTF-8 propre
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(new_target_data, f, ensure_ascii=False, indent=2)

    print(f"  ✨ {filename} mis à jour avec succès.")

def main():
    en_file = os.path.join(BASE_DIR, 'en.json')
    baseline_file = os.path.join(BASE_DIR, '.en_baseline.json')

    if not os.path.exists(en_file):
        print(f"❌ Erreur : en.json est introuvable dans {BASE_DIR}")
        return

    # Lecture de en.json
    with open(en_file, 'r', encoding='utf-8-sig') as f:
        en_structure = json.load(f)

    en_flat = flatten_dict(en_structure)

    # Comparaison de en.json avec .en_baseline.json pour trouver les modifications de texte
    modified_paths = set()
    if os.path.exists(baseline_file):
        try:
            with open(baseline_file, 'r', encoding='utf-8-sig') as f:
                baseline_flat = flatten_dict(json.load(f))

            for path, en_text in en_flat.items():
                if path in baseline_flat and baseline_flat[path] != en_text:
                    modified_paths.add(path)

            if modified_paths:
                print(f"📝 {len(modified_paths)} modification(s) de texte détectée(s) dans en.json.")
        except Exception as e:
            print(f"⚠️ Erreur lors de la lecture de la baseline : {e}")

    # Récupération de tous les fichiers cibles (exclut en.json et les fichiers cachés)
    target_files = [
        os.path.join(BASE_DIR, f) 
        for f in os.listdir(BASE_DIR) 
        if f.endswith('.json') and f != 'en.json' and not f.startswith('.')
    ]

    print(f"🚀 Début de la synchronisation sur {len(target_files)} fichiers...")

    # Traitement de 2 fichiers en parallèle (max 10 threads API simultanés)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(process_file, f, en_flat, modified_paths)
            for f in target_files
        ]
        for future in as_completed(futures):
            future.result()

    # Mise à jour de la baseline .en_baseline.json avec le nouvel état de en.json
    with open(baseline_file, 'w', encoding='utf-8') as f:
        json.dump(en_structure, f, ensure_ascii=False, indent=2)

    print("\n🎉 Synchronisation terminée ! Tous les fichiers et la baseline sont à jour.")

if __name__ == "__main__":
    main()