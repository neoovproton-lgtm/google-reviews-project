"""Fonctions de parsing pures (aucun effet de bord, aucun navigateur). Testées unitairement."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

# 0x47e66e1f06e2b70f:0x40b82c3688c9460 dans les URLs Maps (identifiant de feature)
_HEX_ID_RE = re.compile(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", re.I)
_CHIJ_RE = re.compile(r"\b(ChIJ[0-9A-Za-z_-]{16,})")
_COORDS_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
_URL_COORDS_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")
_PLACE_NAME_RE = re.compile(r"/maps/place/([^/]+)/")

_PHONE_RE = re.compile(
    r"(?:\+33\s?[1-9]|0[1-9])(?:[\s.\-]?\d{2}){4}",
)
_INT_RE = re.compile(r"\d+")


def extract_place_id(url: str, html: str | None = None) -> str | None:
    """Identifiant stable d'une fiche.

    Préfère le `ChIJ…` (place_id Google) s'il est présent dans le HTML, sinon l'identifiant
    hexadécimal `0x…:0x…` de l'URL. Les deux sont uniques par fiche.
    """
    if html:
        m = _CHIJ_RE.search(html)
        if m:
            return m.group(1)
    m = _HEX_ID_RE.search(unquote(url))
    if m:
        return m.group(1).lower()
    return None


def extract_hex_id(url: str) -> str | None:
    m = _HEX_ID_RE.search(unquote(url))
    return m.group(1).lower() if m else None


def extract_coords(url: str) -> tuple[float, float] | None:
    """Coordonnées de la fiche (`!3d…!4d…`), sinon celles de la vue (`/@lat,lng`)."""
    u = unquote(url)
    m = _COORDS_RE.search(u) or _URL_COORDS_RE.search(u)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def extract_place_name_from_url(url: str) -> str | None:
    m = _PLACE_NAME_RE.search(unquote(url))
    return m.group(1).replace("+", " ") if m else None


def parse_rating(text: str | None) -> float | None:
    """'4,5' / '4.5' / '4,5 étoiles' → 4.5"""
    if not text:
        return None
    m = re.search(r"(\d)[,.](\d)", text)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    m = re.search(r"\b([1-5])\b", text)
    return float(m.group(1)) if m else None


def parse_int(text: str | None) -> int | None:
    """'(1 234)' / '1 234 avis' / '1,234 reviews' → 1234. Gère les espaces insécables."""
    if not text:
        return None
    cleaned = re.sub(r"[\s  ,.]", "", text)
    m = _INT_RE.search(cleaned)
    return int(m.group(0)) if m else None


def normalize_phone(text: str | None) -> str | None:
    """Numéro français normalisé en +33XXXXXXXXX. Retourne None si non reconnu."""
    if not text:
        return None
    digits = re.sub(r"[^\d+]", "", text)
    if digits.startswith("+33"):
        digits = "0" + digits[3:]
    elif digits.startswith("0033"):
        digits = "0" + digits[4:]
    if len(digits) == 10 and digits[0] == "0" and digits[1] in "123456789":
        return "+33" + digits[1:]
    return None


def is_mobile_phone(normalized: str | None) -> bool:
    return bool(normalized) and normalized.startswith(("+336", "+337"))


def find_phones(text: str) -> list[str]:
    """Tous les numéros FR trouvés dans un texte, normalisés, dédoublonnés, ordre conservé."""
    seen: list[str] = []
    for m in _PHONE_RE.finditer(text):
        n = normalize_phone(m.group(0))
        if n and n not in seen:
            seen.append(n)
    return seen


def parse_card(card: dict[str, Any]) -> dict[str, Any] | None:
    """Carte de la liste Maps (dict brut de EXTRACT_CARDS_JS) → champs prospect."""
    href = card.get("href") or ""
    place_id = extract_place_id(href)
    if not place_id:
        return None
    name = (card.get("name") or "").strip() or extract_place_name_from_url(href) or ""
    lines = [ln for ln in card.get("lines") or [] if ln]
    category, address = _split_category_address(lines)
    phones = find_phones(card.get("text") or "")
    coords = extract_coords(href)
    return {
        "place_id": place_id,
        "name": name,
        "maps_url": href,
        "rating": parse_rating(card.get("rating_text")),
        "review_count": parse_int(card.get("reviews_text")),
        "website": (card.get("website") or "").strip() or None,
        "phone": phones[0] if phones else None,
        "category": category,
        "address": address,
        "lat": coords[0] if coords else None,
        "lng": coords[1] if coords else None,
    }


def _split_category_address(lines: list[str]) -> tuple[str | None, str | None]:
    """Sur une carte Maps, une ligne 'Restaurant · 12 rue X' donne catégorie et adresse."""
    for line in lines:
        if "·" in line and not re.search(r"\d{2}[\s.]\d{2}[\s.]\d{2}", line):
            parts = [p.strip() for p in line.split("·")]
            parts = [p for p in parts if p and not re.match(r"^[€$]+$", p)]
            if len(parts) >= 2:
                return parts[0], parts[-1]
            if parts:
                return parts[0], None
    return None, None


def parse_place(place: dict[str, Any]) -> dict[str, Any]:
    """Fiche établissement (dict brut de EXTRACT_PLACE_JS) → champs prospect."""
    url = place.get("url") or ""
    coords = extract_coords(url)
    return {
        "name": (place.get("name") or "").strip() or None,
        "rating": parse_rating(place.get("rating_text")),
        "review_count": parse_int(place.get("reviews_text")),
        "category": (place.get("category") or "").strip() or None,
        "phone": normalize_phone(place.get("phone")),
        "website": (place.get("website") or "").strip() or None,
        "address": (place.get("address") or "").strip() or None,
        "lat": coords[0] if coords else None,
        "lng": coords[1] if coords else None,
    }


# --- Avis ------------------------------------------------------------------------------------

_REL_FR = {
    "seconde": 0,
    "minute": 0,
    "heure": 0,
    "jour": 1,
    "semaine": 7,
    "mois": 30.44,
    "an": 365.25,
}
_REL_EN = {
    "second": 0,
    "minute": 0,
    "hour": 0,
    "day": 1,
    "week": 7,
    "month": 30.44,
    "year": 365.25,
}
_REL_RE = re.compile(
    r"(?:il y a|hace)?\s*(\d+|un|une|a|an)\s+"
    r"(seconde|minute|heure|jour|semaine|mois|an|second|minute|hour|day|week|month|year)s?\b"
    r"(?:\s+ago)?",
    re.I,
)


def parse_relative_date(text: str | None, now: datetime | None = None) -> datetime | None:
    """'il y a 2 mois' / 'a week ago' / 'Modifié il y a 3 jours' → date approximative."""
    if not text:
        return None
    now = now or datetime.now(UTC).replace(tzinfo=None)
    m = _REL_RE.search(text)
    if not m:
        return None
    qty_s, unit = m.group(1).lower(), m.group(2).lower()
    qty = 1 if qty_s in ("un", "une", "a", "an") else int(qty_s)
    days = _REL_FR.get(unit)
    if days is None:
        days = _REL_EN.get(unit)
    if days is None:
        return None
    return now - timedelta(days=qty * days)


def parse_review(raw: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """Avis brut (EXTRACT_REVIEWS_JS) → ligne `reviews`."""
    review_id = (raw.get("review_id") or "").strip()
    if not review_id:
        return None
    rating = None
    label = raw.get("rating_label") or ""
    m = re.search(r"([1-5])", label)
    if m:
        rating = int(m.group(1))
    owner = (raw.get("owner_response") or "").strip()
    date_text = (raw.get("date_text") or "").strip()
    return {
        "review_id": review_id,
        "author": (raw.get("author") or "").strip() or None,
        "rating": rating,
        "date_text": date_text or None,
        "date": parse_relative_date(date_text, now=now),
        "text": (raw.get("text") or "").strip() or None,
        "has_owner_response": 1 if owner else 0,
        "owner_response_text": _strip_owner_prefix(owner) or None,
    }


def _strip_owner_prefix(text: str) -> str:
    for prefix in ("Réponse du propriétaire", "Response from the owner"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    # La ligne suivante est souvent la date de la réponse ('il y a 2 mois')
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if lines and _REL_RE.fullmatch(lines[0].replace("Modifié ", "").strip()):
        lines = lines[1:]
    return "\n".join(lines)
