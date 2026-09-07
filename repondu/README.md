# Répondu

Réponse automatique aux avis Google pour restaurants. Phase A (qualification des prospects)
livrée : scraping Maps → avis → scoring → enrichissement contact. Produit : `docs/PRD.md`.
Guide agents : `CLAUDE.md`. Issues : `docs/issues/`. Handoffs : `docs/handoff/`.

## Déploiement sur le VPS (Docker)

Prérequis : Docker ≥ 24 avec le plugin Compose, proxies résidentiels accessibles depuis le VPS.

```bash
git clone <repo> && cd <repo>/repondu
cp .env.example .env            # renseigner API_TOKEN et PROXY_URL au minimum
docker compose up -d --build    # API sur http://127.0.0.1:8000
curl http://127.0.0.1:8000/health
```

La base SQLite, les exports CSV et `cities.csv` vivent dans `./data` (volume monté dans le
conteneur). Sauvegarde = copier `data/repondu.db`.

### Lancer la Phase A

```bash
# 1. Vérifier les sélecteurs Maps sur un seul job avant la grande passe
docker compose run --rm cli scrape --city Lyon --max-jobs 1
docker compose run --rm cli jobs

# 2. Première passe : 10 plus grandes villes (long : plusieurs heures, reprend seul après coupure)
docker compose run --rm cli scrape

# 3. Avis des fiches les plus commentées, puis scoring, puis enrichissement
docker compose run --rm cli reviews --limit 500
docker compose run --rm cli score
docker compose run --rm cli enrich

# 4. Liste qualifiée
docker compose run --rm cli export --status qualified --limit 300   # → data/exports/*.csv
```

Chaque commande est idempotente et reprenable : relancer une commande interrompue continue là
où elle s'était arrêtée. Étendre à d'autres villes : ajouter des lignes à `data/cities.csv`
(`city,lat,lng,radius_km`) puis `scrape --city <ville>`.

### Piloter par l'API (OpenClaw)

Toutes les routes sauf `/health` exigent `Authorization: Bearer $API_TOKEN` si `API_TOKEN`
est défini.

| Route | Rôle |
|---|---|
| `POST /scrape` `{"mode":"places\|reviews\|score\|enrich\|all","cities":[…],"max_jobs":N,"limit":N}` | Lance une étape en arrière-plan (202, un seul job à la fois). |
| `GET /jobs/{id}` | État/résultat du job. |
| `GET /prospects?status=qualified&city=&limit=&offset=` | Liste triée par score décroissant. |
| `GET /prospects/{id}` | Fiche. |
| `GET /prospects/export.csv?status=qualified&limit=300` | Export CSV. |
| `GET /stats` | Comptes par statut/ville, jobs, couverture contact des qualifiés. |
| `GET /health` | Vivacité (sans jeton). |
| `POST /establishments`, `GET /establishments` | Profil client (Phase B). |
| `POST /establishments/{id}/draft` `{"limit":N,"notify":true}` | Génère les réponses aux avis sans réponse (Sonnet) et les envoie sur Telegram. |
| `GET /reviews/pending` | Brouillons en attente de veto/validation. |
| `POST /reviews/{review_id}/decision` `{"decision":"approve\|reject","text":"…"}` | Décision humaine (texte corrigé optionnel). |
| `POST /reviews/auto-approve` | Approuve les brouillons dont le délai de veto (24 h) est écoulé. |
| `GET /replies?status=approved` | File des réponses à publier (semi-manuel). |
| `POST /telegram/webhook` | Webhook du bot (secret `TELEGRAM_WEBHOOK_SECRET`). |
| `POST /outreach/run` `{"limit":N,"force_window":false}` | Enrôle et envoie les étapes dues (job en arrière-plan). |
| `GET /outreach?status=&channel=`, `GET /outreach/{id}` | Séquences de prospection (avec messages). |
| `POST /outreach/{id}/outcome` `{"outcome":"yes\|objection\|replied\|opted_out\|no\|stopped"}` | Qualification d'une réponse. |
| `POST /outreach/dm-batch`, `POST /outreach/messages/{id}/sent` | Lot de DM vers Telegram, marquage envoyé. |
| `GET /mailboxes`, `POST /mailboxes/sync`, `POST /mailboxes/{id}/resume` | Boîtes d'envoi (santé, quota, réactivation). |
| `POST /webhooks/resend`, `POST /webhooks/brevo?token=` | Événements fournisseur (délivré, bounce, plainte, ouverture). |
| `GET /p/{token}` | Page publique des réponses rédigées (lien des SMS). |
| `POST /outreach/{id}/convert`, `POST /establishments/{id}/invite`, `POST /establishments/{id}/manager-added` | Conversion d'un oui, invitation gestionnaire, démarrage de l'essai. |
| `POST /service/run?force=` | Rafraîchit les avis des clients, rédige, prévient (job). |
| `GET /replies/to-publish`, `POST /replies/{id}/published` | File de publication et log de publication. |
| `POST /reports/weekly?force=` | Rapports hebdomadaires clients. |
| `GET /establishments/{id}/messages` | Journal des messages envoyés au client. |
| `POST /surveys/send?force=`, `POST /establishments/{id}/feedback`, `GET /bilan` | Questionnaire de fin d'essai et bilan J+45 (Phase E). |

```bash
curl -H "Authorization: Bearer $API_TOKEN" -X POST localhost:8000/scrape \
  -H 'content-type: application/json' -d '{"mode":"reviews","limit":200}'
curl -H "Authorization: Bearer $API_TOKEN" localhost:8000/stats
```

### Phase B — moteur de réponse

```bash
# Clé API Sonnet dans .env (ANTHROPIC_API_KEY), puis évaluation des 30 avis test à relire
docker compose run --rm cli eval-replies          # → data/exports/eval-replies-*.md

# Bot Telegram : jeton + secret dans .env, HTTPS devant l'API, puis
docker compose run --rm cli telegram-webhook --url https://<domaine>/telegram/webhook
# Dans Telegram : /onboard (5 questions) crée le profil ; lier ensuite le prospect via l'API
docker compose run --rm cli draft -e 1            # brouillons + notification Telegram
docker compose run --rm cli auto-approve          # à planifier toutes les heures (cron)
```

### Phase C — prospection

```bash
cp data/mailboxes.example.json data/mailboxes.json   # boîtes, quotas, IMAP (jamais commité)
docker compose run --rm cli mailboxes --sync
docker compose run --rm cli outreach-eval --limit 10  # 10 mails à relire avant tout envoi
docker compose run --rm cli outreach-run              # ou laisser le service `scheduler` (horaire)
docker compose run --rm cli inbox-poll                # réponses : OK → oui, STOP → opt-out
docker compose run --rm cli funnel                    # entonnoir (idem /stats Telegram)
docker compose run --rm cli mailboxes                 # santé, bounces, boîtes coupées
```

Le service `scheduler` enchaîne chaque heure : relève IMAP → prospection (jours ouvrés,
`OUTREACH_SEND_HOURS`, quotas 20 → 30 /jour/boîte) → approbation des brouillons au veto écoulé.
Webhooks fournisseur à déclarer : `https://<domaine>/webhooks/resend` (secret Svix dans
`RESEND_WEBHOOK_SECRET`) et `https://<domaine>/webhooks/brevo?token=<BREVO_WEBHOOK_TOKEN>`.

### Phase D — clients en essai

```bash
# Un prospect a répondu OK (séquence 12) : profil client + mail d'invitation gestionnaire
docker compose run --rm cli service-run --force      # ou Telegram : /client 12
# Accès gestionnaire obtenu : /gestionnaire <client>  (essai 30 jours, avis vérifiés toutes les 6 h)
docker compose run --rm cli service-inboxes          # notifications Google + vetos par mail
docker compose run --rm cli weekly-report --force    # rapport hebdomadaire (auto le lundi)
# Chaque jour : Telegram /apublier → coller sur Google → /publie <id>
```

Boîtes à configurer dans `.env` : `SERVICE_FROM_ADDRESS` (+ `SERVICE_IMAP_*` pour les vetos par
mail), `MANAGER_IMAP_*` (compte Google gestionnaire, notifications d'avis),
`GOOGLE_MANAGER_EMAIL`. Captures d'écran de l'invitation dans `data/onboarding/`.

### Phase E — bilan

```bash
docker compose run --rm cli bilan                 # chiffres du bilan (idem /bilan Telegram)
docker compose run --rm cli bilan --send-surveys  # questionnaires des essais terminés (auto via scheduler)
# Appel de 5 min : /bilan <client> oui|non [prix] [ce qui manque]
```

### Exploitation

```bash
docker compose logs -f api            # journaux
docker compose pull && docker compose up -d --build   # mise à jour
docker compose run --rm cli initdb    # migration légère du schéma après une mise à jour
```

Mettre un reverse proxy TLS (Caddy, Nginx) devant le port 8000 si l'API doit être jointe
depuis l'extérieur ; par défaut elle n'écoute que sur 127.0.0.1.

## Développement local

```bash
make install   # uv venv .venv (Python 3.12) + dépendances + Chromium Playwright
make test      # 93 tests, dont tests navigateur sur fixtures locales (aucun réseau externe)
make lint
make run       # http://127.0.0.1:8000/docs
.venv/bin/repondu --help
```

Variables : voir `.env.example` (lu automatiquement depuis `repondu/.env`).
