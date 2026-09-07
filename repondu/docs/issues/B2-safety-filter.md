# B2 — Filtre de sécurité

## Objectif (PRD)
Avis mentionnant hygiène, intoxication, discrimination, menace juridique → toujours en
validation humaine, jamais auto. DoD : tests unitaires sur 20 cas.

## Conception
- `app/replies/safety.py` : détection par lexique/regex (code pur, insensible à la casse et aux
  accents), catégories `hygiene`, `intoxication`, `discrimination`, `legal`. Renvoie les
  drapeaux ; `needs_human_review(text) -> bool`.
- Intégré au générateur : `Reply.needs_human = True` + `safety_flags` ; ces brouillons ne
  sont jamais publiés automatiquement (seul un `POST /reviews/{id}/decision` humain les
  approuve).

## Definition of Done
- [ ] ≥ 20 cas de test (positifs par catégorie, négatifs, pièges d'accent/casse).
- [ ] Le générateur force `needs_human` sur ces avis.
- [ ] Handoff `docs/handoff/B2.md`.
