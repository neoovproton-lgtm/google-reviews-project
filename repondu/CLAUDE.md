# Répondu — guide pour les agents

Réponse automatique aux avis Google pour restaurants. Le PRD complet est dans `docs/PRD.md` :
le lire avant toute issue. Ce fichier décrit la stack, les conventions et le workflow.

## Stack

- Python 3.12, FastAPI, SQLAlchemy 2 sur SQLite (`data/repondu.db`), Playwright (Chromium),
  Docker sur le VPS. Pas de PostgreSQL tant que SQLite tient.
- LLM : Claude Sonnet via clé API (`ANTHROPIC_API_KEY`, modèle `claude-sonnet-5`) pour tout ce
  qui rédige. **Aucun LLM** dans le scraping, le scoring ni l'enrichissement : code pur.
- Proxies résidentiels déjà en place : `PROXY_URL` (format `http://user:pass@host:port`).
- Interface de pilotage : API HTTP (OpenClaw) + Telegram (Phase B+). Pas de front web.

## Arborescence

```
repondu/
  app/            code applicatif (package `app`)
    config.py     settings pydantic (lit .env)
    db.py         engine, sessions, init_db()
    models.py     tables SQLAlchemy (prospects, reviews, scrape_jobs)
    main.py       FastAPI (POST /scrape, GET /prospects, GET /stats, ...)
    cli.py        CLI typer (`repondu scrape|reviews|score|enrich|export`)
    scraping/     Playwright + parseurs purs (maps.py, reviews.py, parsers.py, browser.py)
    scoring.py    score = avis_par_mois × (1 − taux_reponse)
    enrichment/   site web → email, formulaire, réseaux sociaux, mobile ; canal prioritaire
  tests/          pytest ; fixtures HTML dans tests/fixtures/
  data/           cities.csv, base SQLite, exports (ignorés par git sauf cities.csv)
  docs/PRD.md     produit
  docs/issues/    une issue = un fichier, avec DoD
  docs/handoff/   un handoff par issue terminée
```

## Workflow : une issue = une branche + un handoff

1. Lire `docs/PRD.md`, l'issue dans `docs/issues/<issue>.md` et le dernier handoff.
2. Créer la branche `feat/<issue>` (ex. `feat/A2-reviews`) depuis `main`.
3. Implémenter, tests verts (`make test`), lint propre (`make lint`).
4. Écrire `docs/handoff/<issue>.md` : ce qui est fait, ce qui ne l'est pas, comment
   vérifier, points d'attention pour l'issue suivante.
5. Commit + push, PR vers `main`. Une session agent fraîche reprend l'issue suivante.

## Commandes

```
make install      # uv venv + deps + navigateur Playwright
make test         # pytest (les tests navigateur utilisent Chromium local)
make lint         # ruff check + format --check
make run          # uvicorn app.main:app --reload
repondu --help    # CLI (dans .venv)
docker compose up -d --build   # déploiement VPS
```

## Conventions

- Séparer strictement **navigation** (Playwright, effets de bord) et **parsing** (fonctions pures
  sur du HTML/dict, testées unitairement avec des fixtures dans `tests/fixtures/`).
- Les sélecteurs Google Maps vivent dans `app/scraping/dom.py` uniquement. S'ils cassent, c'est
  le seul fichier à toucher.
- Scraping : rythme lent (`SCRAPE_MIN_DELAY_S`/`SCRAPE_MAX_DELAY_S`), reprise sur erreur via la
  table `scrape_jobs`, idempotence par `place_id` (upsert).
- Statuts prospect : `new` → `reviews_scraped` → `qualified` | `disqualified` → `enriched`.
- Français partout dans les docs, les messages et les données ; identifiants de code en anglais.
- Tests : pytest, pas de réseau externe. Les tests navigateur servent des fixtures HTML via un
  serveur local et sont marqués `@pytest.mark.browser`.
- Secrets uniquement via `.env` (voir `.env.example`). Jamais commités.
- Pas de dépendance ajoutée sans la justifier dans le handoff.

## Déploiement

VPS avec Docker : `docker compose up -d --build`. Volume `./data` pour la base SQLite et les
exports. Voir `README.md`.
