"""B2 — Filtre de sécurité : avis sensibles → toujours validation humaine. Code pur, sans LLM."""

from __future__ import annotations

import re

from app.replies.checks import normalize

# Lexique sur texte normalisé (minuscules, sans accents). Préfixes volontaires pour couvrir
# les flexions (« intoxiqu » → intoxiqué, intoxication…).
CATEGORIES: dict[str, tuple[str, ...]] = {
    "hygiene": (
        "hygiene",
        "insalubr",
        "cafard",
        "blatte",
        "rat ",
        "rats",
        "souris",
        "moisi",
        "perime",
        "cheveu",
        "sale ",
        "sales",
        "crasse",
        "degueulasse",
        "degoutant",
        "puant",
        "odeur d'egout",
        "toilettes immondes",
        "vaisselle sale",
        "nappe tachee",
        "mouche",
        "asticot",
        "pas propre",
    ),
    "intoxication": (
        "intoxi",
        "empoisonn",
        "malade",
        "vomi",
        "diarrhee",
        "gastro",
        "salmonell",
        "listeria",
        "nausee",
        "urgences",
        "hopital",
        "allerg",
        "mal au ventre",
        "mal de ventre",
    ),
    "discrimination": (
        "racis",
        "discrimin",
        "sexis",
        "homophob",
        "transphob",
        "antisemit",
        "islamophob",
        "handicap",
        "couleur de peau",
        "a cause de mon origine",
        "a cause de mes origines",
        "a cause de mon voile",
        "refuse l'entree",
        "refuse de nous servir",
        "harcel",
        "agress",
        "insult",
        "humili",
    ),
    "legal": (
        "avocat",
        "plainte",
        "proces",
        "tribunal",
        "poursuite",
        "mise en demeure",
        "huissier",
        "dgccrf",
        "signaler aux autorites",
        "signalement",
        "police",
        "gendarmerie",
        "prud",
        "arnaque",
        "escroquerie",
        "fraude",
        "vol ",
        "vole ",
        "diffam",
    ),
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def safety_flags(text: str | None) -> list[str]:
    """Catégories détectées dans l'avis (ordre stable). Vide = rien de sensible."""
    if not text:
        return []
    norm = " " + normalize(text) + " "
    return [cat for cat, words in CATEGORIES.items() if any(w in norm for w in words)]


def needs_human_review(text: str | None) -> bool:
    return bool(safety_flags(text))
