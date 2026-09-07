# E1 — Fin d'essai : questionnaire et bilan J+45

## Objectif (PRD)
Questionnaire de fin d'essai (3 questions) : continueriez-vous ? à quel prix ? qu'est-ce qui
manque ? Décision : arrêter, pivoter verticale, ou structurer pour facturer. Mesure de succès :
nombre de « oui » et réponse à « vous paieriez combien ? » (cible : ≥ 50 % de « je paierais »).

## Conception
- Table `trial_feedback` (une réponse par client : continuerait, prix, ce qui manque, source,
  texte brut).
- À `trial_ends_at` : mail `[Répondu bilan #client]` avec les 3 questions (scheduler) ; réponse
  lue sur la boîte de service (oui/non, prix « 39 € », reste = manques). Appel de 5 min :
  Telegram `/bilan <client> oui|non [prix] [manque…]`. Statut d'onboarding → `ended`.
- `repondu bilan` / `GET /stats.bilan` : essais démarrés, questionnaires envoyés et reçus,
  « oui », « je paierais » (%), prix médian, manques, entonnoir de prospection, et les
  critères des 3 options de décision.

## Definition of Done
- [ ] Envoi à la fin d'essai (une fois), parsing des réponses, saisie Telegram, bilan testés.
- [ ] Handoff `docs/handoff/E1.md`.
