# C3 — Multicanal

## Objectif (PRD)
Formulaire de contact rempli par Playwright si pas d'email ; DM Instagram/Facebook préparés
(texte + 2 réponses) et livrés sur Telegram par lot de 30/jour pour envoi manuel ; SMS B2B
court avec lien vers les réponses rédigées. DoD : 3 canaux opérationnels, chaque prospect
contacté par un seul canal à la fois.

## Conception
- Une seule séquence active par prospect (contrainte unique), canal = `canal_prioritaire`.
- Formulaire : `app/outreach/forms.py` (Playwright, détection heuristique des champs nom /
  email / téléphone / message, soumission, vérification). Un seul envoi (J0), puis `done`.
- DM : texte rédigé (≤ 80 mots) + 2 réponses, statut `manual_pending`, livraison Telegram par
  lot (`OUTREACH_DM_BATCH` = 30) via `repondu dm-batch` ou `/dm` ; `/envoye <id>` marque envoyé.
- SMS : modèle court en code (pas de LLM), lien public `GET /p/{token}` vers les 2 réponses,
  heures ouvrables uniquement, « STOP » honoré ; fournisseur Brevo SMS (`SmsProvider`).

## Definition of Done
- [ ] Test navigateur : formulaire de fixture rempli et soumis.
- [ ] Tests DM (lot, marquage) et SMS (fenêtre horaire, lien, opt-out).
- [ ] Handoff `docs/handoff/C3.md`.
