# -*- coding: utf-8 -*-
"""
Configuration partagée pour PISUM
Évite les imports circulaires entre cloud_license_client et Comptes_Rendus
"""

import os
import json
import tempfile
import datetime

LANGUAGE_STORE = os.path.join(tempfile.gettempdir(), 'pisum_language_store.json')

class LanguageStore:
    """Gestion du stockage de la langue sélectionnée"""

    def __init__(self, storefile=LANGUAGE_STORE):
        self.storefile = storefile

    def save_language(self, language):
        """Sauvegarde la langue sélectionnée"""
        try:
            data = {
                'language': language,
                'saved_at': datetime.datetime.now().isoformat()
            }
            with open(self.storefile, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            print(f"📂 Langue sauvegardée: {language}")
            return True
        except Exception as e:
            print(f"📂 Erreur sauvegarde langue: {e}")
            return False

    def load_language(self):
        """Charge la langue sélectionnée"""
        try:
            if os.path.exists(self.storefile):
                with open(self.storefile, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    language = data.get('language')
                    if language:
                        print(f"📂 Langue chargée: {language}")
                        return language
        except Exception as e:
            print(f"📂 Erreur chargement langue: {e}")
        return None

def get_selected_language():
    """Récupère la langue sélectionnée"""
    lang_store = LanguageStore()
    return lang_store.load_language()

def save_selected_language(language):
    """Sauvegarde la langue sélectionnée"""
    lang_store = LanguageStore()
    return lang_store.save_language(language)
