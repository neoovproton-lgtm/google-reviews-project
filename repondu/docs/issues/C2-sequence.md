# C2 — Séquence d'envoi

## Objectif (PRD)
J0 mail, J+3 relance (1 nouvelle réponse rédigée pour un avis récent), J+8 dernier message
court. Stop dès réponse. Rotation des boîtes, quotas par boîte, opt-out honoré.
DoD : machine à états en base, envois idempotents, quotas respectés.

## Conception
- Tables `mailboxes` (boîtes d'envoi, quota, warm-up, santé), `outreach` (une séquence par
  prospect : canal, étape, statut, prochaine action), `outreach_messages` (un message par
  (séquence, étape), clé d'idempotence, identifiant fournisseur, statut), `optouts` (registre
  des désinscriptions), `email_events` (webhooks fournisseur).
- Statuts séquence : `active` → `replied` | `yes` | `objection` | `opted_out` | `bounced` |
  `done` (3 messages sans réponse) | `stopped`.
- `app/outreach/sequence.py::run_outreach(now)` : enrôle les prospects `enriched` avec un canal,
  envoie les étapes dues dans la fenêtre (jours ouvrés, `OUTREACH_SEND_HOURS`), choisit la boîte
  la moins sollicitée du jour sous son quota effectif (20/jour en semaine 1 → 30 à S+3),
  programme l'étape suivante (+3 j, +5 j). Un message `sent` n'est jamais renvoyé.
- Fournisseurs : `app/outreach/email_providers.py` (Resend, Brevo, `LogProvider`), sélection
  par boîte. `POST /outreach/run`, `repondu outreach-run`.

## Definition of Done
- [ ] Tests : enrôlement, envoi J0/J+3/J+8, arrêt sur réponse, opt-out, quotas, idempotence,
  fenêtre d'envoi, rotation.
- [ ] Handoff `docs/handoff/C2.md`.
