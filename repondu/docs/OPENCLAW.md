# Répondu × OpenClaw — guide d'intégration

> À transmettre à OpenClaw tel quel. Principe PRD : **OpenClaw pilote, l'appli exécute.** Si
> OpenClaw tombe, le service `scheduler` continue seul (prospection, avis clients, rapports).

## 1. Ce qu'est l'appli

Service Docker autonome (FastAPI + SQLite + Playwright) qui couvre les cinq phases du PRD
(`docs/PRD.md`) :

| Phase | Ce que fait l'appli | Ce qui reste humain |
|---|---|---|
| A Qualification | Scrape Maps, avis, score, enrichissement contact | Lancer la première passe, vérifier les sélecteurs |
| B Réponses | Rédaction Sonnet, filtre de sécurité, veto 24 h | Approuver/vetoer, coller sur Google |
| C Prospection | Mails J0/J+3/J+8, formulaires, DM, SMS, quotas, opt-out, IMAP | Envoyer les DM, qualifier les réponses, lire les objections |
| D Service | Invitation gestionnaire, détection d'avis, boucle veto, rapport du lundi | Accepter l'invitation Google, publier |
| E Bilan | Questionnaire J+30, bilan J+45 | Appels de 5 min, décision |

Code : dépôt `neoovproton-lgtm/google-reviews-project`, dossier `repondu/`, branche
`claude/repondu-project-setup-mjwy1p`. Lire `README.md` (déploiement), `CLAUDE.md`
(conventions), `docs/handoff/*.md` (état exact de chaque issue), `docs/openapi.json`
(contrat complet, 37 routes, aussi servi par l'appli sur `GET /openapi.json`).

## 1 bis. Mise en service autonome

`docs/OPENCLAW_MISSION_J1.md` est la mission complète de J1 : OpenClaw installe, diagnostique
avec `repondu doctor` (`GET /doctor`, Telegram `/doctor`), ne demande à l'humain que les secrets
manquants, puis enchaîne les phases dès qu'elles sont prêtes. `scripts/bootstrap.sh` fait
l'installation VPS (Docker, Caddy, HTTPS, build).

## 2. Accès

- Base : `http://127.0.0.1:8000` sur le VPS (mettre un reverse proxy HTTPS devant si OpenClaw
  n'est pas sur la même machine). `PUBLIC_BASE_URL` doit être l'URL HTTPS publique.
- Auth : `Authorization: Bearer <API_TOKEN>` (valeur de `.env`). Sans jeton : `GET /health`,
  `GET /p/{token}`, `POST /webhooks/*`, `POST /telegram/webhook` (secret propre).
- Jobs longs : `POST /scrape`, `POST /outreach/run`, `POST /service/run` répondent `202` avec
  `{"id": "…", "status": "running"}`. **Un seul job à la fois** : `409` sinon. Interroger
  `GET /jobs/{id}` jusqu'à `status` = `done` ou `error` (champ `result` / `error`).
- Erreurs : `401` jeton, `404` objet inconnu, `409` conflit d'état (déjà décidé, job en cours),
  `422` valeur invalide (statut inconnu…). Corps JSON `{"detail": "…"}`.

## 3. Contrat d'API par usage

### Phase A — pipeline de qualification

```http
POST /scrape            {"mode":"places|reviews|score|enrich|all","cities":["Lyon"],"max_jobs":50,"limit":200}
GET  /jobs/{id}
GET  /prospects?status=new|reviews_scraped|qualified|disqualified|enriched&city=&limit=&offset=
GET  /prospects/{id}
GET  /prospects/export.csv?status=qualified&limit=300
GET  /stats             → prospects par statut/ville, scrape_jobs, qualified.written_channel_rate,
                          mailboxes, outreach (entonnoir), service, bilan
```
Ordre : `places` (long, reprend seul) → `reviews` (fiches ≥ 30 avis, les plus commentées d'abord)
→ `score` → `enrich`. `mode: "all"` enchaîne. Cible PRD : 150–300 `qualified`, ≥ 80 % avec canal écrit.

### Phase B — réponses aux avis

```http
POST /establishments                    {"name","prospect_id","cuisine_type","tone","signature",
                                         "manager_first_name","never_say","telegram_chat_id","contact_email"}
POST /establishments/{id}/draft         {"limit":N,"notify":true}   → brouillons + Telegram
GET  /reviews/pending?establishment_id=
POST /reviews/{review_id}/decision      {"decision":"approve|reject","text":"(corrigé, optionnel)","by":"openclaw"}
POST /reviews/auto-approve              → approuve les brouillons au veto écoulé (24 h), jamais les needs_human
GET  /replies?status=pending|approved|rejected|published
```
Un brouillon `needs_human: true` (hygiène, intoxication, discrimination, juridique, ou vérification
en échec) n'est **jamais** approuvé automatiquement : OpenClaw ne doit pas non plus l'approuver
sans un humain.

### Phase C — prospection

```http
POST /outreach/run                      {"limit":N,"force_window":false}   (jours ouvrés 9–18 h Paris)
GET  /outreach?status=active|replied|yes|objection|opted_out|bounced|done|stopped&channel=
GET  /outreach/{id}                     → séquence + messages (étapes 1..3, statut, fournisseur)
POST /outreach/{id}/outcome             {"outcome":"replied|yes|objection|opted_out|no|stopped","note":"…"}
POST /outreach/dm-batch                 {"chat_id":"…","limit":30}  → DM livrés sur Telegram
POST /outreach/messages/{id}/sent       → DM envoyé à la main
GET  /mailboxes · POST /mailboxes/sync · POST /mailboxes/{id}/resume
```
Le scheduler lance déjà `outreach/run` et la relève IMAP toutes les heures ; OpenClaw n'a
besoin de l'appeler que pour forcer ou limiter. Les réponses « OK » arrivent seules en `yes`
(IMAP), les autres en `replied` avec notification Telegram : à qualifier via `outcome`.

### Phase D — clients en essai

```http
POST /outreach/{id}/convert             → client créé + mail d'invitation gestionnaire (201)
POST /establishments/{id}/invite        → renvoyer l'invitation
POST /establishments/{id}/manager-added → l'accès Google est obtenu : essai 30 j, avis vérifiés / 6 h
POST /service/run?force=false           → rafraîchir les avis des clients dus, rédiger, prévenir
GET  /replies/to-publish                → file à coller sur Google
POST /replies/{id}/published?by=openclaw → log de publication (l'avis devient « répondu »)
POST /reports/weekly?force=false        → rapports du lundi (auto par le scheduler)
GET  /establishments/{id}/messages      → journal des messages client
```

### Phase E — bilan

```http
POST /surveys/send?force=false          → questionnaires des essais terminés (auto)
POST /establishments/{id}/feedback      {"would_continue":true,"price_willing":39,"missing":"…"}
GET  /bilan                             → oui, « je paierais » (%), prix médian, manques, décision suggérée
```

## 4. Ce qu'OpenClaw doit faire, et quand

| Cadence | Action OpenClaw | Appels |
|---|---|---|
| J1 (une fois) | Vérifier `GET /health`, `GET /stats`, lancer la Phase A ville par ville | `POST /scrape` puis `GET /jobs/{id}` |
| J1–J3 | Surveiller `stats.prospects` ; quand `qualified ≥ 150`, lancer `enrich` | `POST /scrape {"mode":"enrich"}` |
| J3 | Demander la relecture humaine des 20 premiers prospects et des 10 mails (`repondu outreach-eval`) avant tout envoi | — |
| Toutes les heures | Rien d'obligatoire (scheduler). Optionnel : lire `stats.outreach` et `stats.mailboxes`, alerter si une boîte est `active: false` | `GET /stats`, `POST /mailboxes/{id}/resume` après vérification humaine |
| Quotidien (15 min humain) | Pousser le lot de DM, lister les réponses à qualifier et à publier | `POST /outreach/dm-batch`, `GET /outreach?status=replied`, `GET /replies/to-publish` |
| Sur « oui » | Convertir, puis relancer si `manager_added` n'arrive pas (rappel auto à J+3) | `POST /outreach/{id}/convert` |
| Lundi | Vérifier que `reports.sent` n'est pas vide pour chaque client actif | `POST /reports/weekly` (idempotent) |
| J+30 / J+45 | Vérifier les questionnaires, saisir les appels, produire le bilan | `POST /surveys/send`, `POST /establishments/{id}/feedback`, `GET /bilan` |

Métriques à surveiller (PRD §4) : `stats.prospects.qualified` (≥ 5 000 à terme),
`stats.outreach.total.sent_step1` (100/jour S1 → 300/jour S+4), `rates.reply_of_contacted`
(≥ 8 %), `stats.outreach.total.yes` (5–10), `stats.service.pct_under_24h` (100 %),
`stats.bilan.would_pay_rate` (≥ 50 %).

## 5. Garde-fous à respecter

- Ne jamais contourner un `needs_human` ni approuver un brouillon d'avis sensible.
- Ne jamais réactiver une boîte coupée (`paused_reason`) sans qu'un humain ait regardé les bounces.
- `force_window: true` uniquement pour un test : pas d'envoi le week-end ni hors 9–18 h.
- Un prospect = un canal = une séquence. Le registre `optouts` est définitif.
- Les identifiants (clé API, jetons, IMAP) restent dans `.env` et `data/mailboxes.json` du VPS ;
  ne pas les demander à l'appli, elle ne les expose pas.

## 6. Telegram (@NeoovBOT) — commandes disponibles pour l'humain

`/onboard` `/profils` `/dm` `/envoye <id>` `/stats` `/oui|/objection|/non|/stop <séquence>`
`/client <séquence>` `/gestionnaire <client>` `/apublier` `/publie <id>` `/bilan [client oui|non prix manque]`
et les boutons Approuver / Veto sur chaque brouillon.
