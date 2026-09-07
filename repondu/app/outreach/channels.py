"""C3 — Handlers des canaux non-email : formulaire (Playwright), DM (Telegram, manuel), SMS."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm import LLM
from app.models import MessageStatus, Outreach, OutreachMessage, OutreachStatus, utcnow
from app.outreach import compose
from app.outreach.email_providers import SendError
from app.outreach.sequence import (
    CHANNEL_HANDLERS,
    RunReport,
    _ensure_examples,
    close,
)
from app.outreach.sms import get_sms
from app.telegram.client import Telegram

log = logging.getLogger(__name__)

STALE_AFTER_DAYS = 10  # sans réponse après un envoi sans relance possible → done


def _single_message(
    session: Session, o: Outreach, body: str, subject: str | None = None
) -> OutreachMessage:
    msg = session.scalar(
        select(OutreachMessage).where(
            OutreachMessage.outreach_id == o.id, OutreachMessage.step == 1
        )
    )
    if msg is None:
        msg = OutreachMessage(
            outreach_id=o.id, step=1, channel=o.channel, subject=subject, body=body
        )
        session.add(msg)
        session.flush()
    return msg


def _mark_sent(o: Outreach, msg: OutreachMessage, now: datetime, provider_id: str | None) -> None:
    msg.status = MessageStatus.SENT
    msg.sent_at = now
    msg.provider_message_id = provider_id
    o.step = 1
    o.started_at = o.started_at or now
    o.last_sent_at = now
    o.next_action_at = None  # pas de relance automatique sur ce canal ; on attend une réponse


def public_link(o: Outreach, settings: Settings) -> str:
    return f"{settings.public_base_url.rstrip('/')}/p/{o.token}"


# --- Formulaire de contact ------------------------------------------------------------------


def send_form_step(
    session: Session, o: Outreach, now: datetime, llm: LLM, settings: Settings, report: RunReport
) -> bool:
    from app.outreach.forms import submit_contact_form

    if o.step >= 1:
        return False
    facts = compose.facts_from_prospect(o.prospect)
    examples = _ensure_examples(session, o, llm)
    body = compose.compose_form_message(facts, examples, settings)
    msg = _single_message(session, o, body, subject=f"{facts.constat} — {o.prospect.name}")
    if msg.status in MessageStatus.SENT_LIKE:
        _mark_sent(o, msg, msg.sent_at or now, msg.provider_message_id)
        return True
    result = submit_contact_form(
        o.contact or "",
        name=settings.outreach_signature.split(",")[0].strip(),
        email=settings.outreach_reply_address,
        phone=None,
        subject=f"Vos avis Google — {o.prospect.name}",
        message=body,
        settings=settings,
    )
    if not result.ok:
        msg.status = MessageStatus.FAILED
        msg.error = result.detail
        report.errors += 1
        close(session, o, OutreachStatus.STOPPED, note=f"formulaire : {result.detail}", now=now)
        return False
    _mark_sent(o, msg, now, None)
    msg.error = None if result.verified else result.detail
    report.sent += 1
    report.details.append(f"outreach {o.id} formulaire → {o.contact} ({result.detail})")
    return True


# --- DM Instagram / Facebook (préparés, envoyés à la main) ----------------------------------


def prepare_dm_step(
    session: Session, o: Outreach, now: datetime, llm: LLM, settings: Settings, report: RunReport
) -> bool:
    if o.step >= 1:
        return False
    facts = compose.facts_from_prospect(o.prospect)
    examples = _ensure_examples(session, o, llm)
    existing = session.scalar(
        select(OutreachMessage).where(
            OutreachMessage.outreach_id == o.id, OutreachMessage.step == 1
        )
    )
    if existing is None:
        c = compose.compose_dm(facts, examples, llm, settings)
        existing = OutreachMessage(
            outreach_id=o.id,
            step=1,
            channel=o.channel,
            body=c.body,
            status=MessageStatus.MANUAL_PENDING,
            check_issues=c.issues,
        )
        session.add(existing)
        session.flush()
        report.prepared_manual += 1
        report.details.append(f"outreach {o.id} DM {o.channel} préparé pour @{o.contact}")
    o.step = 1
    o.started_at = o.started_at or now
    o.next_action_at = None
    return True


def dm_handle_url(channel: str, contact: str) -> str:
    if channel == "instagram":
        return f"https://instagram.com/{contact}"
    return f"https://facebook.com/{contact}"


def deliver_dm_batch(
    session: Session, telegram: Telegram, chat_id: str, limit: int, now: datetime | None = None
) -> list[OutreachMessage]:
    """Livre sur Telegram jusqu'à `limit` DM en attente (non encore livrés)."""
    now = now or utcnow()
    stmt = (
        select(OutreachMessage)
        .where(
            OutreachMessage.status == MessageStatus.MANUAL_PENDING,
            OutreachMessage.delivered_at.is_(None),
        )
        .order_by(OutreachMessage.id)
        .limit(limit)
    )
    delivered = []
    for msg in session.scalars(stmt):
        o = msg.outreach
        telegram.send_message(
            chat_id,
            f"DM #{msg.id} · {o.channel} · {o.prospect.name}\n"
            f"{dm_handle_url(o.channel, o.contact or '')}\n\n"
            f"{msg.body}\n\nUne fois envoyé : /envoye {msg.id}",
        )
        msg.delivered_at = now
        delivered.append(msg)
    return delivered


def mark_manual_sent(
    session: Session, message_id: int, now: datetime | None = None
) -> OutreachMessage | None:
    now = now or utcnow()
    msg = session.get(OutreachMessage, message_id)
    if msg is None or msg.status != MessageStatus.MANUAL_PENDING:
        return None
    msg.status = MessageStatus.MANUAL_SENT
    msg.sent_at = now
    o = msg.outreach
    o.last_sent_at = now
    o.started_at = o.started_at or now
    return msg


# --- SMS ---------------------------------------------------------------------------------------


def send_sms_step(
    session: Session, o: Outreach, now: datetime, llm: LLM, settings: Settings, report: RunReport
) -> bool:
    if o.step >= 1:
        return False
    facts = compose.facts_from_prospect(o.prospect)
    _ensure_examples(session, o, llm)  # la page publique /p/{token} les affiche
    c = compose.compose_sms(facts, public_link(o, settings))
    msg = _single_message(session, o, c.body)
    msg.check_issues = c.issues
    if msg.status in MessageStatus.SENT_LIKE:
        _mark_sent(o, msg, msg.sent_at or now, msg.provider_message_id)
        return True
    try:
        provider_id = get_sms(settings).send(o.contact or "", c.body, settings.sms_sender)
    except SendError as exc:
        msg.status = MessageStatus.FAILED
        msg.error = str(exc)[:500]
        report.errors += 1
        return False
    _mark_sent(o, msg, now, provider_id)
    report.sent += 1
    report.details.append(f"outreach {o.id} SMS → {o.contact}")
    return True


# --- Clôture des séquences sans relance -----------------------------------------------------


def expire_stale(
    session: Session, now: datetime | None = None, days: int = STALE_AFTER_DAYS
) -> int:
    """Formulaire / DM / SMS envoyés depuis > `days` jours sans réponse → `done`."""
    now = now or utcnow()
    cutoff = now - timedelta(days=days)
    n = 0
    stmt = select(Outreach).where(
        Outreach.status == OutreachStatus.ACTIVE,
        Outreach.next_action_at.is_(None),
        Outreach.last_sent_at.is_not(None),
        Outreach.last_sent_at <= cutoff,
    )
    for o in session.scalars(stmt):
        close(session, o, OutreachStatus.DONE, note="sans réponse après envoi", now=now)
        n += 1
    return n


CHANNEL_HANDLERS.update(
    {
        "formulaire": send_form_step,
        "instagram": prepare_dm_step,
        "facebook": prepare_dm_step,
        "sms": send_sms_step,
    }
)
