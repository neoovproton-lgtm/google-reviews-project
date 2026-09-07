# A2 — Extraire les 30 derniers avis par fiche

## Objectif
Pour chaque prospect : les 30 avis les plus récents avec date, note, texte, présence d'une
réponse du propriétaire. Calculer par fiche `taux_reponse` et `avis_par_mois`.

## Conception
- Ouvrir la fiche Maps par `place_id`/URL, onglet « Avis », tri « Les plus récents », scroll
  jusqu'à 30 avis.
- Les dates Google sont relatives (« il y a 2 mois ») : `parsers.parse_relative_date` les
  convertit en date approximative (FR + EN).
- Table `reviews` : `prospect_id`, `review_id` (unique), `date`, `rating`, `text`,
  `has_owner_response`, `owner_response_text`.
- `avis_par_mois` = nb d'avis / durée en mois entre l'avis le plus ancien de l'échantillon et
  aujourd'hui (min 1 mois). Si < 30 avis, même formule sur l'échantillon complet.
- `taux_reponse` = avis avec réponse / avis de l'échantillon.
- Statut prospect → `reviews_scraped`.
- `repondu reviews --limit N` et `POST /scrape {"mode": "reviews"}`.

## Definition of Done
- [ ] Table `reviews`, upsert par `review_id`.
- [ ] Parseur de dates relatives testé (FR/EN, jour/semaine/mois/an).
- [ ] Calcul `taux_reponse` et `avis_par_mois` testés.
- [ ] Test navigateur sur fixture HTML d'une fiche avec avis.
- [ ] Handoff `docs/handoff/A2.md`.
