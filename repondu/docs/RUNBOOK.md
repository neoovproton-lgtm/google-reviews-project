# Runbook — mise en service (humain)

## J1 — 1 h
1. VPS : `git clone …` puis `cd repondu && cp .env.example .env` ; renseigner `API_TOKEN`,
   `PROXY_URL`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
   `TELEGRAM_WEBHOOK_SECRET`, `RESEND_API_KEY` ou `BREVO_API_KEY` (+ secrets de webhook),
   `PUBLIC_BASE_URL`, `SERVICE_FROM_ADDRESS`, `SERVICE_IMAP_*`, `GOOGLE_MANAGER_EMAIL`,
   `MANAGER_IMAP_*`.
2. `cp data/mailboxes.example.json data/mailboxes.json` : les 6 boîtes (3 domaines), leur
   fournisseur, `warmup_started_at`, IMAP. Lancer l'outil de warm-up.
3. `docker compose up -d --build` puis `curl localhost:8000/health`.
4. Reverse proxy HTTPS devant :8000, puis `docker compose run --rm cli telegram-webhook --url https://<domaine>/telegram/webhook`
   et déclarer les webhooks Resend/Brevo (voir README).
5. Déposer les 4 captures dans `data/onboarding/` ; déposer la demande d'accès API Google
   Business Profile ; créer l'alerte de dépense Anthropic à 30 €.
6. **Validation Maps** : `docker compose run --rm cli scrape --city Lyon --max-jobs 1` puis
   `docker compose run --rm cli jobs`. Si `prospects` reste vide : ajuster `app/scraping/dom.py`.

## J1–J3 — qualification
`scrape` (10 villes, plusieurs heures, reprend seul) → `reviews --limit 500` → `score` →
`enrich` → `export --status qualified --limit 300`. Objectif : 150–300 qualifiés, ≥ 80 % avec canal.

## J3 — relecture humaine
`eval-replies` (30 réponses, ≥ 27/30) et `outreach-eval --limit 10` (0 faute, 0 formule
générique). Valider les 20 premiers prospects à la main. Sans cette relecture, ne pas lancer
`outreach-run`.

## J6 → continu — 15 min par jour
- Telegram `/stats` ; `/dm` puis envoyer les DM et `/envoye <id>` ; qualifier les réponses
  (`/oui`, `/objection`) ; `/client <séquence>` sur chaque oui ; `/gestionnaire <client>` une
  fois l'invitation Google acceptée ; `/apublier` puis coller sur Google et `/publie <id>`.
- Surveiller `mailboxes` (bounce > 3 % = boîte coupée, comprendre avant `--resume`).

## J+30 / J+45
Questionnaires automatiques ; appels de 5 min saisis par `/bilan <client> oui|non prix manque` ;
`/bilan` pour la décision.

## Métrique unique de la semaine 2 (PRD §5)
Le nombre de mails envoyés : `stats.outreach.total.sent_step1`. Tout le reste attend.
