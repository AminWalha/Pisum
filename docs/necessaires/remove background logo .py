from rembg import remove
from PIL import Image

def supprimer_fond(input_path, output_path):
    try:
        # Ouvrir l'image
        img = Image.open(input_path)
        
        # Supprimer l'arrière-plan (IA)
        resultat = remove(img)
        
        # Sauvegarder en PNG pour la transparence
        # PNG est un format "lossless" (sans perte de qualité)
        resultat.save(output_path)
        
        print(f"Terminé : {output_path}")
        
    except Exception as e:
        print(f"Erreur : {e}")

# Exemple d'utilisation
supprimer_fond("logo V2.png", "image_sans_fond.png")