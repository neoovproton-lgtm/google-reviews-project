# B3 — Profil établissement et onboarding Telegram

## Objectif (PRD)
Profil : nom, type de cuisine, ton, signature, prénom du gérant, éléments à ne jamais dire.
DoD : schéma + formulaire Telegram de 5 questions pour l'onboarding.

## Conception
- Table `establishments` (client en essai) liée à un `prospect` optionnel ; table `replies`
  (brouillons de réponse : texte, `needs_human`, `safety_flags`, statut
  `pending|approved|rejected|published`) ; table `telegram_sessions` (état du formulaire).
- `app/telegram/client.py` : envoi de messages (Bot API, httpx) ; `FakeTelegram` en tests.
- `app/telegram/bot.py` : commandes `/start`, `/onboard` (5 questions : nom du restaurant ·
  type de cuisine · prénom du gérant · ton et signature · à ne jamais dire), `/profils`,
  `/annuler`, boutons `Approuver` / `Refuser` sur chaque brouillon envoyé.
- API : `POST /telegram/webhook` (secret `TELEGRAM_WEBHOOK_SECRET`), `GET /reviews/pending`,
  `POST /reviews/{id}/decision`, `POST /establishments`, `POST /establishments/{id}/draft`
  (génère les brouillons pour les avis sans réponse et les envoie sur Telegram).

## Definition of Done
- [ ] Schéma créé/migré par `init_db()`.
- [ ] Formulaire Telegram testé bout en bout avec un faux transport (5 questions → profil).
- [ ] Veto/validation testés (`/reviews/pending`, décision, boutons Telegram).
- [ ] Handoff `docs/handoff/B3.md`.
