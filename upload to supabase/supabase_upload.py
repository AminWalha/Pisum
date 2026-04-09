"""
supabase_upload.py — Upload des modèles vers Supabase
======================================================
1. Lancez d'abord "csv firebase.py" pour générer models_final.csv
2. Exécutez ce script pour pousser le CSV vers Supabase

Table SQL à créer dans Supabase (Dashboard → SQL Editor) :
------------------------------------------------------------
CREATE TABLE templates (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    language    TEXT NOT NULL,
    category    TEXT NOT NULL,
    exam_type   TEXT NOT NULL,
    name        TEXT NOT NULL,
    content     TEXT,
    plan        TEXT NOT NULL DEFAULT 'pro',
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index pour accélérer les requêtes de l'app
CREATE INDEX idx_templates_lang_plan ON templates(language, plan);

-- NOUVEAU : Contrainte UNIQUE indispensable pour utiliser l'UPSERT
ALTER TABLE templates ADD CONSTRAINT unique_template UNIQUE (language, category, exam_type, name, plan);

-- Politique RLS : lecture publique (anon peut lire)
ALTER TABLE templates ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Lecture publique" ON templates FOR SELECT USING (true);
------------------------------------------------------------

IMPORTANT : pour l'upload, utilisez la clé service_role (pas anon).
Récupérez-la dans Supabase Dashboard → Settings → API → service_role (secret).
"""

import pandas as pd
import requests
import json
import sys
import os

# ── Configuration ──────────────────────────────────────────────────────────────
SUPABASE_URL  = "https://lepqbnhrdgfetoysedbq.supabase.co"
# ⚠ Remplacez par votre clé SERVICE_ROLE (Settings → API → service_role)
SERVICE_ROLE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImxlcHFibmhyZGdmZXRveXNlZGJxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTA2NDUwMCwiZXhwIjoyMDkwNjQwNTAwfQ.uUnRftUM7XBbm-JBBSHD2Qjivx3ElAGGL8EgWtdWlgo"
TABLE         = "templates"
CSV_PATH = os.path.join(os.path.dirname(__file__), "models_final.csv")
BATCH_SIZE    = 500   # lignes par requête (limite Supabase ≈ 1000)
# ───────────────────────────────────────────────────────────────────────────────


def upload_batch(rows: list, headers: dict) -> bool:
    """Envoie un lot de données en utilisant la méthode UPSERT."""
    url = f"{SUPABASE_URL}/rest/v1/{TABLE}"
    params = {"on_conflict": "language,category,exam_type,name,plan"}
    resp = requests.post(
        url,
        headers={**headers, "Prefer": "resolution=merge-duplicates"},
        params=params,
        json=rows,
        timeout=30,
    )
    if resp.status_code in (200, 201):
        return True
    print(f"  ❌ Erreur HTTP {resp.status_code}: {resp.text[:300]}")
    return False


def main():
    if SERVICE_ROLE_KEY == "VOTRE_CLE_SERVICE_ROLE_ICI":
        print("❌ Renseignez SERVICE_ROLE_KEY dans ce fichier avant de lancer l'upload.")
        sys.exit(1)

    print(f"📂 Lecture de {CSV_PATH}...")
    try:
        df = pd.read_csv(CSV_PATH, encoding="utf-8-sig")
    except FileNotFoundError:
        print(f"❌ Fichier introuvable : {CSV_PATH}")
        print("   Lancez d'abord 'csv firebase.py' pour générer le CSV.")
        sys.exit(1)

    print(f"📊 {len(df)} modèles à uploader")

    # Vérifier les colonnes attendues
    required = {"name", "content", "language", "category", "exam_type", "plan"}
    missing = required - set(df.columns)
    if missing:
        print(f"❌ Colonnes manquantes dans le CSV : {missing}")
        sys.exit(1)

    # Nettoyer les NaN
    df = df.fillna("")

    headers = {
        "apikey":        SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "Content-Type":  "application/json",
    }

    # ── Étape 1 : Insérer/Mettre à jour les modèles (UPSERT) ───────────────────
    records = df.to_dict(orient="records")
    total   = len(records)
    ok      = 0
    errors  = 0

    print(f"\n🚀 Upsert (Mise à jour/Insertion) par batches de {BATCH_SIZE}...\n")

    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"  Batch {batch_num} ({i+1}–{min(i+BATCH_SIZE, total)}/{total})...", end=" ")

        if upload_batch(batch, headers):
            print("✅")
            ok += len(batch)
        else:
            errors += len(batch)

    print(f"\n{'='*50}")
    print(f"✅ Uploadés  : {ok}")
    print(f"❌ Erreurs   : {errors}")
    print(f"{'='*50}")

    if errors == 0:
        print("\n🎉 Upload terminé avec succès !")
    else:
        print("\n⚠ Certains enregistrements ont échoué. Vérifiez les logs ci-dessus.")


if __name__ == "__main__":
    main()
