"""`repondu doctor` — vérifie chaque secret et dépendance, sans jamais afficher les valeurs.

Chaque contrôle est une fonction indépendante et injectable (tests). Résultat : liste de
{name, status: ok|missing|error|skipped, detail, hint} + un verdict par phase.
"""

from __future__ import annotations

import imaplib
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)
PLACEHOLDER = "À_REMPLACER"


@dataclass
class Check:
    name: str
    status: str  # ok | missing | error | skipped
    detail: str = ""
    hint: str = ""
    phase: str = ""


def _is_set(value) -> bool:
    return bool(value) and PLACEHOLDER not in str(value)


# --- Contrôles unitaires ---------------------------------------------------------------------


def check_api_token(s: Settings) -> Check:
    if not _is_set(s.api_token):
        return Check(
            "api_token", "missing", hint="API_TOKEN vide : openssl rand -hex 32", phase="A"
        )
    return Check("api_token", "ok", phase="A")


def check_proxy(s: Settings, client_factory=httpx.Client) -> Check:
    if not _is_set(s.proxy_url):
        return Check(
            "proxy", "missing", hint="PROXY_URL : accès HTTP du fournisseur de proxies", phase="A"
        )
    try:
        with client_factory(proxy=s.proxy_url, timeout=20, trust_env=False) as c:
            r = c.get("https://www.google.com/generate_204")
        if r.status_code in (204, 200):
            return Check("proxy", "ok", "Google joignable via le proxy", phase="A")
        return Check("proxy", "error", f"HTTP {r.status_code} via le proxy", phase="A")
    except Exception as exc:  # noqa: BLE001
        return Check(
            "proxy", "error", str(exc)[:200], "Vérifier user:pass@host:port et l'IP autorisée", "A"
        )


def check_chromium(s: Settings) -> Check:
    try:
        from app.scraping.browser import browser_session

        with browser_session(s) as (context, _):
            page = context.new_page()
            page.set_content("<p>ok</p>")
            page.close()
        return Check("chromium", "ok", phase="A")
    except Exception as exc:  # noqa: BLE001
        return Check("chromium", "error", str(exc)[:200], "playwright install chromium", "A")


def check_anthropic(s: Settings) -> Check:
    if not _is_set(s.anthropic_api_key):
        return Check(
            "anthropic",
            "missing",
            hint="ANTHROPIC_API_KEY : console.anthropic.com → API keys",
            phase="B",
        )
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=1, timeout=20)
        model = client.models.retrieve(s.anthropic_model)
        return Check("anthropic", "ok", f"modèle {model.id} accessible", phase="B")
    except Exception as exc:  # noqa: BLE001
        return Check(
            "anthropic",
            "error",
            f"{type(exc).__name__}: {str(exc)[:160]}",
            "Clé invalide ou modèle inconnu",
            "B",
        )


def check_telegram(s: Settings, client_factory=httpx.Client) -> Check:
    if not _is_set(s.telegram_bot_token):
        return Check(
            "telegram_bot", "missing", hint="TELEGRAM_BOT_TOKEN : @BotFather → /mybots", phase="B"
        )
    try:
        with client_factory(timeout=15) as c:
            me = c.get(f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe").json()
            if not me.get("ok"):
                return Check(
                    "telegram_bot", "error", str(me)[:160], "Jeton refusé par Telegram", "B"
                )
            info = c.get(
                f"https://api.telegram.org/bot{s.telegram_bot_token}/getWebhookInfo"
            ).json()
        url = (info.get("result") or {}).get("url") or ""
        detail = f"@{me['result'].get('username')} · webhook : {url or 'non déclaré'}"
        if not _is_set(s.telegram_chat_id):
            return Check(
                "telegram_bot",
                "error",
                detail,
                "TELEGRAM_CHAT_ID vide : envoyer un message au bot puis getUpdates",
                "B",
            )
        if not url:
            return Check(
                "telegram_bot",
                "error",
                detail,
                "repondu telegram-webhook --url https://<domaine>/telegram/webhook",
                "B",
            )
        return Check("telegram_bot", "ok", detail, phase="B")
    except Exception as exc:  # noqa: BLE001
        return Check("telegram_bot", "error", str(exc)[:200], phase="B")


def check_resend(s: Settings, client_factory=httpx.Client) -> Check:
    if not _is_set(s.resend_api_key):
        return Check(
            "resend",
            "missing",
            hint="RESEND_API_KEY : resend.com → API Keys (optionnel si Brevo)",
            phase="C",
        )
    try:
        with client_factory(timeout=15) as c:
            r = c.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {s.resend_api_key}"},
            )
        if r.status_code != 200:
            return Check("resend", "error", f"HTTP {r.status_code}", "Clé Resend refusée", "C")
        domains = r.json().get("data", [])
        verified = [d["name"] for d in domains if d.get("status") == "verified"]
        pending = [d["name"] for d in domains if d.get("status") != "verified"]
        detail = f"vérifiés : {verified or '—'} · en attente : {pending or '—'}"
        status = "ok" if verified else "error"
        return Check(
            "resend",
            status,
            detail,
            "" if verified else "Ajouter les DNS SPF/DKIM chez le registrar",
            "C",
        )
    except Exception as exc:  # noqa: BLE001
        return Check("resend", "error", str(exc)[:200], phase="C")


def check_brevo(s: Settings, client_factory=httpx.Client) -> Check:
    if not _is_set(s.brevo_api_key):
        return Check(
            "brevo",
            "missing",
            hint="BREVO_API_KEY : app.brevo.com → SMTP & API (requis pour les SMS)",
            phase="C",
        )
    try:
        with client_factory(timeout=15) as c:
            r = c.get("https://api.brevo.com/v3/account", headers={"api-key": s.brevo_api_key})
        if r.status_code != 200:
            return Check("brevo", "error", f"HTTP {r.status_code}", "Clé Brevo refusée", "C")
        plan = r.json().get("plan", [])
        credits = {p.get("type"): p.get("credits") for p in plan if isinstance(p, dict)}
        return Check("brevo", "ok", f"crédits : {credits}", phase="C")
    except Exception as exc:  # noqa: BLE001
        return Check("brevo", "error", str(exc)[:200], phase="C")


def check_imap(name: str, host, port, user, password, phase: str, login=None) -> Check:
    if not (_is_set(host) and _is_set(user) and _is_set(password)):
        return Check(
            name, "missing", hint=f"{name} : hôte, utilisateur et mot de passe IMAP", phase=phase
        )
    try:
        if login is None:
            conn = imaplib.IMAP4_SSL(host, int(port or 993))
            conn.login(user, password)
            conn.logout()
        else:
            login(host, port, user, password)
        return Check(name, "ok", f"{user} sur {host}", phase=phase)
    except Exception as exc:  # noqa: BLE001
        return Check(
            name,
            "error",
            str(exc)[:160],
            "Mot de passe d'application (Gmail) ? IMAP activé ?",
            phase,
        )


def check_mailboxes(s: Settings, login=None) -> list[Check]:
    from app.outreach.mailboxes import load_mailboxes_file

    entries = load_mailboxes_file(s.mailboxes_file)
    if not entries:
        return [
            Check(
                "mailboxes",
                "missing",
                hint="data/mailboxes.json absent ou vide (6 boîtes attendues)",
                phase="C",
            )
        ]
    out = [Check("mailboxes", "ok", f"{len(entries)} boîte(s) déclarée(s)", phase="C")]
    for e in entries:
        imap = e.get("imap") or {}
        out.append(
            check_imap(
                f"imap:{e.get('address')}",
                imap.get("host"),
                imap.get("port", 993),
                imap.get("user"),
                imap.get("password"),
                "C",
                login,
            )
        )
        if e.get("provider") not in ("resend", "brevo", "log"):
            out.append(
                Check(
                    f"provider:{e.get('address')}",
                    "error",
                    str(e.get("provider")),
                    "provider = resend | brevo",
                    "C",
                )
            )
    return out


def check_public_url(s: Settings, client_factory=httpx.Client) -> Check:
    if not _is_set(s.public_base_url) or "127.0.0.1" in s.public_base_url:
        return Check(
            "public_url",
            "missing",
            hint="PUBLIC_BASE_URL : https://api.<domaine> (DNS A → IP du VPS, Caddy)",
            phase="C",
        )
    try:
        with client_factory(timeout=15) as c:
            r = c.get(s.public_base_url.rstrip("/") + "/health")
        ok = r.status_code == 200
        return Check(
            "public_url",
            "ok" if ok else "error",
            f"HTTP {r.status_code} sur /health",
            "" if ok else "Caddy / DNS",
            "C",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "public_url", "error", str(exc)[:160], "DNS non propagé ou reverse proxy absent", "C"
        )


def check_service_mail(s: Settings) -> Check:
    if not _is_set(s.service_from_address):
        return Check(
            "service_from", "missing", hint="SERVICE_FROM_ADDRESS : boîte de service", phase="D"
        )
    if s.service_email_provider not in ("resend", "brevo"):
        return Check(
            "service_from",
            "error",
            f"provider {s.service_email_provider}",
            "SERVICE_EMAIL_PROVIDER=resend ou brevo",
            "D",
        )
    return Check("service_from", "ok", s.service_from_address, phase="D")


def check_google_manager(s: Settings) -> Check:
    if not _is_set(s.google_manager_email) or "example" in s.google_manager_email:
        return Check(
            "google_manager",
            "missing",
            hint="GOOGLE_MANAGER_EMAIL : compte Google du projet",
            phase="D",
        )
    return Check("google_manager", "ok", s.google_manager_email, phase="D")


def check_onboarding_assets(s: Settings) -> Check:
    found = [
        n
        for n in ("etape-1", "etape-2", "etape-3", "etape-4")
        if any(
            (s.onboarding_assets_dir / f"{n}{ext}").exists() for ext in (".png", ".jpg", ".jpeg")
        )
    ]
    if len(found) == 4:
        return Check("onboarding_assets", "ok", phase="D")
    return Check(
        "onboarding_assets",
        "missing",
        f"{len(found)}/4 captures",
        "data/onboarding/etape-1..4.png",
        "D",
    )


# --- Orchestration -------------------------------------------------------------------------


DEFAULT_CHECKS: dict[str, Callable[[Settings], Check | list[Check]]] = {
    "api_token": check_api_token,
    "proxy": check_proxy,
    "chromium": check_chromium,
    "anthropic": check_anthropic,
    "telegram": check_telegram,
    "resend": check_resend,
    "brevo": check_brevo,
    "mailboxes": check_mailboxes,
    "public_url": check_public_url,
    "service_from": check_service_mail,
    "service_imap": lambda s: check_imap(
        "imap:service",
        s.service_imap_host,
        s.service_imap_port,
        s.service_imap_user,
        s.service_imap_password,
        "D",
    ),
    "manager_imap": lambda s: check_imap(
        "imap:manager",
        s.manager_imap_host,
        s.manager_imap_port,
        s.manager_imap_user,
        s.manager_imap_password,
        "D",
    ),
    "google_manager": check_google_manager,
    "onboarding_assets": check_onboarding_assets,
}

PHASES = {
    "A": ("api_token", "proxy", "chromium"),
    "B": ("anthropic", "telegram_bot"),
    "C": ("resend|brevo", "mailboxes", "public_url"),
    "D": ("service_from", "imap:service", "imap:manager", "google_manager"),
}


def run_doctor(
    settings: Settings | None = None, checks: dict | None = None, only: list[str] | None = None
) -> dict:
    settings = settings or get_settings()
    checks = checks or DEFAULT_CHECKS
    results: list[Check] = []
    for key, fn in checks.items():
        if only and key not in only:
            continue
        try:
            r = fn(settings)
        except Exception as exc:  # noqa: BLE001
            r = Check(key, "error", f"{type(exc).__name__}: {str(exc)[:160]}")
        results.extend(r if isinstance(r, list) else [r])
    by_name = {r.name: r for r in results}

    def ready(names) -> bool:
        for n in names:
            if "|" in n:
                if not any(by_name.get(x) and by_name[x].status == "ok" for x in n.split("|")):
                    return False
            elif not (by_name.get(n) and by_name[n].status == "ok"):
                return False
        return True

    phases = {p: ready(names) for p, names in PHASES.items()}
    todo = [r for r in results if r.status in ("missing", "error")]
    return {
        "checks": [asdict(r) for r in results],
        "phases_ready": phases,
        "todo_human": [
            {"name": r.name, "status": r.status, "hint": r.hint or r.detail} for r in todo
        ],
        "ok": not todo,
    }


def format_doctor(report: dict) -> str:
    icons = {"ok": "✅", "missing": "⬜", "error": "❌", "skipped": "➖"}
    lines = ["🩺 Répondu doctor"]
    for c in report["checks"]:
        line = f"{icons.get(c['status'], '?')} {c['name']}"
        if c["detail"]:
            line += f" — {c['detail']}"
        if c["status"] != "ok" and c["hint"]:
            line += f"  → {c['hint']}"
        lines.append(line)
    lines.append(
        "Phases prêtes : "
        + " · ".join(f"{p} {'✅' if ok else '⛔'}" for p, ok in report["phases_ready"].items())
    )
    if report["todo_human"]:
        lines.append(f"À faire par un humain : {len(report['todo_human'])} élément(s)")
    else:
        lines.append("Tout est en place.")
    return "\n".join(lines)
