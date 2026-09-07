# A3 — Scoring et liste qualifiée

## Objectif
`score = avis_par_mois × (1 − taux_reponse)`. Filtre : ≥ 10 avis/mois **et** < 50 % de réponse.
Tri décroissant. Liste de 150–300 prospects qualifiés, exportable.

## Conception
- `app/scoring.py` : fonctions pures `compute_score`, `is_qualified`, seuils configurables
  (`SCORE_MIN_REVIEWS_PER_MONTH`, `SCORE_MAX_RESPONSE_RATE`).
- `repondu score` : met à jour `score` et statut `qualified`/`disqualified` de tous les
  prospects `reviews_scraped` (ou déjà scorés).
- `repondu export --status qualified --limit 300` → CSV dans `data/exports/`.
- `GET /prospects?status=qualified` trié par score décroissant ; `GET /prospects/export.csv`.

## Definition of Done
- [ ] Tests unitaires du score et du filtre (bords : 10 avis/mois exact, 50 % exact).
- [ ] Export CSV avec les colonnes utiles à la prospection.
- [ ] `GET /stats` renvoie les comptes par statut.
- [ ] Handoff `docs/handoff/A3.md`.
