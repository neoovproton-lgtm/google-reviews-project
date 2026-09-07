# PRD — Répondu : réponse automatique aux avis Google pour restaurants

## 0. Cadre

- **Nom du projet** : Répondu (domaines à confirmer).
- **Objectif** : 5–10 établissements en essai gratuit 30 jours, avec réponses publiées sur leur fiche Google.
- **But réel** : entraînement à la vente. Pas de facturation. Mesure de succès = nombre de « oui » et réponse à « vous paieriez combien ? ».
- **Ratio** : 95 % agents, 5 % humain (validation des mails sortants les 3 premiers jours, appels/réponses prospects, ajout comme gestionnaire).
- **Durée** : 14 jours jusqu'aux premiers essais, 30 jours d'essai, bilan J+45.
- **Cible** : restaurants, toute la France (pas de contrainte géographique, aucun déplacement physique). Critère : ≥ 10 avis/mois ET taux de réponse < 50 %.
- **Accès fiche** : le client ajoute le compte Google du projet comme *Gestionnaire* dans Google Business Profile. Pas d'API, pas d'extension, pas d'automatisation navigateur côté client.
- **Prix affiché pendant l'essai** : 19 €/mois (< 15 avis/mois) · 39 €/mois (restaurants). Non facturé.

## 1. Stack

- Backend : FastAPI + PostgreSQL (ou SQLite au début), Docker sur le VPS existant.
- Scraping Maps : Playwright + proxies déjà en place, ou API tierce si le volume bloque.
- LLM : Claude Sonnet via clé API pour tout ce qui rédige (réponses aux avis, mails, DM, SMS). Aucun LLM dans le scraping ni le scoring (code pur).
- Emails : 3 domaines proches, 2 boîtes chacune, outil de warm-up dès J1. Montée : 20/jour/boîte en semaine 1 → 30/jour/boîte à S+3 (≈180/jour). Envoi via Resend/Brevo, opt-out dans chaque mail.
- Canaux secondaires : formulaires de contact (Playwright), DM Instagram/Facebook (semi-auto, préparés par l'agent), SMS B2B (Brevo/Twilio, heures ouvrables, opt-out).
- Interface OpenClaw ↔ appli : l'appli est un service Docker autonome exposant `POST /scrape`, `GET /prospects?status=`, `POST /outreach/run`, `GET /reviews/pending`, `POST /reviews/{id}/decision`, `GET /stats`, plus un webhook Telegram pour veto/validation. OpenClaw pilote, l'appli exécute ; si OpenClaw tombe, les réponses aux avis continuent.
- Publication des réponses : depuis le compte gestionnaire, semi-manuel (l'agent prépare, humain colle) tant que la validation API n'est pas obtenue. Demande d'accès API Google Business Profile déposée J1 en parallèle.
- Interface : Telegram (@NeoovBOT) pour valider/veto. Pas de front web avant validation produit.

## 2. Phases et issues

Pattern : une issue = une session agent fraîche, handoff markdown à la fin.

### Phase A — Qualification des prospects (J1–J3)

- **A1** Scraper Maps : paramétré par liste de villes, lister tous les restaurants (nom, adresse, place_id, note, nb avis, téléphone, site, Instagram/Facebook, email si dispo). Rythme lent, proxies résidentiels, reprise sur erreur.
  DoD : 10 plus grandes villes en première passe (≈25 000 fiches), puis extension à toute la France par lots. Table `prospects`.
- **A2** Extraire les 30 derniers avis par fiche : date, note, texte, présence/absence de réponse.
  DoD : table `reviews`, taux de réponse et avis/mois calculés par fiche.
- **A3** Scoring : `score = avis_par_mois × (1 − taux_reponse)`. Filtre ≥ 10 avis/mois et < 50 % réponse. Tri décroissant.
  DoD : liste de 150–300 prospects qualifiés, exportable.
- **A4** Enrichissement contact : email générique du site, URL du formulaire de contact, handles Instagram/Facebook, téléphone mobile si dispo. Un `canal_prioritaire` par prospect.
  DoD : ≥ 80 % des prospects avec au moins un canal écrit.

### Phase B — Moteur de réponse (J3–J6)

- **B1** Prompt de rédaction : ton par note (5★ chaleureux et court, 4★ remerciement + détail repris, 3★ constructif, ≤ 2★ calme, factuel, invitation à échanger hors ligne), vouvoiement par défaut, signature configurable, reprise d'un élément concret de l'avis, max 60 mots pour ≥ 4★, max 90 mots pour ≤ 3★, jamais d'excuse générique, jamais de promesse commerciale.
  DoD : 30 avis test → 30 réponses, validation humaine ≥ 27/30 acceptables sans retouche.
- **B2** Filtre de sécurité : avis mentionnant hygiène, intoxication, discrimination, menace juridique → toujours en validation humaine, jamais auto.
  DoD : tests unitaires sur 20 cas.
- **B3** Profil établissement : nom, type de cuisine, ton, signature, prénom du gérant, éléments à ne jamais dire.
  DoD : schéma + formulaire Telegram de 5 questions pour onboarding.

### Phase C — Machine de prospection (J5–J8)

- **C1** Générateur de mail : pour chaque prospect, mail court (≤ 120 mots) contenant : constat chiffré (« 14 avis sans réponse ces 30 jours, dont 3 négatifs »), les 2 réponses déjà rédigées pour 2 de leurs vrais avis, proposition d'essai gratuit 30 jours, une seule action demandée (répondre « OK »).
  DoD : 10 mails générés, relus, 0 faute, 0 formule générique.
- **C2** Séquence : J0 mail, J+3 relance (1 nouvelle réponse rédigée pour un avis récent), J+8 dernier message court. Stop dès réponse. Rotation des boîtes d'envoi, quotas par boîte, opt-out honoré.
  DoD : machine à états en base, envois idempotents, quotas respectés.
- **C3** Multicanal : formulaire de contact rempli par Playwright si pas d'email ; DM Instagram/Facebook préparés (texte + 2 réponses) et livrés sur Telegram par lot de 30/jour pour envoi manuel ; SMS B2B court avec lien vers les réponses rédigées.
  DoD : 3 canaux opérationnels, chaque prospect contacté par un seul canal à la fois.
- **C5** Deliverability : warm-up des 6 boîtes dès J1, monitoring bounce/spam par boîte, coupure auto d'une boîte au-delà de 3 % de bounce.
  DoD : tableau de santé des boîtes dans `/stats`.
- **C4** Tracking : envois, ouvertures si dispo, réponses, objections, « oui », taux par étape.
  DoD : commande Telegram `/stats` qui renvoie l'entonnoir.

### Phase D — Onboarding et service (J8–J14, puis continu)

- **D1** Procédure client : mail « comment m'ajouter comme gestionnaire » en 4 captures, 30 secondes. Tout l'onboarding et le suivi se font à distance (mail, SMS, visio si demandé).
- **D2** Détection nouveaux avis : lecture des notifications Google reçues sur le compte gestionnaire (IMAP) → création de tâche.
- **D3** Boucle : nouvel avis → réponse générée → envoyée au client par mail/SMS avec « publié dans 24 h sauf veto » → publication (semi-manuelle) → log.
  DoD : délai avis → réponse publiée < 24 h sur 100 % des avis des clients test.
- **D4** Rapport hebdomadaire client : avis reçus, réponses publiées, note moyenne, taux de réponse avant/après.
  DoD : un mail auto par client chaque lundi.

### Phase E — Bilan (J+45)

- Questionnaire de fin d'essai (3 questions) : continueriez-vous ? à quel prix ? qu'est-ce qui manque ?
- Décision : arrêter, pivoter verticale, ou structurer pour facturer quand ce sera possible.

## 3. Lancement — séquence humaine

- J1 : créer compte Google projet, déposer demande API, acheter 3 domaines, créer 6 boîtes, lancer le warm-up, créer la clé API Sonnet avec alerte de dépense.
- J3 : valider les 20 premiers prospects et les 10 premiers mails à la main.
- J6–J8 : premiers envois 20/jour/boîte + premiers DM.
- Quotidien 15 min : lire les réponses prospects, répondre, envoyer le lot de DM préparé, ajouter les « oui » en clients.
- Fin d'essai : appel de 5 min par client pour le questionnaire.

## 4. Métriques cibles

| Étape | Cible |
|---|---|
| Prospects qualifiés | ≥ 5 000 (10 villes), extensible |
| Contacts envoyés | 100/jour en S1 → 300/jour à S+4, tous canaux |
| Taux de réponse | ≥ 8 % |
| Essais acceptés | 5–10 |
| Délai avis → réponse | < 24 h |
| Taux de réponse client après 30 j | 100 % |
| « Je paierais » à la fin | ≥ 50 % des testeurs |

## 5. Risques et parades

- **Deliverability** : plusieurs domaines, warm-up continu, quotas par boîte, texte court, pas de lien tracké les premiers jours, coupure auto d'une boîte qui dérape.
- **Coût Sonnet** : ~400 réponses + ~3 000 messages/mois ≈ 10–20 €. Alerte de dépense à 30 €.
- **Cadre légal prospection B2B** : opt-out dans chaque message, pas de SMS hors heures ouvrables, registre des désinscriptions.
- **Scraping bloqué** : proxies résidentiels, rythme lent, fallback API tierce.
- **Refus d'ajouter un gestionnaire** : c'est un signal, pas un problème à contourner. Noter l'objection.
- **Réponse IA inappropriée** : filtre B2 + veto 24 h + relecture Claude sur ≤ 2★.
- **Pattern d'abandon** : la seule métrique qui compte en semaine 2 est le nombre de mails envoyés. Tout le reste attend.

## 6. Hors périmètre

Front web, paiement, multi-plateforme (TripAdvisor, Uber Eats), API Google tant que non validée, autre verticale que restaurants, rendez-vous physiques.
