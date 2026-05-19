import os
import re
import json
from bs4 import BeautifulSoup

# Directories containing your HTML files
HTML_DIRS = [
    r"c:\Users\walha\Desktop\PISUM\LOGICIEL\PISUM\Logiciel Pisum V2.9.8.2\docs",
    r"c:\Users\walha\Desktop\PISUM\LOGICIEL\PISUM\Logiciel Pisum V2.9.8.2\docs\saas\frontend"
]

def set_nested_value(d, keys, value):
    """Safely build nested dictionary paths and set the value."""
    for key in keys[:-1]:
        if key not in d or not isinstance(d[key], dict):
            d[key] = {}
        d = d[key]
    
    # Only set the value if it's missing or empty so we don't overwrite manual edits
    if keys[-1] not in d or not d[keys[-1]]:
        d[keys[-1]] = value.strip() if value else ""

def generate_en_json():
    for html_dir in HTML_DIRS:
        en_json_path = os.path.join(html_dir, "translations", "en.json")
        en_data = {}
        
        # Load existing en.json to preserve already defined structures
        if os.path.exists(en_json_path):
            with open(en_json_path, "r", encoding="utf-8") as f:
                try:
                    en_data = json.load(f)
                except json.JSONDecodeError:
                    en_data = {}

        # Scan all HTML files in the directory
        for filename in os.listdir(html_dir):
            if not filename.endswith(".html"):
                continue
            
            filepath = os.path.join(html_dir, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f, "html.parser")
            
            # Find all elements with the 'data-i18n' attribute
            for el in soup.find_all(attrs={"data-i18n": True}):
                raw_key = el["data-i18n"]
                
                # Mirror the JS logic: Check for attribute tags like [placeholder]key.path
                attr_match = re.match(r"^\[(.*?)\](.*)$", raw_key)
                if attr_match:
                    target_attr = attr_match.group(1)
                    key_path = attr_match.group(2)
                    value = el.get(target_attr, "")
                else:
                    key_path = raw_key
                    tag_name = el.name.lower() if el.name else ""
                    
                    if tag_name in ['input', 'textarea']:
                        if el.get('type') in ['button', 'submit']:
                            value = el.get('value', "")
                        else:
                            value = el.get('placeholder', "")
                    else:
                        value = el.decode_contents() # gets inner HTML

                # Split the key (e.g., 'hero.title' -> ['hero', 'title']) and set it
                keys = key_path.split('.')
                set_nested_value(en_data, keys, value)

        # Save the constructed dictionary back to en.json
        os.makedirs(os.path.dirname(en_json_path), exist_ok=True)
        with open(en_json_path, "w", encoding="utf-8") as f:
            json.dump(en_data, f, indent=2, ensure_ascii=False)
        
        print(f"Successfully generated/updated: {en_json_path}")

if __name__ == "__main__":
    generate_en_json()