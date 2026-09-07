"""B1 — Vérifications en code pur sur une réponse générée."""

from __future__ import annotations

import re
import unicodedata

from app.replies.prompt import Profile, ReviewInput, rating_rules

GENERIC_APOLOGIES = (
    "gene occasionnee",
    "toutes nos excuses",
    "veuillez nous excuser",
    "nous nous excusons pour ce desagrement",
    "desoles pour ce desagrement",
    "desole pour ce desagrement",
    "navres pour ce desagrement",
    "pour la gene",
)
COMMERCIAL_PROMISES = (
    "offert",
    "offrirons",
    "offrira",
    "vous offrir",
    "vous offrons",
    "on vous offre",
    "gratuit",
    "remise",
    "reduction",
    "geste commercial",
    "un geste",
    "compensation",
    "rembours",
    "dedommag",
    "cadeau",
    "pour nous la prochaine",
    "la prochaine fois c'est pour nous",
    "bon d'achat",
    "-10 %",
    "-10%",
    "-20 %",
    "-20%",
)
OFFLINE_CONTACT_MARKERS = (
    "@",
    "telephone",
    "appel",
    "contacter",
    "contactez",
    "joindre",
    "echanger",
    "ecrire",
    "ecrivez",
    "mail",
    "message",
    "en direct",
    "de vive voix",
)
TUTOIEMENT_RE = re.compile(r"\b(tu|toi|tes|t'es|t'as|t'a)\b", re.I)
AI_RE = re.compile(r"\b(ia|intelligence artificielle|chatgpt|claude|assistant virtuel)\b", re.I)
EMOJI_RE = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf]")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def detail_is_in_review(detail: str, review_text: str) -> bool:
    """Le détail annoncé apparaît-il (au moins un mot significatif) dans l'avis ?"""
    words = [w for w in re.findall(r"[a-z0-9]{4,}", normalize(detail))]
    review = normalize(review_text)
    return any(w in review for w in words) if words else False


def check_reply(reply: str, detail_reused: str, profile: Profile, review: ReviewInput) -> list[str]:
    """Liste des problèmes (vide = conforme)."""
    issues: list[str] = []
    _, max_words = rating_rules(review.rating)
    n = word_count(reply)
    if n > max_words:
        issues.append(f"trop long : {n} mots pour {max_words} maximum")
    if n < 8:
        issues.append("réponse trop courte")
    norm = normalize(reply)
    if any(p in norm for p in GENERIC_APOLOGIES):
        issues.append("excuse générique")
    if any(p in norm for p in COMMERCIAL_PROMISES):
        issues.append("promesse commerciale")
    if not profile.use_tutoiement and TUTOIEMENT_RE.search(reply):
        issues.append("tutoiement")
    if AI_RE.search(reply):
        issues.append("mention d'IA")
    if EMOJI_RE.search(reply):
        issues.append("emoji")
    if profile.never_say:
        for item in re.split(r"[;,\n]", profile.never_say):
            item = item.strip()
            if len(item) >= 3 and normalize(item) in norm:
                issues.append(f"élément interdit : « {item} »")
    if profile.signature and len(profile.signature) >= 4:
        sig = re.escape(normalize(profile.signature))
        if re.search(rf"(?<![a-z0-9]){sig}(?![a-z0-9])", norm):
            issues.append("signature incluse par le modèle")
    if review.text.strip() and not detail_is_in_review(detail_reused, review.text):
        issues.append("aucun élément concret de l'avis repris")
    if review.rating <= 2 and not any(m in norm for m in OFFLINE_CONTACT_MARKERS):
        issues.append("pas d'invitation à échanger hors ligne")
    return issues


def append_signature(reply: str, profile: Profile) -> str:
    reply = reply.strip()
    if profile.signature:
        return f"{reply}\n\n{profile.signature.strip()}"
    return reply
