import os
import json

# Dictionnaire complet de toutes les langues de votre dossier
translations = {
    "da": "Bygget til soloradiologer, der bruger for lang tid på beskrivelser. Gratis abonnement inkluderet — intet kreditkort.",
    "de": "Entwickelt für Einzelradiologen, die zu viel Zeit mit Befunden verbringen. Kostenloser Tarif enthalten — keine Kreditkarte.",
    "el": "Σχεδιασμένο για ακτινολόγους που αφιερώνουν πολύ χρόνο στις αναφορές. Περιλαμβάνεται δωρεάν πρόγραμμα — χωρίς πιστωτική κάρτα.",
    "en": "Built for solo radiologists who spend too long on reports. Free plan included — no credit card.",
    "es": "Diseñado para radiólogos independientes que pasan demasiado tiempo en sus informes. Plan gratuito incluido — sin tarjeta de crédito.",
    "fr": "Conçu pour les radiologues exerçant seuls qui passent trop de temps sur leurs compte-rendus. Plan gratuit inclus — sans carte de crédit.",
    "hi": "उन व्यक्तिगत रेडियोलॉजिस्टों के लिए बनाया गया जो रिपोर्ट में बहुत अधिक समय बिताते हैं। मुफ़्त प्लान शामिल — कोई क्रेडिट कार्ड नहीं।",
    "id": "Dibuat untuk radiolog mandiri yang menghabiskan terlalu banyak waktu untuk laporan. Paket gratis termasuk — tanpa kartu kredit.",
    "it": "Progettato per radiologi individuali che dedicano troppo tempo ai referti. Piano gratuito incluso — senza carta di credito.",
    "ja": "レポート作成に時間をかけすぎている個人放射線科医のために構築。無料プラン付き — クレジットカード不要。",
    "ko": "판독서 작성에 너무 많은時間を 쏟는 개인 방사선과 의사를 위해 제작되었습니다. 무료 플랜 포함 — 신용카드 필요 없음.",
    "ms": "Dibina untuk pakar radiologi solo yang menghabiskan terlalu banyak masa untuk laporan. Pelan percuma disertakan — tiada kad kredit.",
    "nl": "Gemaakt voor solo-radiologen die te veel tijd besteden aan verslagen. Gratis abonnement inbegrepen — geen creditcard.",
    "no": "Bygget for soloradiologer som bruker for lang tid på rapporter. Gratis plan inkludert — ingen kredittkort.",
    "pl": "Stworzone dla radiologów indywidualnych, którzy spędzają zbyt wiele czasu na opisach. Darmowy plan w cenie — bez karty kredytowej.",
    "pt": "Criado para radiologistas individuais que passam muito tempo em relatórios. Plano gratuito incluído — sem cartão de crédito.",
    "ro": "Creat pentru radiologii independenți care petrec prea mult timp cu rapoartele. Plan gratuit inclus — fără card de credit.",
    "ru": "Создано для частных радиологов, тратящих слишком много времени на отчеты. Бесплатный тариф включен — без кредитной карты.",
    "sv": "Byggd för soloradiologer som tillbringar för mycket tid på rapporter. Gratis plan ingår — inget kreditkort.",
    "th": "สร้างขึ้นสำหรับรังสีแพทย์เดี่ยวที่ใช้เวลากับรายงานนานเกินไป มีแพ็กเกจฟรี — ไม่ต้องใช้บัตรเครดิต",
    "tl": "Binuo para sa mga solo radiologist na masyadong matagal sa mga ulat. Kasama ang libreng plan — walang kinakailangang credit card.",
    "tr": "Raporlara çok fazla zaman harcayan bağımsız radyologlar için tasarlandı. Ücretsiz plan dahil — kredi kartı gerekmez.",
    "zh": "专为在报告上花费太多时间的独立放射科医生打造。包含免费计划 — 无需信用卡。"
}

default_text = translations["en"]
folder_path = os.path.dirname(os.path.abspath(__file__))

count = 0
for filename in os.listdir(folder_path):
    if filename.endswith(".json"):
        filepath = os.path.join(folder_path, filename)
        
        # Extrait le code langue du nom de fichier (ex: fr.json -> fr, .en_baseline.json -> en)
        lang_code = filename.replace(".json", "").replace(".en_baseline", "en")
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if "index" in data and "p_53" in data["index"]:
                new_val = translations.get(lang_code, default_text)
                data["index"]["p_53"] = new_val
                
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                print(f"✅ Mis à jour : {filename} ({lang_code})")
                count += 1
        except Exception as e:
            print(f"❌ Erreur sur {filename}: {e}")

print(f"\nTerminé ! {count} fichiers de langues ont été mis à jour.")