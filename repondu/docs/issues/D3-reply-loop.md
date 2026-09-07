# D3 — Boucle : nouvel avis → réponse → veto 24 h → publication → log

## Objectif (PRD)
Nouvel avis → réponse générée → envoyée au client par mail/SMS avec « publié dans 24 h sauf
veto » → publication (semi-manuelle) → log. DoD : délai avis → réponse publiée < 24 h sur
100 % des avis des clients test.

## Conception
- `app/service/loop.py::process_new_reviews` : brouillon (B1 + B2), notification client
  (Telegram si lié, sinon mail de service, SMS si mobile) avec le texte et « publié dans 24 h
  sauf veto », veto par bouton Telegram, `POST /reviews/{id}/decision`, ou réponse mail
  (« veto » / « non » → refus, « ok » → approbation) détectée par la boîte de service.
- Approbation automatique au délai (`auto_approve_due`, B3). File de publication :
  Telegram `/apublier` (texte à coller), `/publie <id>` ou `POST /replies/{id}/published` →
  `published_at`, l'avis passe `has_owner_response = 1`.
- Mesure : `GET /stats.service` → délai médian avis → publication, part < 24 h, en attente.

## Definition of Done
- [ ] Boucle testée bout en bout avec faux LLM / mail / Telegram.
- [ ] Veto par mail testé ; publication et log testés ; stats de délai.
- [ ] Handoff `docs/handoff/D3.md`.
