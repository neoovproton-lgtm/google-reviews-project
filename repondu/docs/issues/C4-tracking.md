# C4 — Tracking

## Objectif (PRD)
Envois, ouvertures si dispo, réponses, objections, « oui », taux par étape.
DoD : commande Telegram `/stats` qui renvoie l'entonnoir.

## Conception
- Réponses : détection IMAP (`app/outreach/inbox.py`, `repondu inbox-poll`) sur les boîtes
  d'envoi → `replied` (+ `yes` si la réponse est « OK », `opted_out` si « STOP »), notification
  Telegram avec l'extrait ; et saisie manuelle `POST /outreach/{id}/outcome`
  (`replied|yes|objection|opted_out|no`) ou Telegram `/oui <id>`, `/objection <id> <note>`.
- `app/outreach/stats.py::funnel()` : par canal et global : enrôlés, envoyés J0/J+3/J+8,
  délivrés, ouverts, répondus, objections, oui, opt-out, taux par étape.
- `GET /stats.outreach`, Telegram `/stats`.

## Definition of Done
- [ ] Tests : détection IMAP sur messages bruts, saisie manuelle, entonnoir, `/stats`.
- [ ] Handoff `docs/handoff/C4.md`.
