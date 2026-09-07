"""B3 — Brouillons de réponse en base : génération pour un établissement, veto/validation."""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLM, get_llm
from app.models import Establishment, Reply, ReplyStatus, Review, utcnow
from app.replies.generator import generate_reply
from app.replies.prompt import Profile, ReviewInput
from app.telegram.client import Telegram

log = logging.getLogger(__name__)


def profile_from_establishment(est: Establishment) -> Profile:
    return Profile(
        name=est.name,
        cuisine_type=est.cuisine_type,
        tone=est.tone or "chaleureux et professionnel",
        signature=est.signature,
        manager_first_name=est.manager_first_name,
        never_say=est.never_say,
        use_tutoiement=bool(est.use_tutoiement),
        contact_email=est.contact_email,
    )


def reviews_to_answer(
    session: Session, est: Establishment, limit: int | None = None
) -> list[Review]:
    """Avis du prospect lié, sans réponse du propriétaire et sans brouillon existant."""
    if est.prospect_id is None:
        return []
    answered = select(Reply.review_id).where(Reply.establishment_id == est.id)
    stmt = (
        select(Review)
        .where(
            Review.prospect_id == est.prospect_id,
            Review.has_owner_response == 0,
            Review.id.not_in(answered),
        )
        .order_by(Review.date.desc().nullslast(), Review.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def draft_reply(
    session: Session, est: Establishment, review: Review, llm: LLM | None = None
) -> Reply:
    profile = profile_from_establishment(est)
    gen = generate_reply(
        profile,
        ReviewInput(
            rating=review.rating or 3,
            text=review.text or "",
            author=review.author,
            date_text=review.date_text,
        ),
        llm=llm or get_llm(),
    )
    reply = Reply(
        review_id=review.id,
        establishment_id=est.id,
        text=gen.text,
        detail_reused=gen.detail_reused,
        word_count=gen.word_count,
        needs_human=int(gen.needs_human),
        safety_flags=gen.safety_flags,
        check_issues=gen.issues,
        model=gen.model,
        attempts=gen.attempts,
    )
    session.add(reply)
    session.flush()
    return reply


def draft_for_establishment(
    session: Session,
    est: Establishment,
    llm: LLM | None = None,
    limit: int | None = None,
    telegram: Telegram | None = None,
) -> list[Reply]:
    drafts = []
    for review in reviews_to_answer(session, est, limit):
        reply = draft_reply(session, est, review, llm)
        drafts.append(reply)
        if telegram and est.telegram_chat_id:
            notify_draft(telegram, est, review, reply)
    return drafts


def format_draft_message(est: Establishment, review: Review, reply: Reply) -> str:
    stars = "★" * (review.rating or 0) + "☆" * (5 - (review.rating or 0))
    head = f"{est.name} — avis {stars} de {review.author or 'anonyme'}"
    if review.date_text:
        head += f" ({review.date_text})"
    body = (review.text or "(sans texte)").strip()
    if len(body) > 600:
        body = body[:600] + "…"
    warn = ""
    if reply.needs_human:
        reasons = list(reply.safety_flags or []) + list(reply.check_issues or [])
        warn = "\n\n⚠️ Validation humaine obligatoire : " + ", ".join(reasons)
    delay = f"Publication automatique dans {est.auto_publish_delay_h} h sauf veto."
    if reply.needs_human:
        delay = "Pas de publication sans votre accord."
    return f"{head}\n\n« {body} »\n\nRéponse proposée :\n{reply.text}{warn}\n\n{delay}"


def notify_draft(telegram: Telegram, est: Establishment, review: Review, reply: Reply) -> None:
    telegram.send_message(
        est.telegram_chat_id or "",
        format_draft_message(est, review, reply),
        buttons=[[("✅ Approuver", f"approve:{reply.id}"), ("❌ Refuser", f"reject:{reply.id}")]],
    )


def pending_replies(session: Session, establishment_id: int | None = None) -> list[Reply]:
    stmt = select(Reply).where(Reply.status == ReplyStatus.PENDING)
    if establishment_id:
        stmt = stmt.where(Reply.establishment_id == establishment_id)
    return list(session.scalars(stmt.order_by(Reply.created_at)))


def decide(
    session: Session, reply: Reply, decision: str, by: str = "humain", text: str | None = None
) -> Reply:
    """`approve` (avec texte corrigé éventuel) ou `reject`."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision doit être approve ou reject")
    if reply.status != ReplyStatus.PENDING:
        raise ValueError(f"brouillon déjà {reply.status}")
    if decision == "approve":
        if text and text.strip():
            reply.text = text.strip()
        reply.status = ReplyStatus.APPROVED
    else:
        reply.status = ReplyStatus.REJECTED
    reply.decision_by = by
    reply.decided_at = utcnow()
    return reply


def latest_reply_for_review(session: Session, review_id: int) -> Reply | None:
    return session.scalar(
        select(Reply).where(Reply.review_id == review_id).order_by(Reply.id.desc()).limit(1)
    )


def auto_approve_due(session: Session, now=None) -> list[Reply]:
    """Veto expiré : approuve les `pending` sans `needs_human` plus vieux que le délai."""
    now = now or utcnow()
    approved = []
    for reply in pending_replies(session):
        if reply.needs_human:
            continue
        delay = timedelta(hours=reply.establishment.auto_publish_delay_h or 24)
        if reply.created_at + delay <= now:
            decide(session, reply, "approve", by="auto (délai écoulé)")
            approved.append(reply)
    return approved
