# A1 — Scraper Google Maps : liste des restaurants par ville

## Objectif
Paramétré par une liste de villes, lister tous les restaurants avec : nom, adresse, `place_id`,
note, nombre d'avis, téléphone, site web, Instagram/Facebook, email si disponible.

## Contraintes (PRD)
- Rythme lent, proxies résidentiels (`PROXY_URL`), reprise sur erreur.
- Aucun LLM.

## Conception
- `data/cities.csv` : ville, lat, lng, rayon_km. Les 10 plus grandes villes de France livrées.
- Une recherche Maps rend ≈120 résultats max. Pour couvrir une ville : grille de points
  (pas ≈ 1,2 km) × liste de requêtes (`restaurant`, `pizzeria`, `brasserie`, …). Dédoublonnage
  par `place_id`.
- Table `scrape_jobs` : une ligne par (ville, requête, point de grille), statut
  `pending|done|error`, pour reprendre après une coupure.
- Table `prospects` : upsert par `place_id`, statut `new`.
- Navigation dans `app/scraping/maps.py`, sélecteurs dans `app/scraping/dom.py`, parsing pur dans
  `app/scraping/parsers.py`.
- Exposé par `POST /scrape` (job en arrière-plan) et `repondu scrape --city Lyon`.

## Definition of Done
- [ ] Tables `prospects` et `scrape_jobs` créées à l'init.
- [ ] `repondu scrape --city <ville>` remplit `prospects` et reprend là où il s'est arrêté.
- [ ] Tests unitaires des parseurs (place_id, note, nb avis, téléphone, réseaux sociaux).
- [ ] Test navigateur sur fixture HTML locale (listing + fiche).
- [ ] Première passe : 10 plus grandes villes (≈25 000 fiches) — à lancer sur le VPS avec proxies.
- [ ] Handoff `docs/handoff/A1.md`.
