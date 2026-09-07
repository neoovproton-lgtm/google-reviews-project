# D2 — Détection des nouveaux avis

## Objectif (PRD)
Lecture des notifications Google reçues sur le compte gestionnaire (IMAP) → création de tâche.

## Conception
- Source de vérité : la fiche Maps (A2). Une notification déclenche un rafraîchissement
  immédiat des avis du prospect lié ; les avis dont le `review_id` est nouveau deviennent des
  tâches (brouillon B1/B2/B3).
- `app/service/notifications.py` : boîte du compte gestionnaire (`MANAGER_IMAP_*`), filtre sur
  l'expéditeur Google, extraction du nom d'établissement / auteur / note / extrait,
  appariement avec `establishments` par nom normalisé.
- Filet de sécurité : rafraîchissement périodique de tous les clients actifs toutes les
  `SERVICE_REVIEW_CHECK_HOURS` (6) heures par le scheduler, notification ou pas.

## Definition of Done
- [ ] Parseur de notification testé sur fixtures (FR/EN, HTML).
- [ ] Rafraîchissement → nouveaux avis détectés (test avec fixture Maps) → tâches créées.
- [ ] Handoff `docs/handoff/D2.md`.
