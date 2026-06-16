# Soléo — Backend

Calculateur solaire grand public / PME (Côte d'Ivoire).
Rapport premium en **paiement manuel** : l'utilisateur paie sur un numéro mobile money,
envoie sa preuve via WhatsApp/email, et l'admin valide depuis un panneau dédié.
👉 Aucune registre de commerce ni passerelle de paiement requis pour démarrer.

## Structure

```
soleo_backend/
├── app.py                # API Flask (leads + paiement manuel + admin)
├── static/index.html     # Frontend (le calculateur)
├── static/admin.html     # Panneau admin (validation des paiements)
├── requirements.txt
├── Procfile / runtime.txt / render.yaml
├── .env.example
└── README.md
```

## Comment marche le paiement (manuel)

1. L'utilisateur clique « Obtenir mon rapport » → reçoit une **référence** (ex. `SOLEO-4F2A1C`),
   le **numéro à payer** et un **bouton WhatsApp pré-rempli**.
2. Il paie (Orange Money / Wave / MTN), puis envoie sa **capture** + sa référence via WhatsApp ou email.
3. Vous ouvrez **`/admin`**, saisissez votre token, vérifiez le paiement reçu, et cliquez **Approuver**.
4. Le rapport se débloque pour l'utilisateur (il clique « Vérifier mon accès »).

La capture voyage par WhatsApp/email — **rien à stocker côté serveur**.

## Lancer en local

```bash
pip install -r requirements.txt
cp .env.example .env        # puis remplir les valeurs
python app.py               # -> http://localhost:5000  (admin: /admin)
```

## Variables à définir

| Variable | Rôle |
|---|---|
| `PAY_NUMBER` | Numéro mobile money affiché à l'utilisateur (texte libre) |
| `ADMIN_WHATSAPP` | Votre WhatsApp, format international **sans +** (ex. `2250700000000`) |
| `ADMIN_EMAIL` | Email admin (optionnel, alternative à WhatsApp) |
| `ADMIN_TOKEN` | Mot de passe du panneau `/admin` (choisissez-le long) |
| `BASE_URL` | URL publique Render |
| `REPORT_PRICE` / `CURRENCY` | 2000 / XOF (valeurs par défaut) |

## Déploiement Render

1. Pousser le contenu de `soleo_backend` à la **racine** d'un dépôt GitHub.
2. Render > **New > Web Service** (ou Blueprint via `render.yaml`).
   - Build : `pip install -r requirements.txt` — Start : `gunicorn app:app`
   - Root Directory : vide si `app.py` est à la racine.
3. Ajouter les variables ci-dessus (`BASE_URL` après le 1er déploiement, puis redéployer).
4. Le panneau admin est sur `https://VOTRE-APP.onrender.com/admin`.

### ⚠️ Persistance (étape 2)

Free tier Render = disque éphémère : les demandes/leads seraient perdus à chaque redéploiement.
- **Disque persistant** (Starter) : monter sur `/var/data`, puis `DB_PATH=/var/data/soleo.db` (bloc prêt dans `render.yaml`).
- **PostgreSQL** pour passer à l'échelle. L'accès DB est isolé dans `db()` / `init_db()`.

## Étape 2 — Persistance des données

Par défaut l'app utilise **SQLite** (un fichier `soleo.db`). Sur le free tier Render, ce
fichier est effacé à chaque redéploiement. Pour conserver durablement leads et paiements :

**Option recommandée (gratuite, persistante) : PostgreSQL via Neon**
1. Créez une base gratuite sur [neon.tech](https://neon.tech) (ou Supabase).
2. Copiez l'URL de connexion (`postgresql://...?sslmode=require`).
3. Sur Render, ajoutez la variable `DATABASE_URL` avec cette URL.

Le code détecte `DATABASE_URL` et bascule **automatiquement** sur Postgres — aucune autre
modification. Sans cette variable, il reste sur SQLite. (Alternative : disque persistant
Render Starter + `DB_PATH=/var/data/soleo.db`.)

## Étape 3 — Rapport PDF premium

À la validation d'un paiement, l'utilisateur peut **télécharger un rapport PDF** détaillé
(synthèse, dimensionnement technique, rentabilité année par année sur 20 ans, hypothèses).

- Génération à la demande via `report.py` (reportlab) — **rien à stocker**.
- Téléchargeable sur `/api/report/<ref>/pdf` (refusé si non validé : 403).
- **Envoi par email automatique** si vous renseignez les variables `SMTP_*` (optionnel) ;
  sinon le client télécharge depuis le site.
- `report.py` est le **point d'extension ESTHER** : remplacez-y les calculs simplifiés par
  le moteur ESTHER (production horaire, P50/P90, courbes) sans toucher au reste.

## API

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/` | Frontend |
| POST | `/api/leads` | Lead installateur |
| POST | `/api/pay/request` | Crée une demande, renvoie référence + instructions |
| GET | `/api/report/<ref>` | État (en attente / payé) + rapport |
| GET | `/admin` | Panneau admin |
| GET | `/api/admin/requests` | Demandes de paiement (protégé) |
| POST | `/api/admin/approve` | Valide une demande → débloque (protégé) |
| POST | `/api/admin/reject` | Rejette (protégé) |
| GET | `/api/admin/leads` | Leads (protégé) |

## À brancher ensuite

- **Rapport PDF premium** : `generate_report_pdf()` dans `app.py` = point d'entrée pour le moteur **ESTHER** (dimensionnement détaillé + ROI 20 ans + PDF + envoi email automatique après validation).
- **Migration paiement automatique** : quand vous aurez un registre de commerce, repassez à CinetPay — seule la section paiement de `app.py` change, le reste est intact.
