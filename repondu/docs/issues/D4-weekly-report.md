# D4 — Rapport hebdomadaire client

## Objectif (PRD)
Avis reçus, réponses publiées, note moyenne, taux de réponse avant/après. DoD : un mail auto
par client chaque lundi.

## Conception
- `app/service/report.py` : chiffres sur 7 jours glissants et depuis le début de l'essai, base
  de référence (`baseline_response_rate`, `baseline_rating`) figée à la conversion.
- Envoi par la boîte de service chaque lundi (scheduler, `last_report_at` évite les doublons),
  journal `client_messages`. CLI `repondu weekly-report [--force]`.

## Definition of Done
- [ ] Contenu du rapport testé ; envoi le lundi seulement, une fois par semaine.
- [ ] Handoff `docs/handoff/D4.md`.
