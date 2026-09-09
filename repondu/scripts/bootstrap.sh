#!/usr/bin/env bash
# Bootstrap VPS (Debian/Ubuntu) — idempotent. Usage :
#   sudo bash scripts/bootstrap.sh api.mon-domaine.fr
# Installe Docker + Caddy si absents, configure l'HTTPS vers :8000, construit et démarre l'appli.
set -euo pipefail
DOMAIN="${1:-}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

log() { printf '\n== %s\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  log "Installation de Docker"
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  log "Plugin Docker Compose manquant"; apt-get update && apt-get install -y docker-compose-plugin
fi

if [ -n "$DOMAIN" ]; then
  if ! command -v caddy >/dev/null 2>&1; then
    log "Installation de Caddy"
    apt-get update && apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update && apt-get install -y caddy
  fi
  log "Caddyfile pour $DOMAIN"
  cat > /etc/caddy/Caddyfile <<CADDY
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
CADDY
  systemctl enable --now caddy && systemctl reload caddy
fi

cd "$HERE"
[ -f .env ] || { cp .env.example .env; log "ATTENTION : .env créé depuis l'exemple, à compléter"; }
[ -f data/mailboxes.json ] || { cp data/mailboxes.example.json data/mailboxes.json; log "ATTENTION : data/mailboxes.json créé depuis l'exemple, à compléter"; }
chmod 600 .env data/mailboxes.json
if [ -n "$DOMAIN" ]; then
  grep -q '^PUBLIC_BASE_URL=' .env && sed -i "s#^PUBLIC_BASE_URL=.*#PUBLIC_BASE_URL=https://$DOMAIN#" .env || echo "PUBLIC_BASE_URL=https://$DOMAIN" >> .env
fi

log "Construction et démarrage"
docker compose up -d --build
sleep 5
curl -fsS http://127.0.0.1:8000/health && echo
log "Diagnostic"
docker compose run --rm cli doctor || true
log "Terminé. Compléter .env / data/mailboxes.json puis relancer : docker compose run --rm cli doctor"
