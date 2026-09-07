# C5 — Délivrabilité

## Objectif (PRD)
Warm-up des 6 boîtes dès J1, monitoring bounce/spam par boîte, coupure auto d'une boîte au-delà
de 3 % de bounce. DoD : tableau de santé des boîtes dans `/stats`.

## Conception
- Warm-up : outil externe (hors code) ; `mailboxes.warmup_started_at` pilote la montée en
  quota (20 → 25 → 30 /jour).
- Webhooks fournisseur : `POST /webhooks/resend`, `POST /webhooks/brevo` → `email_events`,
  compteurs par boîte (`delivered`, `bounced`, `complained`, `opened`), mise à jour du
  message et de la séquence (bounce → `bounced`, plainte → opt-out).
- Coupure : après ≥ 20 envois, `bounced / sent > OUTREACH_MAX_BOUNCE_RATE` (0,03) ou toute
  plainte spam → `active = 0`, `paused_reason`. Réactivation manuelle (`repondu mailboxes
  --resume`).
- `/stats.mailboxes` : par boîte, envoyés (jour / total), quota effectif, bounce %, statut.

## Definition of Done
- [ ] Tests webhooks Resend et Brevo, coupure automatique, quota effectif.
- [ ] Handoff `docs/handoff/C5.md`.
