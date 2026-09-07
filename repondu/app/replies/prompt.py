"""B1 — Construction du prompt de rédaction. Code pur, testé."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

MAX_WORDS_POSITIVE = 60  # ≥ 4★
MAX_WORDS_NEGATIVE = 90  # ≤ 3★


@dataclass(frozen=True)
class Profile:
    """Sous-ensemble du profil établissement nécessaire à la rédaction (B3 fournit la table)."""

    name: str
    cuisine_type: str | None = None
    tone: str = "chaleureux et professionnel"
    signature: str | None = None
    manager_first_name: str | None = None
    never_say: str | None = None
    use_tutoiement: bool = False
    contact_email: str | None = None


@dataclass(frozen=True)
class ReviewInput:
    rating: int
    text: str
    author: str | None = None
    date_text: str | None = None


class ReplyOutput(BaseModel):
    """Sortie structurée demandée au modèle."""

    reply: str = Field(description="La réponse publiable, sans signature.")
    detail_reused: str = Field(
        description="L'élément concret de l'avis repris dans la réponse (quelques mots)."
    )


class ReviewVerdict(BaseModel):
    """Relecture d'une réponse à un avis ≤ 2★."""

    ok: bool
    issues: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """Tu rédiges, au nom d'un restaurant, des réponses publiques aux avis Google. \
Tu écris en français, dans un style naturel et humain, comme le ferait le gérant. Chaque réponse \
est publiée telle quelle sous l'avis, visible par tous.

Règles absolues :
- Reprendre au moins un élément concret et précis de l'avis (un plat, un moment, un détail du \
service, un prénom si le client le donne). Jamais de réponse interchangeable.
- Jamais d'excuse générique du type « nous sommes désolés pour la gêne occasionnée », \
« toutes nos excuses pour ce désagrément ». Si l'on regrette quelque chose, dire quoi, précisément.
- Jamais de promesse commerciale : pas de remise, de geste, de repas offert, de compensation, \
de « la prochaine fois c'est pour nous ».
- Jamais de contestation agressive, jamais de mise en cause du client, jamais de détail sur \
d'autres clients ou sur le personnel nommément.
- Pas de mention d'intelligence artificielle. Pas d'emoji. Pas de majuscules criardes. Pas de \
formule marketing.
- Ne pas signer : la signature est ajoutée séparément.
- Respecter le nombre de mots maximal indiqué.

Ton par note :
- 5 étoiles : chaleureux et court, un remerciement sincère qui rebondit sur ce que le client a aimé.
- 4 étoiles : remerciement, reprise du détail apprécié, et prise en compte simple de la réserve \
éventuelle sans se justifier.
- 3 étoiles : constructif, on remercie pour la franchise, on reconnaît le point précis soulevé et \
on dit ce que l'on retient, sans promesse.
- 1 ou 2 étoiles : calme, factuel, jamais sur la défensive. On reconnaît ce que le client décrit \
sans dramatiser ni minimiser, et on l'invite à échanger hors ligne (par email ou téléphone) pour \
comprendre ce qui s'est passé.

Réponds uniquement avec le JSON demandé."""


def rating_rules(rating: int) -> tuple[str, int]:
    """Consigne spécifique et nombre de mots maximal pour cette note."""
    if rating >= 5:
        return "5 étoiles : chaleureux et court.", MAX_WORDS_POSITIVE
    if rating == 4:
        return "4 étoiles : remerciement, reprise du détail apprécié.", MAX_WORDS_POSITIVE
    if rating == 3:
        return "3 étoiles : constructif, on reconnaît le point précis soulevé.", MAX_WORDS_NEGATIVE
    return (
        "1 ou 2 étoiles : calme, factuel, invitation à échanger hors ligne.",
        MAX_WORDS_NEGATIVE,
    )


def build_user_message(
    profile: Profile, review: ReviewInput, feedback: list[str] | None = None
) -> str:
    consigne, max_words = rating_rules(review.rating)
    lines = [
        "## Établissement",
        f"Nom : {profile.name}",
    ]
    if profile.cuisine_type:
        lines.append(f"Type de cuisine : {profile.cuisine_type}")
    lines.append(f"Ton souhaité : {profile.tone}")
    if profile.manager_first_name:
        lines.append(
            f"Prénom du gérant (peut se présenter à la première personne) : "
            f"{profile.manager_first_name}"
        )
    lines.append("Tutoiement autorisé." if profile.use_tutoiement else "Vouvoiement obligatoire.")
    if profile.never_say:
        lines.append(f"À ne jamais dire ni évoquer : {profile.never_say}")
    if review.rating <= 2 and profile.contact_email:
        lines.append(f"Contact hors ligne à proposer : {profile.contact_email}")
    lines += [
        "",
        "## Avis à traiter",
        f"Note : {review.rating}/5",
    ]
    if review.author:
        lines.append(f"Auteur : {review.author}")
    if review.date_text:
        lines.append(f"Date : {review.date_text}")
    lines += [
        "Texte de l'avis :",
        '"""',
        review.text.strip() or "(avis sans texte)",
        '"""',
        "",
        "## Consigne",
        consigne,
        f"Maximum {max_words} mots. Sans signature.",
    ]
    if feedback:
        lines += ["", "## Relecture de la version précédente : à corriger"]
        lines += [f"- {f}" for f in feedback]
    return "\n".join(lines)


REVIEWER_SYSTEM = """Tu relis une réponse publique à un avis Google négatif (1 ou 2 étoiles) \
avant publication. Tu vérifies qu'elle est calme et factuelle, qu'elle ne conteste pas \
agressivement le client, qu'elle ne contient ni excuse générique ni promesse commerciale \
(remise, geste, repas offert), qu'elle ne mentionne pas d'autres clients ou d'employés nommément, \
qu'elle invite à échanger hors ligne et qu'elle reprend un élément concret de l'avis. \
Réponds uniquement avec le JSON demandé : ok=true si publiable telle quelle, sinon ok=false \
avec la liste courte des problèmes, en français."""


def build_reviewer_message(review: ReviewInput, reply_text: str) -> str:
    return (
        f'## Avis ({review.rating}/5)\n"""\n{review.text.strip()}\n"""\n\n'
        f'## Réponse proposée\n"""\n{reply_text.strip()}\n"""'
    )
