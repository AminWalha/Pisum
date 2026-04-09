# -*- coding: utf-8 -*-
"""
supabase_templates_loader.py — Chargement des modèles depuis Supabase
======================================================================
Remplace le chargement depuis les fichiers Excel locaux (.xlsx.enc).
Utilise l'API REST Supabase via requests (aucune dépendance supplémentaire).

Structure retournée (identique à load_excel_data) :
    {
        "IRM":     { "Thorax": {"data": DataFrame, "plans": DataFrame}, ... },
        "Scanner": { "Thorax": {"data": DataFrame, "plans": DataFrame}, ... },
        ...
    }
"""

import logging
import requests
import pandas as pd

logger = logging.getLogger(__name__)

SUPABASE_URL      = "https://lepqbnhrdgfetoysedbq.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxlcHFibmhyZGdmZXRveXNlZGJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTA2NDUwMCwiZXhwIjoyMDkwNjQwNTAwfQ.uUnRftUM7XBbm-JBBSHD2Qjivx3ElAGGL8EgWtdWlgo"
TABLE = "templates"

# Cache session — évite de requêter Supabase à chaque changement de langue
_cache: dict = {}

def normalize_plan(plan):
    if not plan: return "free"
    p_str = str(plan).lower()
    if 'free' in p_str: return 'free'
    if 'solo' in p_str: return 'solo'
    if 'pro' in p_str: return 'pro'
    if 'clinic' in p_str: return 'clinic'
    return 'free'


def _fetch_rows(language: str) -> list:
    """Interroge Supabase et retourne les lignes brutes."""
    headers = {
        "apikey":        SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type":  "application/json",
    }

    params = {
        "language": f"eq.{language}",
        "select":   "category,exam_type,name,content,plan",
        "limit":    "10000",
    }

    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning("[Supabase] Timeout — pas de connexion ?")
        return []
    except Exception as e:
        logger.error(f"[Supabase] Erreur requête : {e}")
        return []


def _build_data(rows: list, user_plan: str) -> dict:
    """
    Construit le dict data attendu par l'application.

    Entrée  : liste de dicts {category, exam_type, name, content}
    Sortie  : {category: {exam_type: {"data": DataFrame, "plans": DataFrame}}}
    """
    grouped: dict = {}
    logger.info(f"[Supabase] Fetched {len(rows)} raw templates from database")

    PLAN_ORDER = {"free": 0, "solo": 2, "pro": 2, "clinic": 3}
    user_order = PLAN_ORDER.get(user_plan, 0)

    for row in rows:
        cat   = (row.get("category") or "").strip()
        exam  = (row.get("exam_type") or "").strip()
        title = (row.get("name") or "").strip()
        text  = row.get("content") or ""
        plan  = normalize_plan(row.get("plan"))
        
        if not cat or not exam or not title:
            continue

        template_order = PLAN_ORDER.get(plan, 0)
        current_entry = grouped.setdefault(cat, {}).setdefault(exam, {}).get(title)
        
        if current_entry:
            current_order = PLAN_ORDER.get(current_entry["plan"], 0)
            new_is_accessible = template_order <= user_order
            current_is_accessible = current_order <= user_order
            
            if new_is_accessible and not current_is_accessible:
                # On remplace par la version que l'utilisateur peut utiliser
                grouped[cat][exam][title] = {"content": text, "plan": plan}
            elif new_is_accessible and current_is_accessible:
                # L'utilisateur a accès aux deux, on préfère la version supérieure
                if template_order > current_order:
                    grouped[cat][exam][title] = {"content": text, "plan": plan}
            elif not new_is_accessible and not current_is_accessible:
                # Inaccessible : on choisit le plan inférieur le plus proche
                if template_order < current_order:
                    grouped[cat][exam][title] = {"content": text, "plan": plan}
        else:
            grouped[cat][exam][title] = {
                "content": text,
                "plan": plan
            }

    data: dict = {}
    for cat, exams in grouped.items():
        data[cat] = {}
        for exam, titles_dict in exams.items():
            titles   = list(titles_dict.keys())
            contents = [titles_dict[t]["content"] for t in titles]
            plans    = [titles_dict[t]["plan"] for t in titles]
            
            df_data = pd.DataFrame([contents], columns=titles)
            df_plan = pd.DataFrame([plans], columns=titles)
            
            data[cat][exam] = {
                "data": df_data,
                "plans": df_plan
            }

    return data


def load_templates(language: str, user_plan: str = "free") -> dict:
    """
    Point d'entrée principal pour charger les modèles.
    Utilise un cache en mémoire pour éviter les requêtes répétées.
    """
    cache_key = f"{language}_{user_plan}"
    if cache_key in _cache:
        logger.info(f"[Supabase] Utilisation du cache pour {language} (plan: {user_plan})")
        return _cache[cache_key]

    rows = _fetch_rows(language)
    data = _build_data(rows, user_plan) if rows else {}
    
    _cache[cache_key] = data
    return data

def clear_cache():
    """Vide le cache des modèles (utile après un changement de licence)."""
    _cache.clear()