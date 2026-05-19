import json
import os
import time
from bs4 import BeautifulSoup, NavigableString
from deep_translator import GoogleTranslator

dirs = [
    r"c:\Users\walha\Desktop\PISUM\LOGICIEL\PISUM\Logiciel Pisum V2.9.8.2\docs\translations",
    r"c:\Users\walha\Desktop\PISUM\LOGICIEL\PISUM\Logiciel Pisum V2.9.8.2\docs\saas\frontend\translations"
]

TARGET_LANGUAGES = {
    'fr': 'Français',
    'de': 'Deutsch',
    'es': 'Español',
    'it': 'Italiano',
    'pt': 'Português',
    'nl': 'Nederlands',
    'ru': 'Русский',
    'tr': 'Türkçe',
    'sv': 'Svenska',
    'pl': 'Polski',
    'el': 'Ελληνικά',
    'zh': '中文',
    'no': 'Norsk',
    'da': 'Dansk',
    'ja': '日本語',
    'ko': '한국어',
    'hi': 'हिन्दी',
    'id': 'Bahasa Indonesia',
    'th': 'ไทย',
    'ms': 'Bahasa Melayu',
    'tl': 'Filipino',
    'ro': 'Română',
}

def extract_strings(d, path=[]):
    strings = []
    for k, v in d.items():
        if isinstance(v, dict):
            strings.extend(extract_strings(v, path + [k]))
        elif isinstance(v, str):
            strings.append((path + [k], v))
    return strings

def set_value(d, path, value):
    for key in path[:-1]:
        d = d[key]
    d[path[-1]] = value

def process_directory(d):
    print(f"\nProcessing directory: {d}", flush=True)
    en_file = os.path.join(d, 'en.json')
    if not os.path.exists(en_file):
        print("en.json not found, skipping.", flush=True)
        return

    with open(en_file, 'r', encoding='utf-8') as f:
        en_data = json.load(f)

    strings_with_paths = extract_strings(en_data)

    for target_lang, lang_label in TARGET_LANGUAGES.items():
        target_file = os.path.join(d, f'{target_lang}.json')

        print(f"Translating to {lang_label} ({target_lang})...", flush=True)

        lang_code = target_lang
        if lang_code == 'zh':
            lang_code = 'zh-CN'

        try:
            translator = GoogleTranslator(source='en', target=lang_code)
        except Exception as e:
            print(f"Skipping {target_lang} due to initialization error: {e}", flush=True)
            continue

        # 1. Extraction Phase
        texts_to_translate = []
        structure_map = []

        for path, text in strings_with_paths:
            if not text.strip():
                structure_map.append({'path': path, 'type': 'empty', 'original': text})
                continue

            if '<' not in text and '>' not in text:
                structure_map.append({'path': path, 'type': 'plain', 'index': len(texts_to_translate)})
                texts_to_translate.append(text)
            else:
                soup = BeautifulSoup(text, 'html.parser')
                nodes = []
                for text_node in soup.find_all(string=True):
                    if isinstance(text_node, NavigableString) and text_node.parent.name not in ['script', 'style']:
                        stripped = str(text_node).strip()
                        if stripped:
                            nodes.append({
                                'node': text_node,
                                'index': len(texts_to_translate)
                            })
                            texts_to_translate.append(stripped)
                structure_map.append({
                    'path': path,
                    'type': 'html',
                    'soup': soup,
                    'nodes': nodes,
                    'original': text
                })

        # 2. Translation Phase
        translated_texts = []
        batch_size = 100
        for i in range(0, len(texts_to_translate), batch_size):
            batch = texts_to_translate[i:i+batch_size]
            print(f"  Translating chunk {i//batch_size + 1}/{(len(texts_to_translate) + batch_size - 1)//batch_size}...", flush=True)
            try:
                t_batch = translator.translate_batch(batch)
                translated_texts.extend(t_batch)
                time.sleep(0.5)
            except Exception as e:
                print(f"Batch translation failed for a chunk: {e}. Falling back to single translations.", flush=True)
                for text in batch:
                    try:
                        res = translator.translate(text)
                        translated_texts.append(res)
                        time.sleep(0.5)
                    except:
                        translated_texts.append(text)

        # 3. Reconstruction Phase
        new_data = json.loads(json.dumps(en_data))

        for item in structure_map:
            if item['type'] == 'empty':
                set_value(new_data, item['path'], item['original'])
            elif item['type'] == 'plain':
                t_text = translated_texts[item['index']]
                set_value(new_data, item['path'], t_text if t_text else "")
            elif item['type'] == 'html':
                soup = item['soup']
                for node_info in item['nodes']:
                    t_text = translated_texts[node_info['index']]
                    if t_text is None:
                        t_text = str(node_info['node']).strip()
                    original_str = str(node_info['node'])
                    prefix = original_str[:len(original_str)-len(original_str.lstrip())]
                    suffix = original_str[len(original_str.rstrip()):]
                    node_info['node'].replace_with(prefix + t_text + suffix)

                set_value(new_data, item['path'], str(soup))

        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        print(f"Saved {target_lang}.json", flush=True)

for d in dirs:
    process_directory(d)

print("\nTranslation completed!", flush=True)
