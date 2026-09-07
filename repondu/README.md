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
