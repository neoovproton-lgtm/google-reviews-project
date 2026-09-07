# C1 — Générateur de mail de prospection

## Objectif (PRD)
Pour chaque prospect, mail court (≤ 120 mots) contenant : constat chiffré (« 14 avis sans
réponse ces 30 jours, dont 3 négatifs »), 2 réponses déjà rédigées pour 2 de leurs vrais avis,
proposition d'essai gratuit 30 jours, une seule action demandée (répondre « OK »).
DoD : 10 mails générés, relus, 0 faute, 0 formule générique.

## Conception
- `app/outreach/compose.py` : le modèle écrit `subject`, `opening` (constat + ce qu'on fait),
  `closing` (essai 30 jours, répondre « OK ») ; le code assemble le mail avec les 2 exemples
  (avis réel + réponse rédigée par B1), la signature et la ligne d'opt-out. Les 120 mots
  s'appliquent au texte rédigé (hors avis cités et opt-out).
- Vérifications en code : longueur, chiffres du constat présents, « OK » présent, aucune
  formule générique (liste), vouvoiement, pas de jargon (« IA », « solution », « booster »…).
- Les 2 exemples : avis récents sans réponse, un ≥ 4★ et un ≤ 3★ si possible, jamais un avis
  signalé par le filtre B2.
- `repondu outreach-eval --limit 10` : 10 mails → markdown à relire.

## Definition of Done
- [ ] Tests unitaires : prompt, assemblage, chaque vérification, choix des exemples.
- [ ] `outreach-eval` produit le fichier de relecture (clé API sur le VPS).
- [ ] Handoff `docs/handoff/C1.md`.
