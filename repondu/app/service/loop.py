"""D3 — Boucle de service : rafraîchir les avis, rédiger, prévenir le client, veto, publier, log."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm import LLM, get_llm
from app.models import (
    ClientMessage,
    EmailEvent,
    Establishment,
    OnboardingStatus,
    Reply,
    ReplyStatus,
    Review,
    utcnow,
)
from app.outreach.inbox import InboundMail, strip_quotes
from app.replies.checks import normalize
from app.replies.service import decide, draft_reply, format_draft_message, reviews_to_answer
from app.service.mailer import send_client_email, send_client_sms, send_client_telegram
from app.telegram.client import Telegram

log = logging.getLogger(__name__)

TAG_RE = re.compile(r"\[R\S{0,2}pondu #(\d+)\]", re.I)
VETO_RE = re.compile(r"\b(veto|non|refus|stop|pas d'accord|ne publiez pas|ne pas publier)\b")
APPROVE_RE = re.compile(r"^(ok|oui|d'accord|parfait|valide|go|c'est bon)\b")


# --- Rafraîchissement des avis ----------------------------------------------------------------


def known_review_ids(session: Session, prospect_id: int) -> set[str]:
    return set(session.scalars(select(Review.review_id).where(Review.prospect_id == prospect_id)))


def refresh_reviews(
    session: Session,
    est: Establishment,
    now: datetime | None = None,
    settings: Settings | None = None,
    scraper=None,
) -> list[Review]:
    """Relit la fiche Maps du prospect lié ; retourne les avis nouveaux (inconnus avant)."""
    now = now or utcnow()
    settings = settings or get_settings()
    if est.prospect_id is None:
        return []
    before = known_review_ids(session, est.prospect_id)
    if scraper is None:
        from app.scraping.browser import browser_session
        from app.scraping.reviews import scrape_prospect

        session.commit()  # scrape_prospect ouvre ses propres transactions
        with browser_session(settings) as (context, pacer):
            scrape_prospect(context, est.prospect_id, pacer, settings)
    else:
        scraper(session, est.prospect_id)
    session.expire_all()
    est = session.get(Establishment, est.id)
    est.last_review_check_at = now
    new = [
        r
        for r in session.scalars(
            select(Review).where(Review.prospect_id == est.prospect_id).order_by(Review.id)
        )
        if r.review_id not in before
    ]
    return new


def due_for_check(session: Session, now: datetime, settings: Settings) -> list[Establishment]:
    cutoff = now - timedelta(hours=settings.service_review_check_hours)
    stmt = select(Establishment).where(
        Establishment.active == 1,
        Establishment.onboarding_status == OnboardingStatus.MANAGER_ADDED,
        Establishment.prospect_id.is_not(None),
        (Establishment.last_review_check_at.is_(None))
        | (Establishment.last_review_check_at <= cutoff),
    )
    return list(session.scalars(stmt))


# --- Brouillons et notification client ------------------------------------------------------


def notify_client(
    session: Session,
    est: Establishment,
    review: Review,
    reply: Reply,
    telegram: Telegram | None = None,
    settings: Settings | None = None,
) -> list[ClientMessage]:
    """Telegram si lié, sinon mail ; SMS court en plus si mobile. Journalisé."""
    settings = settings or get_settings()
    sent: list[ClientMessage] = []
    text = format_draft_message(est, review, reply)
    if est.telegram_chat_id:
        rec = send_client_telegram(
            session,
            est,
            "draft",
            text,
            buttons=[[("✅ Approuver", f"approve:{reply.id}"), ("❌ Veto", f"reject:{reply.id}")]],
            reply_id=reply.id,
            telegram=telegram,
        )
        if rec:
            sent.append(rec)
    elif est.contact_email:
        subject = f"[Répondu #{reply.id}] Nouvel avis {review.rating or '?'}★ — réponse proposée"
        body = (
            f"{text}\n\n"
            "Pour approuver tout de suite : répondez « OK ». Pour bloquer : répondez « VETO ».\n"
            "Sans réponse de votre part, la réponse est publiée dans 24 h."
            if not reply.needs_human
            else f"{text}\n\nCette réponse ne sera publiée qu'avec votre « OK » en réponse "
            "à ce mail."
        )
        sent.append(
            send_client_email(
                session, est, "draft", subject, body, reply_id=reply.id, settings=settings
            )
        )
    if est.mobile_phone and not est.telegram_chat_id:
        sms = (
            f"Répondu : nouvel avis {review.rating or '?'}★ de {review.author or 'un client'} sur "
            f"{est.name}. Réponse envoyée par mail, publiée dans {est.auto_publish_delay_h} h "
            "sauf veto."
        )
        rec = send_client_sms(session, est, "draft", sms, reply_id=reply.id, settings=settings)
        if rec:
            sent.append(rec)
    return sent


def process_establishment(
    session: Session,
    est: Establishment,
    llm: LLM | None = None,
    telegram: Telegram | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[Reply]:
    """Rédige un brouillon pour chaque avis sans réponse ni brouillon, et prévient le client."""
    settings = settings or get_settings()
    llm = llm or get_llm()
    drafts = []
    for review in reviews_to_answer(session, est):
        reply = draft_reply(session, est, review, llm)
        notify_client(session, est, review, reply, telegram=telegram, settings=settings)
        drafts.append(reply)
    return drafts


def run_service_cycle(
    session: Session,
    now: datetime | None = None,
    llm: LLM | None = None,
    telegram: Telegram | None = None,
    settings: Settings | None = None,
    scraper=None,
    force: bool = False,
) -> dict:
    """Pour chaque client dû : rafraîchir les avis, rédiger, notifier. Utilisé par le scheduler."""
    now = now or utcnow()
    settings = settings or get_settings()
    out = {"checked": 0, "new_reviews": 0, "drafts": 0, "errors": 0}
    ests = (
        list(session.scalars(select(Establishment).where(Establishment.active == 1)))
        if force
        else due_for_check(session, now, settings)
    )
    for est in ests:
        try:
            new = refresh_reviews(session, est, now, settings, scraper=scraper)
            out["checked"] += 1
            out["new_reviews"] += len(new)
            est = session.get(Establishment, est.id)
            out["drafts"] += len(process_establishment(session, est, llm, telegram, settings, now))
            session.flush()
        except Exception as exc:  # noqa: BLE001
            log.exception("service %s", est.name)
            out["errors"] += 1
            out[f"error_{est.id}"] = f"{type(exc).__name__}: {exc}"
    return out


# --- Veto / approbation par réponse mail -----------------------------------------------------


def process_client_reply(
    session: Session,
    mail: InboundMail,
    now: datetime | None = None,
) -> str:
    """`approved` | `rejected` | `ignored` | `duplicate` | `unknown` selon le mail reçu."""
    now = now or utcnow()
    if mail.message_id and session.scalar(
        select(EmailEvent.id).where(
            EmailEvent.external_id == mail.message_id, EmailEvent.source == "service"
        )
    ):
        return "duplicate"
    m = TAG_RE.search(mail.subject or "")
    if not m:
        return "unknown"
    reply = session.get(Reply, int(m.group(1)))
    if reply is None:
        return "unknown"
    own = normalize(strip_quotes(mail.body))
    first = own.splitlines()[0] if own.splitlines() else ""
    kind = "ignored"
    if reply.status == ReplyStatus.PENDING:
        if VETO_RE.search(own):
            decide(session, reply, "reject", by=f"mail:{mail.from_address}")
            kind = "rejected"
        elif APPROVE_RE.match(first):
            decide(session, reply, "approve", by=f"mail:{mail.from_address}")
            kind = "approved"
    session.add(
        EmailEvent(
            type=f"client_{kind}",
            source="service",
            external_id=mail.message_id,
            payload={"from": mail.from_address, "reply_id": reply.id},
            occurred_at=now,
        )
    )
    return kind


# --- Publication (semi-manuelle) et log -----------------------------------------------------


def to_publish(session: Session, establishment_id: int | None = None) -> list[Reply]:
    stmt = select(Reply).where(Reply.status == ReplyStatus.APPROVED)
    if establishment_id:
        stmt = stmt.where(Reply.establishment_id == establishment_id)
    return list(session.scalars(stmt.order_by(Reply.decided_at, Reply.id)))


def mark_published(
    session: Session, reply: Reply, now: datetime | None = None, by: str = "humain"
) -> Reply:
    if reply.status != ReplyStatus.APPROVED:
        raise ValueError(f"brouillon {reply.status}, non approuvé")
    now = now or utcnow()
    reply.status = ReplyStatus.PUBLISHED
    reply.published_at = now
    reply.decision_by = f"{reply.decision_by or ''} · publié par {by}".strip(" ·")
    review = reply.review
    review.has_owner_response = 1
    review.owner_response_text = reply.text
    return reply


def service_stats(session: Session, now: datetime | None = None) -> dict:
    now = now or utcnow()
    published = list(session.scalars(select(Reply).where(Reply.status == ReplyStatus.PUBLISHED)))
    delays = [
        (r.published_at - r.review.date).total_seconds() / 3600
        for r in published
        if r.published_at and r.review.date
    ]
    counts = dict(session.execute(select(Reply.status, func.count()).group_by(Reply.status)).all())
    active = session.scalar(
        select(func.count())
        .select_from(Establishment)
        .where(
            Establishment.onboarding_status == OnboardingStatus.MANAGER_ADDED,
            Establishment.active == 1,
        )
    )
    by_onboarding = dict(
        session.execute(
            select(Establishment.onboarding_status, func.count()).group_by(
                Establishment.onboarding_status
            )
        ).all()
    )
    return {
        "clients_active": int(active or 0),
        "onboarding": {k: int(v) for k, v in by_onboarding.items()},
        "replies": {k: int(v) for k, v in counts.items()},
        "published": len(published),
        "median_delay_h": round(median(delays), 1) if delays else None,
        "pct_under_24h": round(sum(1 for d in delays if d <= 24) / len(delays), 3)
        if delays
        else None,
        "pending_veto": int(counts.get(ReplyStatus.PENDING, 0)),
        "to_publish": int(counts.get(ReplyStatus.APPROVED, 0)),
    }
