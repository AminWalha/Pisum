import os
import tempfile
from pathlib import Path

def reset_pisum_data():
    print("=========================================================")
    print("⚠️  ATTENTION : RÉINITIALISATION DE LA LICENCE ET LANGUE")
    print("=========================================================")
    print("Ce script va supprimer la licence en cours et la préférence de langue.")
    print("Vos bases de données PACS (patients, examens) seront CONSERVÉES.")
    print("=========================================================\n")
    
    confirm = input("Voulez-vous continuer ? (O/N) : ")
    
    if confirm.strip().lower() != 'o':
        print("Annulé.")
        return

    # Dossier temporaire où sont stockées la licence et la langue
    temp_dir = Path(tempfile.gettempdir())
    
    files_to_delete = [
        "pisum_license_store.json",
        "pisum_language_store.json",
        "pisum_lic_v3.json",
        "pisum_timeguard.json"
    ]

    print(f"\n[1] Nettoyage du cache (licence et langue) dans ({temp_dir})...")
    for filename in files_to_delete:
        file_path = temp_dir / filename
        if file_path.exists():
            try:
                file_path.unlink()
                print(f"  ✅ Supprimé : {filename}")
            except Exception as e:
                print(f"  ❌ Erreur ({filename}) : {e}")
        else:
            print(f"  ℹ️  Introuvable (déjà propre) : {filename}")

    print("\n✨ Réinitialisation terminée ! Au prochain lancement, le logiciel vous demandera votre langue et votre licence.")

if __name__ == "__main__":
    reset_pisum_data()