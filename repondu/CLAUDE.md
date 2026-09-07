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
    llm.py        client Claude Sonnet (messages.parse, sortie structurée) ; set_llm() en tests
    replies/      prompt.py (règles), checks.py (vérifs pures), safety.py (B2), generator.py,
                  service.py (brouillons en base, veto/validation), evaluate.py (30 avis test)
    telegram/     client.py (Bot API, NullTelegram sans jeton), bot.py (onboarding, boutons,
                  /dm, /envoye, /stats, /oui, /objection)
    outreach/     compose.py (C1), sequence.py (C2 : machine à états), mailboxes.py, 
                  email_providers.py (Resend/Brevo/Log), channels.py + forms.py + sms.py (C3),
                  events.py (C5 : webhooks), inbox.py (C4 : IMAP), stats.py (entonnoir)
    service/      Phase D : mailer.py (mails/SMS/Telegram clients + journal), onboarding.py (D1),
                  notifications.py + inboxes.py (D2/D3 : boîtes gestionnaire et service),
                  loop.py (D3 : avis → brouillon → veto → publication, stats), report.py (D4),
                  survey.py (E1 : questionnaire de fin d'essai, bilan J+45)
    scheduler.py  boucle horaire (service docker `scheduler`) : prospection + service client
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
- Statuts brouillon (`replies`) : `pending` → `approved` | `rejected` → `published`. Un brouillon
  `needs_human` (filtre B2 ou vérifications B1 en échec) n'est jamais approuvé automatiquement.
- LLM : toujours via `app/llm.py` (`get_llm()`), jamais d'appel direct au SDK ailleurs. Tests
  avec `tests/fakes.py::FakeLLM` ; aucun test ne doit appeler l'API réelle.
- Prospection : une séquence par prospect (`outreach.prospect_id` unique), un message par
  (séquence, étape), jamais renvoyé s'il est `sent`. Fournisseurs (email, SMS, Telegram)
  toujours injectables (`set_provider`, `set_sms`, `set_telegram`) ; tests avec les faux de
  `tests/fakes.py`. Opt-out : registre `optouts`, vérifié à l'enrôlement et avant chaque envoi.
- Secrets IMAP dans `data/mailboxes.json` (volume, jamais commité), jamais en base.
- Service client : la fiche Maps est la source de vérité des avis ; les notifications Google ne
  sont qu'un déclencheur. Une réponse n'est `published` que par action humaine (`/publie`,
  `POST /replies/{id}/published`), qui marque aussi l'avis comme répondu.
- Français partout dans les docs, les messages et les données ; identifiants de code en anglais.
- Tests : pytest, pas de réseau externe. Les tests navigateur servent des fixtures HTML via un
  serveur local et sont marqués `@pytest.mark.browser`.
- Secrets uniquement via `.env` (voir `.env.example`). Jamais commités.
- Pas de dépendance ajoutée sans la justifier dans le handoff.

## Déploiement

VPS avec Docker : `docker compose up -d --build`. Volume `./data` pour la base SQLite et les
exports. Voir `README.md`.
