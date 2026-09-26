# Publier de façon anonyme

GitHub relie tout ce qui est publié à un compte et à une identité git.
Points à vérifier :

1. **Compte dédié** : créer un compte GitHub séparé, avec un pseudo et une
   adresse e-mail qui n'est utilisée nulle part ailleurs.
2. **Identité des commits** : configurer le dépôt localement avec
   ```sh
   git config user.name  "pseudo"
   git config user.email "ID+pseudo@users.noreply.github.com"
   ```
   et activer *Settings → Emails → Keep my email addresses private* ainsi que
   *Block command line pushes that expose my email*.
3. **Historique** : un dépôt neuf n'hérite d'aucun ancien commit. Ne pas
   pousser d'historique existant qui contient votre vrai nom ou e-mail.
4. **Métadonnées de fichiers** : retirer les EXIF des images
   (`exiftool -all= image.jpg`) et les propriétés des PDF/documents.
5. **Contenu** : pas de fuseau horaire, chemins locaux (`/home/prenom/...`),
   noms de machines ou captures d'écran révélatrices.
6. **Réseau** : GitHub voit l'adresse IP de connexion ; un VPN ou Tor limite
   ce recoupement.
