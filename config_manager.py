# -*- coding: utf-8 -*-
"""
Configuration Manager pour sauvegarder les préférences utilisateur
Auteur: Configuration persistante pour PISUM
Description: Sauvegarde automatique des champs etablissement et medecin
"""

import json
import os
import threading
from pathlib import Path


class ConfigManager:
    """Gestionnaire de configuration pour sauvegarder les préférences utilisateur"""

    def __init__(self, config_filename="user_config.json"):
        """
        Initialise le gestionnaire de configuration

        Args:
            config_filename: Nom du fichier de configuration (par défaut: user_config.json)
        """
        # Créer le fichier dans le dossier du programme
        self.config_file = Path(config_filename)
        self.config = self.load_config()
        self._save_timer: threading.Timer | None = None

    def load_config(self):
        """
        Charge la configuration depuis le fichier JSON

        Returns:
            dict: Configuration chargée ou dictionnaire vide si erreur
        """
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Erreur lors du chargement de la configuration: {e}")
                return {}
        return {}

    def save_config(self):
        """Sauvegarde la configuration dans le fichier JSON"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Erreur lors de la sauvegarde de la configuration: {e}")

    def get(self, key, default=''):
        """
        Récupère une valeur de configuration

        Args:
            key: Clé de configuration à récupérer
            default: Valeur par défaut si la clé n'existe pas

        Returns:
            Valeur de la configuration ou valeur par défaut
        """
        return self.config.get(key, default)

    def set(self, key, value):
        """
        Définit une valeur de configuration et planifie une sauvegarde différée.
        Les appels groupés ne déclenchent qu'une seule écriture disque (débounce 500ms).

        Args:
            key: Clé de configuration à définir
            value: Valeur à sauvegarder
        """
        self.config[key] = value
        # Annuler le timer précédent si plusieurs set() arrivent rapidement
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.5, self.save_config)
        self._save_timer.daemon = True
        self._save_timer.start()
