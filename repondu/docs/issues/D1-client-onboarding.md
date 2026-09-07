# D1 — Procédure client : ajouter le compte projet comme gestionnaire

## Objectif (PRD)
Mail « comment m'ajouter comme gestionnaire » en 4 captures, 30 secondes. Tout l'onboarding et
le suivi à distance (mail, SMS, visio si demandé).

## Conception
- Conversion d'un « oui » : `POST /outreach/{id}/convert` ou Telegram `/client <séquence>` →
  `establishments` créé depuis le prospect (nom, cuisine, email, mobile, lien prospect, base de
  référence : taux de réponse et note au moment de la conversion), statut d'onboarding
  `invited`, essai de 30 jours à partir de l'ajout comme gestionnaire.
- `app/service/onboarding.py` : mail d'invitation (4 étapes numérotées, adresse
  `GOOGLE_MANAGER_EMAIL`, captures `data/onboarding/etape-1..4.png` jointes si présentes),
  envoyé depuis la boîte de service (`SERVICE_FROM_ADDRESS`, fournisseur `SERVICE_EMAIL_PROVIDER`).
  Rappel automatique après `ONBOARDING_REMINDER_DAYS` (3) sans ajout. Journal `client_messages`.
- `POST /establishments/{id}/manager-added` ou Telegram `/gestionnaire <id>` : démarre l'essai.

## Definition of Done
- [ ] Conversion testée (profil créé, base de référence, invitation envoyée et journalisée).
- [ ] Rappel après 3 jours testé ; démarrage d'essai testé.
- [ ] Handoff `docs/handoff/D1.md`.
