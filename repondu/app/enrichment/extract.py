"""Extraction pure depuis du HTML : emails, formulaire de contact, réseaux sociaux, téléphones."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from app.scraping.parsers import find_phones, is_mobile_phone

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*([A-Za-z0-9.-]+)\s*[\[(]\s*dot\s*[\])]\s*([A-Za-z]{2,})",
    re.I,
)
_EMAIL_BLOCKLIST = (
    "sentry",
    "wixpress",
    "example.",
    "domain.",
    "email.com",
    "yourdomain",
    "sitename",
    "wordpress",
    "shopify",
    "squarespace",
    "webflow",
    "godaddy",
    "ovh.",
    "ionos",
    "cloudflare",
    "noreply",
    "no-reply",
    "donotreply",
    "@2x",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    "schema.org",
    "w3.org",
)
_GENERIC_LOCAL_PARTS = (
    "contact",
    "info",
    "bonjour",
    "hello",
    "reservation",
    "reservations",
    "resa",
    "restaurant",
    "accueil",
    "commercial",
    "direction",
    "gerant",
    "manager",
    "bienvenue",
    "mail",
    "courrier",
)
_CONTACT_LINK_RE = re.compile(r"contact|nous-?contacter|contactez|joindre|mentions", re.I)
_IG_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]{2,30})/?", re.I)
_FB_RE = re.compile(
    r"facebook\.com/(pages/[^/?#]+/\d+|profile\.php\?id=\d+|[A-Za-z0-9_.\-]{2,60})", re.I
)
_IG_IGNORE = {"p", "explore", "reel", "reels", "stories", "accounts", "share", "tv"}
_FB_IGNORE = {
    "sharer",
    "sharer.php",
    "share",
    "share.php",
    "plugins",
    "login",
    "dialog",
    "hashtag",
    "groups",
    "events",
    "home.php",
    "policies",
    "privacy",
    "help",
}


@dataclass
class Extraction:
    emails: list[str] = field(default_factory=list)
    contact_form_url: str | None = None
    contact_links: list[str] = field(default_factory=list)
    instagram: str | None = None
    facebook: str | None = None
    phones: list[str] = field(default_factory=list)

    @property
    def mobile_phone(self) -> str | None:
        return next((p for p in self.phones if is_mobile_phone(p)), None)

    def merge(self, other: Extraction) -> Extraction:
        for e in other.emails:
            if e not in self.emails:
                self.emails.append(e)
        self.contact_form_url = self.contact_form_url or other.contact_form_url
        for link in other.contact_links:
            if link not in self.contact_links:
                self.contact_links.append(link)
        self.instagram = self.instagram or other.instagram
        self.facebook = self.facebook or other.facebook
        for p in other.phones:
            if p not in self.phones:
                self.phones.append(p)
        return self


def extract_page(html: str, page_url: str) -> Extraction:
    tree = HTMLParser(html)
    for tag in tree.css("script, style, noscript"):
        tag.decompose()
    hrefs = [
        (urljoin(page_url, a.attributes.get("href") or ""), (a.text() or "").strip())
        for a in tree.css("a[href]")
    ]
    text = tree.body.text(separator="\n") if tree.body else tree.text(separator="\n")
    site_domain = _registrable(urlsplit(page_url).hostname or "")

    emails = _find_emails(text, hrefs, site_domain)
    instagram, facebook = _find_socials(hrefs, html)
    phones = find_phones(text + "\n" + "\n".join(h for h, _ in hrefs if h.startswith("tel:")))
    contact_form = page_url if _has_contact_form(tree) else None
    contact_links = [
        h
        for h, label in hrefs
        if h.startswith("http")
        and (_CONTACT_LINK_RE.search(h) or _CONTACT_LINK_RE.search(label))
        and _registrable(urlsplit(h).hostname or "") == site_domain
    ]
    seen: list[str] = []
    for link in contact_links:
        link = link.split("#", 1)[0]
        if link not in seen and link.rstrip("/") != page_url.rstrip("/"):
            seen.append(link)
    return Extraction(emails, contact_form, seen, instagram, facebook, phones)


def _registrable(host: str) -> str:
    parts = host.lower().removeprefix("www.").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def _find_emails(text: str, hrefs: list[tuple[str, str]], site_domain: str) -> list[str]:
    found: list[str] = []
    for href, _ in hrefs:
        if href.lower().startswith("mailto:"):
            addr = href[7:].split("?", 1)[0].strip()
            if addr:
                found.append(addr)
    found.extend(m.group(0) for m in _EMAIL_RE.finditer(text))
    found.extend(f"{a}@{b}.{c}" for a, b, c in _OBFUSCATED_RE.findall(text))
    clean: list[str] = []
    for addr in found:
        addr = addr.strip().strip(".").lower()
        if not _EMAIL_RE.fullmatch(addr) or any(b in addr for b in _EMAIL_BLOCKLIST):
            continue
        if addr not in clean:
            clean.append(addr)
    return sorted(clean, key=lambda a: _email_rank(a, site_domain))


def _email_rank(addr: str, site_domain: str) -> tuple[int, int]:
    local, _, domain = addr.partition("@")
    generic = 0 if local in _GENERIC_LOCAL_PARTS else 1
    own = 0 if site_domain and _registrable(domain) == site_domain else 1
    return (generic, own)


def _find_socials(hrefs: list[tuple[str, str]], html: str) -> tuple[str | None, str | None]:
    instagram = facebook = None
    candidates = [h for h, _ in hrefs] + [html]
    for src in candidates:
        if instagram is None:
            for m in _IG_RE.finditer(src):
                handle = m.group(1).rstrip(".")
                if handle.lower() not in _IG_IGNORE:
                    instagram = handle
                    break
        if facebook is None:
            for m in _FB_RE.finditer(src):
                page = m.group(1)
                if page.split("/")[0].lower() not in _FB_IGNORE:
                    facebook = page
                    break
        if instagram and facebook:
            break
    return instagram, facebook


def _has_contact_form(tree: HTMLParser) -> bool:
    for form in tree.css("form"):
        action = (form.attributes.get("action") or "").lower()
        if "search" in action or form.css_first('input[type="search"]'):
            continue
        if form.css_first('input[type="email"], textarea, input[name*="mail" i]'):
            return True
    return False
