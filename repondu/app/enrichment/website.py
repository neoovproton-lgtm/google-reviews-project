"""Récupération du site web d'un prospect (httpx, via proxy si configuré) et extraction."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from app.config import Settings, get_settings
from app.enrichment.extract import Extraction, extract_page

log = logging.getLogger(__name__)

MAX_CONTACT_PAGES = 3
CONTACT_PATHS = ("/contact", "/contact/", "/nous-contacter", "/contactez-nous", "/mentions-legales")
SOCIAL_HOSTS = ("facebook.com", "instagram.com")
PLATFORM_HOSTS = (
    "ubereats.com",
    "deliveroo.",
    "thefork.",
    "lafourchette.",
    "linktr.ee",
    "google.com",
    "tripadvisor.",
    "zenchef.",
    "business.site",
)


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def make_client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.enrich_user_agent, "Accept-Language": "fr-FR,fr;q=0.9"},
        timeout=settings.enrich_timeout_s,
        follow_redirects=True,
        proxy=settings.proxy_url or None,
        trust_env=False,
    )


def _fetch(client: httpx.Client, url: str) -> tuple[str, str] | None:
    try:
        r = client.get(url)
    except httpx.HTTPError as exc:
        log.info("fetch %s : %s", url, exc)
        return None
    if r.status_code >= 400 or "html" not in r.headers.get("content-type", "text/html"):
        return None
    return r.text, str(r.url)


def enrich_from_website(
    website: str, client: httpx.Client | None = None, settings: Settings | None = None
) -> Extraction:
    """Page d'accueil + pages contact candidates → Extraction fusionnée."""
    settings = settings or get_settings()
    url = normalize_url(website)
    host = (urlsplit(url).hostname or "").lower()
    if any(h in host for h in SOCIAL_HOSTS):
        # Le « site » est une page Facebook/Instagram : on n'y va pas, on garde le handle.
        return extract_page(f'<a href="{url}">x</a>', url)
    if any(h in host for h in PLATFORM_HOSTS):
        return Extraction()

    own_client = client is None
    client = client or make_client(settings)
    try:
        home = _fetch(client, url)
        if home is None:
            return Extraction()
        html, final_url = home
        result = extract_page(html, final_url)
        candidates = list(result.contact_links)
        base = final_url.rstrip("/")
        for path in CONTACT_PATHS:
            cand = (
                base + path
                if not urlsplit(final_url).path.strip("/")
                else f"{urlsplit(final_url).scheme}://{urlsplit(final_url).netloc}{path}"
            )
            if cand not in candidates:
                candidates.append(cand)
        visited = {final_url.rstrip("/")}
        fetched = 0
        for cand in candidates:
            if fetched >= MAX_CONTACT_PAGES:
                break
            if cand.rstrip("/") in visited:
                continue
            visited.add(cand.rstrip("/"))
            page = _fetch(client, cand)
            if page is None:
                continue
            fetched += 1
            result.merge(extract_page(page[0], page[1]))
            if result.emails and result.contact_form_url and result.instagram:
                break
        return result
    finally:
        if own_client:
            client.close()
