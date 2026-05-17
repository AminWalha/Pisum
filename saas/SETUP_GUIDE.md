# PISUM SaaS — Guide de configuration complet

## Structure des fichiers

```
saas/
├── backend/
│   ├── main.py               ← API FastAPI (tous les endpoints)
│   └── requirements.txt
├── frontend/
│   ├── auth.html             ← Page de connexion / inscription
│   ├── dashboard.html        ← Gestion de l'abonnement
│   └── pricing.html          ← Page de tarifs publique
├── desktop_check/
│   └── subscription_check.py ← À intégrer dans l'app bureau
├── supabase_schema.sql       ← À exécuter une fois dans Supabase
├── .env.example              ← Copier en .env et remplir
└── SETUP_GUIDE.md            ← Ce fichier
```

---

## Plans disponibles

| Plan | Stripe | Templates | Dictée AI | AI Enhancer | Worklist | CR/mois |
|---|---|---|---|---|---|---|
| **Free** | — (gratuit) | 10 | ✗ | ✗ | ✗ | 20 |
| **Starter** | `STRIPE_PRICE_ID_STARTER` | 20 | ✗ | ✗ | Basique | ∞ |
| **Pro** | `STRIPE_PRICE_ID_PRO` | 112+ | ✓ | 100/mois | Complète | ∞ |
| **Expert** | `STRIPE_PRICE_ID_EXPERT` | 112+ custom | ✓ | Illimité | Avancée | ∞ |
| **Clinic** | `STRIPE_PRICE_ID_CLINIC` | Sur mesure | ✓ | Illimité | Multi-site | ∞ |

---

## Étape 1 — Supabase

1. Allez sur https://app.supabase.com → ouvrez votre projet
2. **SQL Editor** → New query → collez le contenu de `supabase_schema.sql` → **Run**
3. **Settings → API** → copiez :
   - Project URL → `SUPABASE_URL`
   - `anon public` key → `SUPABASE_ANON_KEY`
   - `service_role` key → `SUPABASE_SERVICE_ROLE_KEY` *(ne jamais exposer côté frontend !)*
   - JWT Secret → `SUPABASE_JWT_SECRET`

> **Si la table `subscriptions` existe déjà**, exécutez uniquement la section **Migration** du fichier SQL (les `ALTER TABLE ADD COLUMN IF NOT EXISTS`).

---

## Étape 2 — Stripe (4 produits à créer)

### 2a — Créer les 4 produits

1. Allez sur https://dashboard.stripe.com → **Products** → **Add product**
2. Créez **4 produits** séparément :

| Produit | Nom suggéré | Type |
|---|---|---|
| Starter | Pisum Starter | Abonnement récurrent |
| Pro | Pisum Pro | Abonnement récurrent |
| Expert | Pisum Expert | Abonnement récurrent |
| Clinic | Pisum Clinic | Abonnement récurrent |

3. Pour chaque produit → copiez le **Price ID** (commence par `price_`) dans votre `.env` :

```env
STRIPE_PRICE_ID_STARTER=price_xxxxxxxxxxxxxxxx
STRIPE_PRICE_ID_PRO=price_xxxxxxxxxxxxxxxx
STRIPE_PRICE_ID_EXPERT=price_xxxxxxxxxxxxxxxx
STRIPE_PRICE_ID_CLINIC=price_xxxxxxxxxxxxxxxx
```

### 2b — Clé API

**Developers → API Keys** → copiez la **Secret key** → `STRIPE_SECRET_KEY`

### 2c — Webhook

**Developers → Webhooks** → **Add endpoint** :
- URL : `https://votre-backend.onrender.com/webhook`
- Événements à écouter :
  - `checkout.session.completed`
  - `invoice.payment_succeeded`
  - `invoice.payment_failed`
  - `customer.subscription.deleted`

→ Copiez le **Signing secret** → `STRIPE_WEBHOOK_SECRET`

---

## Étape 3 — Déploiement backend (Render)

1. Poussez le dossier `saas/backend/` sur un repo GitHub
2. Allez sur https://render.com → **New → Web Service** → connectez votre repo
3. Configurez :
   - **Build command** : `pip install -r requirements.txt`
   - **Start command** : `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Dans **Environment** → ajoutez toutes les variables de `.env.example` avec vos vraies valeurs
5. Copiez votre URL Render (ex : `https://pisum-api.onrender.com`) → vous en aurez besoin à l'étape 4

### Alternative : Railway

```bash
railway login
railway init
railway up
```

---

## Étape 4 — Configuration du frontend

Dans **`frontend/auth.html`** et **`frontend/dashboard.html`**, remplacez :

```js
const SUPABASE_URL  = 'https://YOUR_PROJECT.supabase.co';
const SUPABASE_ANON = 'YOUR_ANON_KEY';
const API_BASE      = 'https://your-backend.onrender.com';
```

Dans **`pricing.html`**, remplacez également les `— €` par vos vrais prix :

```html
<div class="plan-price">29 €<span> / mois</span></div>
```

---

## Étape 5 — Intégration dans l'app bureau

Dans votre `main.py` bureau, au tout début (avant le lancement de l'UI) :

```python
import sys
from saas.desktop_check.subscription_check import check_subscription_access

if not check_subscription_access():
    sys.exit(0)

# ... suite du démarrage de l'app ...
```

Dans `desktop_check/subscription_check.py`, renseignez :

```python
API_BASE_URL  = "https://votre-backend.onrender.com"
SUPABASE_URL  = "https://YOUR_PROJECT.supabase.co"
SUPABASE_ANON = "YOUR_ANON_KEY"
```

Le `check_subscription_access()` retourne aussi le plan actif — vous pouvez le passer au `LicenseManager` :

```python
result = check_subscription_access()
if not result:
    sys.exit(0)

# result = {"access": True, "plan": "pro", "features": {...}}
from pisum_license_manager import LicenseManager
lm = LicenseManager()
lm._plan_name = result.get("plan", "free")
lm._features  = result.get("features", {})
lm._is_active = True
```

### Avec dialog tkinter au lieu du terminal

Décommentez `tk_prompt` dans `subscription_check.py` et appelez :

```python
check_subscription_access(prompt_fn=tk_prompt)
```

---

## Étape 6 — Utiliser les feature gates dans le code

Le `LicenseManager` (`pisum_license_manager.py`) expose ces méthodes :

```python
from pisum_license_manager import LicenseManager
lm = LicenseManager()

# Vérifier si une feature est accessible
ok, msg = lm.can_create_report()          # vérifie limite mensuelle (Free)
ok, msg = lm.can_dictate()                # vérifie si dictée AI incluse
ok, msg = lm.can_use_ai_enhancer()        # vérifie limite mensuelle AI Enhancer
ok, msg = lm.can_use_word_export()        # Word disponible à partir de Starter
ok, msg = lm.can_create_custom_template() # disponible à partir de Expert

# Niveau de worklist
level = lm.can_use_worklist()
# → False | "basic" | "full" | "advanced" | "multisite"

# Niveau de stats
level = lm.can_use_stats()
# → False | "basic" | True | "advanced"

# Limites de templates et langues
max_tpl  = lm.get_templates_limit()   # -1 = illimité
max_lang = lm.get_languages_limit()   # ex: 2, 5, 23

# Incrémenter les compteurs après usage
lm.increment_report_count()           # après création d'un CR
lm.increment_ai_enhancer_count()      # après chaque usage AI Enhancer
lm.increment_dictation_minutes(3)     # après dictée (en minutes)

# Consulter les restants
remaining = lm.ai_enhancer_remaining()   # int | "∞"
```

---

## Étape 7 — Test du flux complet

1. Ouvrez `saas/frontend/auth.html` dans un navigateur
2. Créez un compte → confirmez l'email
3. Connectez-vous → vous arrivez sur `dashboard.html`
4. **Plan Free** : cliquez "Activer gratuitement" → accès immédiat sans Stripe
5. **Plan payant** : sélectionnez un plan → "S'abonner" → redirection Stripe Checkout
6. Carte de test Stripe : `4242 4242 4242 4242`, date future, n'importe quel CVC
7. Après paiement : Stripe envoie le webhook → backend met à jour Supabase → dashboard affiche "Accès accordé" avec le badge du plan
8. Lancez l'app bureau → login → `/check-access` → l'app démarre avec les bonnes features

### Tester les webhooks en local (Stripe CLI)

```bash
stripe listen --forward-to localhost:8000/webhook
```

---

## Endpoints API disponibles

| Méthode | Endpoint | Description |
|---|---|---|
| `POST` | `/activate-free` | Active le plan Free (sans Stripe) |
| `POST` | `/create-checkout-session` | Crée une session Stripe Checkout |
| `POST` | `/webhook` | Reçoit les événements Stripe |
| `GET` | `/check-access` | Vérifie l'accès + retourne le plan et les features |
| `GET` | `/check-feature?feature=ai_enhancer` | Vérifie une feature spécifique + compteur mensuel |
| `POST` | `/use-ai-enhancer` | Incrémente le compteur AI Enhancer (côté serveur) |
| `GET` | `/` | Health check |

---

## Checklist de sécurité

- [x] `SUPABASE_SERVICE_ROLE_KEY` uniquement dans le `.env` backend — jamais dans le HTML
- [x] Signature webhook Stripe vérifiée dans `/webhook`
- [x] Tous les endpoints protégés requièrent un JWT Supabase valide
- [x] JWT vérifié via `SUPABASE_JWT_SECRET` (pas seulement décodé)
- [x] Token desktop stocké dans le dossier temp OS, pas dans le dossier app
- [x] CORS restreint à `FRONTEND_URL` uniquement
- [x] Plan Free sans Stripe — aucune donnée bancaire collectée

---

## Problèmes fréquents

| Problème | Solution |
|---|---|
| `401 Invalid token` | Vérifiez que `SUPABASE_JWT_SECRET` correspond bien au secret de votre projet |
| Webhook `400 Invalid Stripe signature` | Assurez-vous d'utiliser le raw body (FastAPI le fait automatiquement) |
| `CORS error` | Définissez `FRONTEND_URL` dans `.env` avec l'origine exacte de votre frontend |
| Desktop : `Network error` | Vérifiez `API_BASE_URL` dans `subscription_check.py` |
| Webhook ne se déclenche pas en local | Utilisez Stripe CLI : `stripe listen --forward-to localhost:8000/webhook` |
| Plan Free pas activé après inscription | L'utilisateur doit cliquer "Activer gratuitement" sur le dashboard, ou appelez `/activate-free` automatiquement au 1er login |
| `KeyError: STRIPE_PRICE_ID_STARTER` | Vérifiez que les 4 variables `STRIPE_PRICE_ID_*` sont bien dans votre `.env` |
