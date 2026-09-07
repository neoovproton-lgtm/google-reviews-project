"""C1 — Rédaction des messages de prospection (mail J0, relance J+3, dernier J+8, DM, SMS).

Le modèle rédige les parties libres ; le code assemble, vérifie et ajoute exemples, signature et
opt-out. Aucune formule générique tolérée.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm import LLM
from app.models import Prospect, Review
from app.replies.checks import TUTOIEMENT_RE, normalize, word_count
from app.replies.generator import generate_reply
from app.replies.prompt import Profile, ReviewInput
from app.replies.safety import safety_flags

log = logging.getLogger(__name__)

MAX_WORDS_EMAIL = 120
MAX_WORDS_FOLLOWUP = 80
MAX_WORDS_LAST = 50
MAX_WORDS_DM = 80
MAX_ATTEMPTS = 2

GENERIC_PHRASES = (
    "j'espere que ce message vous trouve",
    "j'espere que vous allez bien",
    "je me permets de vous",
    "n'hesitez pas",
    "au plaisir de vous lire",
    "dans l'attente de votre retour",
    "restant a votre disposition",
    "je reste a votre disposition",
    "leader",
    "innovant",
    "revolutionn",
    "solution",
    "booster",
    "optimiser votre",
    "e-reputation",
    "opportunite",
    "sans plus attendre",
    "intelligence artificielle",
    " ia ",
    "algorithme",
    "cher client",
    "madame, monsieur",
    "cordialement",
)


# --- Données d'entrée -------------------------------------------------------------------------


@dataclass(frozen=True)
class ProspectFacts:
    name: str
    city: str | None
    category: str | None
    unanswered_last_30d: int
    negative_unanswered_last_30d: int
    reviews_per_month: float | None
    response_rate: float | None
    contact: str | None = None

    @property
    def constat(self) -> str:
        n, neg = self.unanswered_last_30d, self.negative_unanswered_last_30d
        s = f"{n} avis sans réponse ces 30 jours"
        if neg:
            s += f", dont {neg} négatif{'s' if neg > 1 else ''}"
        return s


def facts_from_prospect(p: Prospect) -> ProspectFacts:
    return ProspectFacts(
        name=p.name,
        city=p.city,
        category=p.category,
        unanswered_last_30d=p.unanswered_last_30d or 0,
        negative_unanswered_last_30d=p.negative_unanswered_last_30d or 0,
        reviews_per_month=p.reviews_per_month,
        response_rate=p.response_rate,
        contact=p.email,
    )


def prospect_profile(p: Prospect) -> Profile:
    """Profil par défaut pour rédiger des exemples de réponse au nom d'un prospect."""
    return Profile(
        name=p.name,
        cuisine_type=p.category,
        tone="chaleureux et professionnel",
        signature=f"L'équipe de {p.name}",
        contact_email=p.email,
    )


def pick_example_reviews(
    session: Session, prospect_id: int, n: int = 2, exclude_ids: tuple[int, ...] = ()
) -> list[Review]:
    """Avis récents sans réponse, sans drapeau de sécurité, un positif et un négatif si possible."""
    stmt = (
        select(Review)
        .where(Review.prospect_id == prospect_id, Review.has_owner_response == 0)
        .order_by(Review.date.desc().nullslast(), Review.id.desc())
    )
    candidates = [
        r
        for r in session.scalars(stmt)
        if r.id not in exclude_ids and (r.text or "").strip() and not safety_flags(r.text)
    ]
    positives = [r for r in candidates if (r.rating or 0) >= 4]
    negatives = [r for r in candidates if (r.rating or 0) <= 3]
    chosen: list[Review] = []
    if n >= 2 and positives and negatives:
        chosen = [positives[0], negatives[0]]
    for r in candidates:
        if len(chosen) >= n:
            break
        if r not in chosen:
            chosen.append(r)
    return chosen[:n]


def build_examples(
    session: Session, prospect: Prospect, llm: LLM, n: int = 2, exclude_ids: tuple[int, ...] = ()
) -> list[dict]:
    profile = prospect_profile(prospect)
    out = []
    for r in pick_example_reviews(session, prospect.id, n, exclude_ids):
        gen = generate_reply(
            profile,
            ReviewInput(
                rating=r.rating or 3, text=r.text or "", author=r.author, date_text=r.date_text
            ),
            llm=llm,
        )
        out.append(
            {
                "review_id": r.id,
                "author": r.author,
                "rating": r.rating,
                "text": r.text,
                "reply": gen.text,
                "needs_human": gen.needs_human,
            }
        )
    return out


# --- Prompts -------------------------------------------------------------------------------


class EmailOutput(BaseModel):
    subject: str = Field(description="Objet court, concret, sans majuscules criardes.")
    opening: str = Field(description="Constat chiffré + ce que fait Répondu. 2 à 4 phrases.")
    closing: str = Field(
        description="Proposition d'essai gratuit 30 jours et l'action : répondre OK."
    )


class ShortOutput(BaseModel):
    text: str


EMAIL_SYSTEM = """Tu écris des emails de prospection pour Répondu, un service qui rédige et publie \
les réponses aux avis Google des restaurants, dans le ton de l'établissement, en moins de 24 h. \
Le destinataire est le gérant d'un restaurant. Tu écris en français, au vouvoiement, comme une \
personne réelle qui a regardé sa fiche Google, pas comme un vendeur.

Règles :
- Court. Concret. Une idée par phrase. Aucune formule creuse (pas de « j'espère que ce message \
vous trouve bien », « je me permets », « n'hésitez pas », « au plaisir de vous lire »).
- Aucun jargon ni superlatif : pas de « solution », « innovant », « booster », « e-réputation », \
« leader », « IA ». On parle de ce qui est fait, pas de technologie.
- Le constat chiffré fourni doit apparaître tel quel (mêmes nombres).
- Ne pas inventer de chiffres ni de faits. Ne pas citer les avis : ils sont ajoutés par le code \
juste après l'ouverture, annonce-les simplement.
- Pas de signature, pas de formule de politesse finale (ajoutées par le code). Pas d'emoji.
- Une seule action demandée : répondre « OK ».
Réponds uniquement avec le JSON demandé."""


def _facts_block(facts: ProspectFacts) -> str:
    lines = [
        "## Prospect",
        f"Restaurant : {facts.name}" + (f" ({facts.city})" if facts.city else ""),
    ]
    if facts.category:
        lines.append(f"Type : {facts.category}")
    lines.append(f"Constat chiffré à reprendre tel quel : « {facts.constat} »")
    if facts.reviews_per_month:
        lines.append(f"Rythme : environ {facts.reviews_per_month:.0f} avis par mois")
    if facts.response_rate is not None:
        lines.append(f"Taux de réponse actuel : {facts.response_rate * 100:.0f} %")
    return "\n".join(lines)


def build_email_user(
    facts: ProspectFacts, n_examples: int, settings: Settings, feedback=None
) -> str:
    lines = [
        _facts_block(facts),
        "",
        "## Ce qu'il faut écrire",
        f"- `subject` : objet court et concret (ex. « {facts.unanswered_last_30d} avis sans "
        f"réponse chez {facts.name} »).",
        "- `opening` : le constat chiffré, puis ce que fait Répondu (rédige et publie les "
        "réponses dans le ton du restaurant, sous 24 h, le gérant garde un droit de veto), puis "
        f"une phrase qui annonce les {n_examples} exemples rédigés pour ses vrais avis, placés "
        "juste après.",
        f"- `closing` : essai gratuit 30 jours, sans engagement ({settings.outreach_price_text}), "
        f"et une seule action : répondre « OK ».",
        f"- `opening` + `closing` : {MAX_WORDS_EMAIL} mots maximum au total.",
    ]
    if feedback:
        lines += ["", "## À corriger par rapport à la version précédente"] + [
            f"- {f}" for f in feedback
        ]
    return "\n".join(lines)


def build_followup_user(facts: ProspectFacts, example: dict | None, feedback=None) -> str:
    lines = [
        _facts_block(facts),
        "",
        "## Ce qu'il faut écrire",
        "Relance 3 jours après un premier mail resté sans réponse. `text` : 2 à 4 phrases, "
        f"{MAX_WORDS_FOLLOWUP} mots maximum. Rappeler en une demi-phrase la proposition (réponses "
        "rédigées et publiées, essai gratuit 30 jours), "
        + (
            f"annoncer qu'un nouvel avis récent de {example.get('author') or 'un client'} "
            f"({example.get('rating')}★) a reçu une réponse rédigée, placée juste après. "
            if example
            else ""
        )
        + "Terminer par l'unique action : répondre « OK ». Pas de reproche pour le silence.",
    ]
    if feedback:
        lines += ["", "## À corriger"] + [f"- {f}" for f in feedback]
    return "\n".join(lines)


def build_last_user(facts: ProspectFacts, feedback=None) -> str:
    lines = [
        _facts_block(facts),
        "",
        "## Ce qu'il faut écrire",
        f"Dernier message, 8 jours après le premier. `text` : 2 phrases, {MAX_WORDS_LAST} mots "
        "maximum. Dire simplement que c'est le dernier message, que la proposition d'essai "
        "gratuit reste ouverte, et qu'il suffit de répondre « OK ». Aucune pression, aucune "
        "culpabilisation.",
    ]
    if feedback:
        lines += ["", "## À corriger"] + [f"- {f}" for f in feedback]
    return "\n".join(lines)


def build_dm_user(facts: ProspectFacts, n_examples: int, feedback=None) -> str:
    lines = [
        _facts_block(facts),
        "",
        "## Ce qu'il faut écrire",
        f"Message privé Instagram/Facebook au restaurant. `text` : {MAX_WORDS_DM} mots maximum, "
        "ton direct et cordial, vouvoiement. Le constat chiffré, ce que fait Répondu en une "
        f"phrase, l'annonce des {n_examples} réponses rédigées pour ses vrais avis (placées "
        "juste après), l'essai gratuit 30 jours, et l'unique action : répondre « OK ».",
    ]
    if feedback:
        lines += ["", "## À corriger"] + [f"- {f}" for f in feedback]
    return "\n".join(lines)


# --- Vérifications et assemblage -------------------------------------------------------------


def check_text(
    text: str,
    facts: ProspectFacts,
    max_words: int,
    require_ok: bool = True,
    require_constat: bool = True,
) -> list[str]:
    issues: list[str] = []
    n = word_count(text)
    if n > max_words:
        issues.append(f"trop long : {n} mots pour {max_words} maximum")
    if n < 10:
        issues.append("trop court")
    norm = " " + normalize(text) + " "
    for phrase in GENERIC_PHRASES:
        if phrase in norm:
            issues.append(f"formule générique : « {phrase.strip()} »")
    if TUTOIEMENT_RE.search(text):
        issues.append("tutoiement")
    if require_ok and not re.search(r"«\s*OK\s*»|\"OK\"|\bOK\b", text):
        issues.append("l'action « répondre OK » manque")
    if require_constat:
        if not re.search(rf"\b{facts.unanswered_last_30d}\b", text):
            issues.append("le nombre d'avis sans réponse manque")
        neg = facts.negative_unanswered_last_30d
        if neg and not re.search(rf"\b{neg}\b(?!\s*jours)", text):
            issues.append("le nombre d'avis négatifs manque")
    return issues


def format_example(e: dict) -> str:
    stars = "★" * int(e.get("rating") or 0)
    author = e.get("author") or "un client"
    text = (e.get("text") or "").strip().replace("\n", " ")
    if len(text) > 280:
        text = text[:277] + "…"
    return f"— Avis de {author} ({stars}) : « {text} »\n  Réponse proposée : {e['reply'].strip()}"


def optout_line(channel: str) -> str:
    if channel == "email":
        return "Pour ne plus recevoir nos messages, répondez simplement « STOP »."
    return "Répondez STOP pour ne plus être contacté."


def assemble_email(opening: str, examples: list[dict], closing: str, settings: Settings) -> str:
    parts = ["Bonjour,", "", opening.strip()]
    if examples:
        parts += ["", "\n\n".join(format_example(e) for e in examples)]
    parts += ["", closing.strip(), "", settings.outreach_signature, "", optout_line("email")]
    return "\n".join(parts)


@dataclass
class Composed:
    body: str
    subject: str | None = None
    issues: list[str] = field(default_factory=list)
    attempts: int = 1
    free_text: str = ""  # partie rédigée par le modèle (pour l'évaluation)


def _loop(llm: LLM, system: str, user_builder, schema, check, max_attempts: int = MAX_ATTEMPTS):
    best = None
    feedback = None
    for attempt in range(1, max_attempts + 1):
        out = llm.complete_json(system, user_builder(feedback), schema, max_tokens=800)
        issues = check(out)
        if best is None or len(issues) < len(best[1]):
            best = (out, issues, attempt)
        if not issues:
            break
        feedback = issues
    out, issues, _ = best
    return out, issues, attempt


def compose_first_email(
    facts: ProspectFacts, examples: list[dict], llm: LLM, settings: Settings | None = None
) -> Composed:
    settings = settings or get_settings()
    out, issues, attempts = _loop(
        llm,
        EMAIL_SYSTEM,
        lambda fb: build_email_user(facts, len(examples), settings, fb),
        EmailOutput,
        lambda o: (
            check_text(f"{o.opening}\n{o.closing}", facts, MAX_WORDS_EMAIL)
            + (["objet vide"] if not o.subject.strip() else [])
            + (["objet trop long"] if len(o.subject) > 90 else [])
        ),
    )
    return Composed(
        body=assemble_email(out.opening, examples, out.closing, settings),
        subject=out.subject.strip(),
        issues=issues,
        attempts=attempts,
        free_text=f"{out.opening}\n{out.closing}",
    )


def compose_followup(
    facts: ProspectFacts, example: dict | None, llm: LLM, settings: Settings | None = None
) -> Composed:
    settings = settings or get_settings()
    out, issues, attempts = _loop(
        llm,
        EMAIL_SYSTEM,
        lambda fb: build_followup_user(facts, example, fb),
        ShortOutput,
        lambda o: check_text(o.text, facts, MAX_WORDS_FOLLOWUP, require_constat=False),
    )
    parts = ["Bonjour,", "", out.text.strip()]
    if example:
        parts += ["", format_example(example)]
    parts += ["", settings.outreach_signature, "", optout_line("email")]
    return Composed(body="\n".join(parts), issues=issues, attempts=attempts, free_text=out.text)


def compose_last(facts: ProspectFacts, llm: LLM, settings: Settings | None = None) -> Composed:
    settings = settings or get_settings()
    out, issues, attempts = _loop(
        llm,
        EMAIL_SYSTEM,
        lambda fb: build_last_user(facts, fb),
        ShortOutput,
        lambda o: check_text(o.text, facts, MAX_WORDS_LAST, require_constat=False),
    )
    body = "\n".join(
        [
            "Bonjour,",
            "",
            out.text.strip(),
            "",
            settings.outreach_signature,
            "",
            optout_line("email"),
        ]
    )
    return Composed(body=body, issues=issues, attempts=attempts, free_text=out.text)


def compose_dm(
    facts: ProspectFacts, examples: list[dict], llm: LLM, settings: Settings | None = None
) -> Composed:
    settings = settings or get_settings()
    out, issues, attempts = _loop(
        llm,
        EMAIL_SYSTEM,
        lambda fb: build_dm_user(facts, len(examples), fb),
        ShortOutput,
        lambda o: check_text(o.text, facts, MAX_WORDS_DM),
    )
    parts = [out.text.strip()]
    if examples:
        parts += ["", "\n\n".join(format_example(e) for e in examples)]
    parts += ["", settings.outreach_signature]
    return Composed(body="\n".join(parts), issues=issues, attempts=attempts, free_text=out.text)


def compose_sms(facts: ProspectFacts, link: str) -> Composed:
    """SMS B2B court, sans LLM. Cible ≤ 320 caractères (2 segments)."""
    name = facts.name if len(facts.name) <= 30 else facts.name[:29] + "…"
    body = (
        f"{name} : {facts.constat}. Nous avons rédigé 2 réponses pour vous : {link} "
        f"Essai gratuit 30 jours, répondez OK. STOP pour ne plus recevoir."
    )
    issues = ["SMS trop long"] if len(body) > 320 else []
    return Composed(body=body, issues=issues)


def compose_form_message(facts: ProspectFacts, examples: list[dict], settings: Settings) -> str:
    """Message pour un formulaire de contact : sans LLM, texte court + exemples + contact retour."""
    parts = [
        f"Bonjour, en regardant la fiche Google de {facts.name}, j'ai compté {facts.constat}. "
        "Répondu rédige et publie les réponses à vos avis, dans votre ton, sous 24 h, avec votre "
        "veto. Voici deux réponses déjà rédigées pour vos vrais avis :",
        "",
        "\n\n".join(format_example(e) for e in examples),
        "",
        "Essai gratuit 30 jours, sans engagement. Si cela vous intéresse, répondez simplement "
        f"« OK » à {settings.outreach_reply_address}.",
        "",
        settings.outreach_signature,
    ]
    return "\n".join(parts)
