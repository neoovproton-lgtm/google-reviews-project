# A4 — Enrichissement contact et canal prioritaire

## Objectif
Pour chaque prospect qualifié : email générique du site, URL du formulaire de contact, handles
Instagram/Facebook, téléphone mobile si dispo. Un `canal_prioritaire` par prospect.
DoD PRD : ≥ 80 % des prospects avec au moins un canal écrit.

## Conception
- `app/enrichment/website.py` : httpx (via proxy si configuré), page d'accueil + pages
  candidates (`/contact`, `/contact-us`, `/nous-contacter`, `/mentions-legales`). Extraction
  pure dans `app/enrichment/extract.py` : emails (`mailto:` + regex, filtre des adresses
  techniques/génériques d'hébergeur), formulaires (page contenant `<form>` avec champ email ou
  textarea), liens instagram.com / facebook.com, numéros FR (mobile = 06/07).
- `app/enrichment/channel.py` : `canal_prioritaire` = premier disponible dans l'ordre
  `email > formulaire > instagram > facebook > sms > telephone`.
- Statut prospect → `enriched`. Champs : `email`, `contact_form_url`, `instagram`, `facebook`,
  `mobile_phone`, `canal_prioritaire`, `enriched_at`.
- `repondu enrich --limit N` et `POST /scrape {"mode": "enrich"}`.

## Definition of Done
- [ ] Extraction testée sur fixtures HTML (emails, formulaire, réseaux, mobile).
- [ ] Choix du canal testé.
- [ ] `GET /stats` expose le % de prospects qualifiés avec un canal écrit.
- [ ] Handoff `docs/handoff/A4.md`.
