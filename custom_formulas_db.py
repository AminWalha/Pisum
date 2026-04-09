import sqlite3
import os
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class CustomFormulasDB:
    """Gestionnaire de formules personnalisées avec SQLite"""
    
    def __init__(self):
        # Créer le dossier de données s'il n'existe pas
        self.db_folder = Path.home() / ".pisum_data"
        self.db_folder.mkdir(exist_ok=True)
        self.db_path = self.db_folder / "custom_formulas.db"
        self.init_database()
    
    def init_database(self):
        """Initialise la base de données"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Créer la table des formules personnalisées
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS custom_formulas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    modality TEXT NOT NULL,
                    exam_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    formula_content TEXT NOT NULL,
                    language TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(modality, exam_type, title, language)
                )
            ''')
            
            # Index pour la recherche rapide
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_modality_examtype 
                ON custom_formulas(modality, exam_type)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_language 
                ON custom_formulas(language)
            ''')
            
            conn.commit()
            conn.close()
            logger.info(f"Base de données initialisée: {self.db_path}")
        except Exception as e:
            logger.error(f"Erreur initialisation DB: {e}", exc_info=True)
    
    def add_formula(self, modality, exam_type, title, formula_content, language):
        """Ajoute ou met à jour une formule personnalisée"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT OR REPLACE INTO custom_formulas 
                (modality, exam_type, title, formula_content, language, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (modality, exam_type, title, formula_content, language))
            
            conn.commit()
            conn.close()
            logger.info(f"Formule ajoutée: {modality} - {exam_type} - {title}")
            return True
        except Exception as e:
            logger.error(f"Erreur ajout formule: {e}", exc_info=True)
            return False
    
    def get_formulas_by_language(self, language):
        """Récupère toutes les formules pour une langue"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM custom_formulas 
                WHERE language = ?
                ORDER BY modality, exam_type, title
            ''', (language,))
            
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.error(f"Erreur récupération formules: {e}", exc_info=True)
            return []
    
    def update_formula(self, formula_id, modality, exam_type, title, formula_content, language):
        """Met à jour une formule existante"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE custom_formulas
                SET modality=?, exam_type=?, title=?, formula_content=?,
                    language=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            ''', (modality, exam_type, title, formula_content, language, formula_id))
            conn.commit()
            conn.close()
            logger.info(f"Formule mise à jour: ID {formula_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur mise à jour formule: {e}", exc_info=True)
            return False

    def delete_formula(self, formula_id):
        """Supprime une formule"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute('DELETE FROM custom_formulas WHERE id = ?', (formula_id,))
            conn.commit()
            conn.close()
            logger.info(f"Formule supprimée: ID {formula_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur suppression formule: {e}", exc_info=True)
            return False
    
    def get_all_formulas(self):
        """Récupère toutes les formules toutes langues confondues"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM custom_formulas
                ORDER BY language, modality, exam_type, title
            ''')
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.error(f"Erreur récupération formules: {e}", exc_info=True)
            return []

    def search_formulas(self, search_term, language=None):
        """Recherche dans les formules personnalisées"""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            search_pattern = f"%{search_term}%"
            
            if language:
                cursor.execute('''
                    SELECT * FROM custom_formulas 
                    WHERE language = ? AND (
                        modality LIKE ? OR 
                        exam_type LIKE ? OR 
                        title LIKE ? OR 
                        formula_content LIKE ?
                    )
                ''', (language, search_pattern, search_pattern, search_pattern, search_pattern))
            else:
                cursor.execute('''
                    SELECT * FROM custom_formulas 
                    WHERE modality LIKE ? OR 
                          exam_type LIKE ? OR 
                          title LIKE ? OR 
                          formula_content LIKE ?
                ''', (search_pattern, search_pattern, search_pattern, search_pattern))
            
            results = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return results
        except Exception as e:
            logger.error(f"Erreur recherche formules: {e}", exc_info=True)
            return []
