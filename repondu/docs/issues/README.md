# Issues — Phase A : qualification des prospects

| Issue | Titre | Branche | Statut |
|---|---|---|---|
| [A1](A1-scraper-maps.md) | Scraper Google Maps → table `prospects` | `feat/A1-scraper-maps` | fait |
| [A2](A2-reviews.md) | 30 derniers avis par fiche → table `reviews`, taux de réponse, avis/mois | `feat/A2-reviews` | fait |
| [A3](A3-scoring.md) | Scoring, filtre, tri, export | `feat/A3-scoring` | fait |
| [A4](A4-enrichment.md) | Enrichissement contact + canal prioritaire | `feat/A4-enrichment` | fait |

Ordre : A1 → A2 → A3 → A4. Chaque issue se termine par `docs/handoff/<issue>.md`.

# Issues — Phase B : moteur de réponse

| Issue | Titre | Branche | Statut |
|---|---|---|---|
| [B1](B1-reply-prompt.md) | Prompt de rédaction (Sonnet), vérifications, éval 30 avis | `feat/B1-reply-prompt` | fait |
| [B2](B2-safety-filter.md) | Filtre de sécurité → validation humaine | `feat/B2-safety-filter` | fait |
| [B3](B3-establishment-profile.md) | Profil établissement, onboarding Telegram, veto/validation | `feat/B3-establishment-profile` | fait |

# Issues — Phase C : machine de prospection

| Issue | Titre | Branche | Statut |
|---|---|---|---|
| [C1](C1-email-generator.md) | Générateur de mail (constat chiffré + 2 réponses + essai) | `feat/C1-email-generator` | fait |
| [C2](C2-sequence.md) | Séquence J0 / J+3 / J+8, boîtes, quotas, opt-out | `feat/C2-sequence` | fait |
| [C3](C3-multichannel.md) | Formulaire (Playwright), DM via Telegram, SMS | `feat/C3-multichannel` | fait |
| [C5](C5-deliverability.md) | Webhooks bounce/spam, coupure auto, santé des boîtes | `feat/C5-deliverability` | à faire |
| [C4](C4-tracking.md) | Réponses (IMAP + manuel), entonnoir, `/stats` Telegram | `feat/C4-tracking` | à faire |
