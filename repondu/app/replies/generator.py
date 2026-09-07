"""B1 — Génération d'une réponse : appel Sonnet, vérifications, relecture ≤ 2★, régénération."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.llm import LLM, get_llm
from app.replies.checks import append_signature, check_reply, word_count
from app.replies.prompt import (
    REVIEWER_SYSTEM,
    SYSTEM_PROMPT,
    Profile,
    ReplyOutput,
    ReviewInput,
    ReviewVerdict,
    build_reviewer_message,
    build_user_message,
)

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 2


@dataclass
class GeneratedReply:
    body: str  # sans signature
    text: str  # publiable (avec signature)
    detail_reused: str
    word_count: int
    issues: list[str] = field(default_factory=list)
    attempts: int = 1
    needs_human: bool = False
    model: str = ""
    safety_flags: list[str] = field(default_factory=list)


def generate_reply(
    profile: Profile,
    review: ReviewInput,
    llm: LLM | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> GeneratedReply:
    """Rédige une réponse conforme, ou la meilleure tentative marquée `needs_human`.

    - Vérifications en code après chaque tentative (`checks.check_reply`).
    - Pour les avis ≤ 2★ : relecture par un second appel (PRD §5), ses remarques servent de
      retour pour la régénération.
    """
    llm = llm or get_llm()
    best: GeneratedReply | None = None
    feedback: list[str] | None = None
    for attempt in range(1, max_attempts + 1):
        out = llm.complete_json(
            SYSTEM_PROMPT, build_user_message(profile, review, feedback), ReplyOutput
        )
        body = out.reply.strip()
        issues = check_reply(body, out.detail_reused, profile, review)
        if not issues and review.rating <= 2:
            verdict = llm.complete_json(
                REVIEWER_SYSTEM, build_reviewer_message(review, body), ReviewVerdict, max_tokens=512
            )
            if not verdict.ok:
                issues = [f"relecture : {i}" for i in verdict.issues] or ["relecture : refusée"]
        candidate = GeneratedReply(
            body=body,
            text=append_signature(body, profile),
            detail_reused=out.detail_reused.strip(),
            word_count=word_count(body),
            issues=issues,
            attempts=attempt,
            model=getattr(llm, "model", ""),
        )
        if best is None or len(issues) < len(best.issues):
            best = candidate
        if not issues:
            break
        log.info("tentative %d rejetée : %s", attempt, issues)
        feedback = issues
    assert best is not None
    best.attempts = attempt
    best.needs_human = bool(best.issues)
    return best
