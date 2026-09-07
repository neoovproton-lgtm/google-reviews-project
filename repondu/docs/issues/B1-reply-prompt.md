# B1 — Prompt de rédaction des réponses aux avis

## Objectif (PRD)
Ton par note : 5★ chaleureux et court · 4★ remerciement + détail repris · 3★ constructif ·
≤ 2★ calme, factuel, invitation à échanger hors ligne. Vouvoiement par défaut, signature
configurable, reprise d'un élément concret de l'avis, max 60 mots (≥ 4★) / 90 mots (≤ 3★),
jamais d'excuse générique, jamais de promesse commerciale.

## Conception
- `app/llm.py` : client Claude Sonnet (`claude-sonnet-5`, clé `ANTHROPIC_API_KEY`), interface
  `LLM.complete_json(system, user, schema)` ; `FakeLLM` en tests (aucun réseau).
  Prompt système stable + `cache_control` (prompt caching), thinking adaptatif, effort moyen.
- `app/replies/prompt.py` : système (règles) + message utilisateur (profil, avis). Sortie
  structurée `{reply, detail_reused}`.
- `app/replies/generator.py` : génère, vérifie en code (longueur, vouvoiement, mots interdits,
  promesses, excuses génériques), un second passage de relecture pour ≤ 2★, une régénération
  si échec, sinon marque `needs_human`.
- `repondu eval-replies` : 30 avis test (`data/eval/reviews_test.json`) → fichier markdown à
  relire (DoD ≥ 27/30 sans retouche, validation humaine).

## Definition of Done
- [ ] Tests unitaires du prompt (règles par note) et des vérifications de sortie.
- [ ] Générateur testé avec `FakeLLM` (retry, relecture ≤ 2★).
- [ ] `repondu eval-replies` produit le fichier de relecture (à lancer avec une clé API).
- [ ] Handoff `docs/handoff/B1.md`.
