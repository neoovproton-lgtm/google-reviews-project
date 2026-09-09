# Mission J1 pour OpenClaw — mettre Répondu en service avec le minimum d'humain

> À coller tel quel à OpenClaw, après avoir remplacé `<IP_VPS>`, `<DOMAINE_API>` et
> `<API_TOKEN>`. OpenClaw fait tout ce qu'un agent peut faire ; il ne demande à l'humain que
> ce qu'un agent ne peut pas obtenir (comptes, paiements, validation en deux étapes, DNS).

## Contexte

Tu as un accès SSH au VPS `<IP_VPS>` (Debian/Ubuntu, root ou sudo). Le code est sur GitHub :
dépôt `neoovproton-lgtm/google-reviews-project`, branche `claude/repondu-project-setup-mjwy1p`,
dossier `repondu/`. Ton jeton d'API sera `<API_TOKEN>`. Le domaine public de l'API sera
`<DOMAINE_API>` (un enregistrement DNS A vers `<IP_VPS>` doit exister ; si tu peux le créer chez
le registrar, fais-le, sinon demande-le).

Lis d'abord `repondu/docs/OPENCLAW.md` (ton rôle, contrat d'API, garde-fous) puis
`repondu/docs/RUNBOOK.md`.

## Ce que tu fais seul, dans cet ordre

1. **Installer**
   ```bash
   git clone https://github.com/neoovproton-lgtm/google-reviews-project.git
   cd google-reviews-project && git checkout claude/repondu-project-setup-mjwy1p && cd repondu
   sudo bash scripts/bootstrap.sh <DOMAINE_API>
   ```
   Le script installe Docker et Caddy s'ils manquent, configure l'HTTPS, construit l'image,
   démarre l'API et le scheduler, crée `.env` et `data/mailboxes.json` depuis les exemples.

2. **Poser les secrets déjà connus** dans `.env` : `API_TOKEN=<API_TOKEN>`,
   `PUBLIC_BASE_URL=https://<DOMAINE_API>`, et les deux jetons aléatoires que tu génères
   toi-même : `TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 16)`, `BREVO_WEBHOOK_TOKEN=$(openssl rand -hex 16)`.
   `SERVICE_EMAIL_PROVIDER=resend` (ou `brevo` si seul Brevo est fourni).

3. **Diagnostiquer** : `docker compose run --rm cli doctor`. La sortie liste, ligne par ligne,
   ce qui est ⬜ manquant, ❌ en erreur, ✅ ok, et quelles phases sont prêtes.
   La même information est disponible via `GET https://<DOMAINE_API>/doctor` (avec le jeton)
   et par la commande Telegram `/doctor`.

4. **Demander à l'humain uniquement ce que doctor signale comme manquant**, en un seul
   message groupé, avec pour chaque élément l'endroit exact où le trouver (les indications sont
   dans la sortie de doctor et dans `docs/RUNBOOK.md` étape J1). Ne demande jamais deux fois la
   même chose. N'invente jamais une valeur.

5. **À chaque valeur reçue** : l'écrire dans `.env` ou `data/mailboxes.json` (`nano` ou `sed`),
   `chmod 600` les deux fichiers, `docker compose up -d` pour recharger, puis relancer `doctor`.
   Quand le jeton Telegram et le chat sont fournis :
   `docker compose run --rm cli telegram-webhook --url https://<DOMAINE_API>/telegram/webhook`.
   Quand les clés Resend/Brevo sont fournies : dire à l'humain quelles URL de webhook déclarer
   (`https://<DOMAINE_API>/webhooks/resend` ; `https://<DOMAINE_API>/webhooks/brevo?token=<BREVO_WEBHOOK_TOKEN>`).

6. **Dès que la phase A est ✅** (jeton, proxy, Chromium), sans attendre le reste :
   ```bash
   docker compose run --rm cli scrape --city Lyon --max-jobs 1
   docker compose run --rm cli jobs
   curl -H "Authorization: Bearer <API_TOKEN>" "http://127.0.0.1:8000/prospects?limit=3"
   ```
   Si des prospects apparaissent, lance la Phase A complète : `POST /scrape {"mode":"places"}`
   puis, à la fin du job, `reviews`, `score`, `enrich`. Si la liste reste vide après le test,
   envoie à l'humain les 100 dernières lignes de `docker compose logs api` et arrête-toi sur ce
   point : c'est le seul élément du projet jamais validé contre le vrai Google Maps.

7. **Dès que la phase B est ✅** : `docker compose run --rm cli eval-replies` puis, quand la
   Phase A a produit des prospects enrichis, `docker compose run --rm cli outreach-eval --limit 10`.
   Envoie à l'humain les deux fichiers `data/exports/eval-*.md` et demande une relecture. Sans
   son accord explicite (« relecture OK »), n'appelle jamais `POST /outreach/run`.

8. **Dès que la phase C est ✅ et la relecture validée** : rien à lancer, le scheduler envoie
   chaque heure dans la fenêtre 9–18 h jours ouvrés. Surveille `GET /stats` et remonte sur
   Telegram chaque jour : envoyés, réponses, oui, boîtes coupées.

9. **Phase D** : sur chaque `yes`, `POST /outreach/{id}/convert` ; rappelle à l'humain
   d'accepter l'invitation Google puis d'envoyer `/gestionnaire <client>`. Le reste est automatique.

## Ce que tu ne fais jamais

- Approuver un brouillon `needs_human`. Réactiver une boîte coupée. Envoyer hors fenêtre.
- Coller un secret dans un message, un log ou un commit. `doctor` ne montre jamais les valeurs, toi non plus.
- Lancer deux jobs en même temps (`409`) : attends `GET /jobs/{id}` → `done`.
- Modifier le code. Si quelque chose casse, rapporte le message d'erreur exact à l'humain.

## Rapport

À la fin de chaque étape, un message Telegram de 5 lignes maximum : ce qui est fait, ce qui
bloque, ce que tu attends de l'humain, avec les chiffres de `doctor` ou de `/stats`.
