"""C2 — Machine à états de prospection : enrôlement, étapes J0 / J+3 / J+8, arrêts, opt-out.

Idempotence : un `OutreachMessage` par (séquence, étape) ; un message `sent` n'est jamais renvoyé.
Quotas : boîte choisie par `mailboxes.pick_mailbox`, jamais au-delà du quota effectif du jour.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.enrichment.channel import WRITTEN_CHANNELS
from app.llm import LLM, get_llm
from app.models import (
    EmailEvent,
    MessageStatus,
    OptOut,
    Outreach,
    OutreachMessage,
    OutreachStatus,
    Prospect,
    ProspectStatus,
    utcnow,
)
from app.outreach import compose
from app.outreach.email_providers import OutgoingEmail, SendError, get_provider
from app.outreach.mailboxes import pick_mailbox

log = logging.getLogger(__name__)

STEPS = 3
CHANNELS = tuple(WRITTEN_CHANNELS)  # email, formulaire, instagram, facebook, sms


# --- Fenêtre d'envoi ------------------------------------------------------------------------


def is_send_window(now: datetime, settings: Settings | None = None) -> bool:
    """Jours ouvrés, entre les heures `OUTREACH_SEND_HOURS` (heure de Paris). `now` en UTC naïf."""
    settings = settings or get_settings()
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo(settings.outreach_timezone))
    if local.weekday() >= 5:
        return False
    start, _, end = settings.outreach_send_hours.partition("-")
    return int(start) <= local.hour < int(end or 24)


# --- Opt-out ---------------------------------------------------------------------------------


def normalize_contact(contact: str) -> str:
    return contact.strip().lower()


def is_opted_out(session: Session, contact: str | None) -> bool:
    if not contact:
        return False
    return (
        session.scalar(select(OptOut.id).where(OptOut.contact == normalize_contact(contact)))
        is not None
    )


def opt_out(session: Session, contact: str, source: str, now: datetime | None = None) -> None:
    """Inscrit au registre et clôt toute séquence active sur ce contact."""
    now = now or utcnow()
    contact = normalize_contact(contact)
    if session.scalar(select(OptOut.id).where(OptOut.contact == contact)) is None:
        session.add(OptOut(contact=contact, source=source))
    for o in session.scalars(select(Outreach).where(Outreach.status == OutreachStatus.ACTIVE)):
        if o.contact and normalize_contact(o.contact) == contact:
            close(session, o, OutreachStatus.OPTED_OUT, note=f"opt-out ({source})", now=now)


def close(
    session: Session | None,
    o: Outreach,
    status: str,
    note: str | None = None,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    o.status = status
    o.next_action_at = None
    o.closed_at = now
    if status in (OutreachStatus.REPLIED, OutreachStatus.YES, OutreachStatus.OBJECTION):
        o.replied_at = o.replied_at or now
    if note:
        o.outcome_note = note


# --- Enrôlement ------------------------------------------------------------------------------


def contact_for(p: Prospect, channel: str) -> str | None:
    return {
        "email": p.email,
        "formulaire": p.contact_form_url,
        "instagram": p.instagram,
        "facebook": p.facebook,
        "sms": p.mobile_phone,
    }.get(channel)


def enroll(session: Session, now: datetime | None = None, limit: int | None = None) -> int:
    """Une séquence par prospect `enriched` avec un canal, sans séquence existante, non opt-out."""
    now = now or utcnow()
    existing = select(Outreach.prospect_id)
    stmt = (
        select(Prospect)
        .where(
            Prospect.status == ProspectStatus.ENRICHED,
            Prospect.canal_prioritaire.in_(CHANNELS),
            Prospect.id.not_in(existing),
        )
        .order_by(Prospect.score.desc().nullslast(), Prospect.id)
    )
    created = 0
    for p in session.scalars(stmt):
        contact = contact_for(p, p.canal_prioritaire or "")
        if not contact or is_opted_out(session, contact):
            continue
        session.add(
            Outreach(
                prospect_id=p.id,
                channel=p.canal_prioritaire,
                contact=contact,
                next_action_at=now,
                token=secrets.token_urlsafe(9),
            )
        )
        created += 1
        if limit and created >= limit:
            break
    session.flush()
    return created


# --- Envoi d'une étape -------------------------------------------------------------------


@dataclass
class RunReport:
    enrolled: int = 0
    sent: int = 0
    skipped_window: bool = False
    skipped_quota: int = 0
    errors: int = 0
    prepared_manual: int = 0
    details: list[str] = field(default_factory=list)


def due_outreach(session: Session, now: datetime) -> list[Outreach]:
    stmt = (
        select(Outreach)
        .join(Prospect)
        .where(Outreach.status == OutreachStatus.ACTIVE, Outreach.next_action_at <= now)
        .order_by(Prospect.score.desc().nullslast(), Outreach.id)
    )
    return list(session.scalars(stmt))


def _ensure_examples(session: Session, o: Outreach, llm: LLM) -> list[dict]:
    if o.examples is None:
        o.examples = compose.build_examples(session, o.prospect, llm)
    return o.examples


def prepare_message(
    session: Session, o: Outreach, step: int, llm: LLM, settings: Settings
) -> OutreachMessage:
    """Le message de cette étape, composé une seule fois (idempotent)."""
    msg = session.scalar(
        select(OutreachMessage).where(
            OutreachMessage.outreach_id == o.id, OutreachMessage.step == step
        )
    )
    if msg is not None:
        return msg
    facts = compose.facts_from_prospect(o.prospect)
    examples = _ensure_examples(session, o, llm)
    if step == 1:
        c = compose.compose_first_email(facts, examples, llm, settings)
    elif step == 2:
        used = tuple(e["review_id"] for e in examples)
        fresh = compose.build_examples(session, o.prospect, llm, n=1, exclude_ids=used)
        if fresh:
            o.examples = examples + fresh
        c = compose.compose_followup(facts, fresh[0] if fresh else None, llm, settings)
        c.subject = "Re: " + (
            session.scalar(
                select(OutreachMessage.subject).where(
                    OutreachMessage.outreach_id == o.id, OutreachMessage.step == 1
                )
            )
            or "vos avis Google"
        )
    else:
        c = compose.compose_last(facts, llm, settings)
        c.subject = "Re: " + (
            session.scalar(
                select(OutreachMessage.subject).where(
                    OutreachMessage.outreach_id == o.id, OutreachMessage.step == 1
                )
            )
            or "vos avis Google"
        )
    msg = OutreachMessage(
        outreach_id=o.id,
        step=step,
        channel=o.channel,
        subject=c.subject,
        body=c.body,
        check_issues=c.issues,
    )
    session.add(msg)
    session.flush()
    return msg


def _advance(o: Outreach, step: int, now: datetime, settings: Settings) -> None:
    o.step = step
    o.last_sent_at = now
    o.started_at = o.started_at or now
    if step == 1:
        o.next_action_at = now + timedelta(days=settings.outreach_followup_days)
    elif step == 2:
        o.next_action_at = now + timedelta(days=settings.outreach_last_days)
    else:
        close(None, o, OutreachStatus.DONE, note="séquence terminée sans réponse", now=now)


def send_email_step(
    session: Session, o: Outreach, now: datetime, llm: LLM, settings: Settings, report: RunReport
) -> bool:
    """Retourne True si un envoi a eu lieu (ou a été constaté déjà fait)."""
    step = o.step + 1
    msg = prepare_message(session, o, step, llm, settings)
    if msg.status in MessageStatus.SENT_LIKE:
        _advance(o, step, msg.sent_at or now, settings)  # reprise après coupure
        return True
    mailbox = pick_mailbox(session, now, preferred_id=o.mailbox_id)
    if mailbox is None:
        report.skipped_quota += 1
        return False
    provider = get_provider(mailbox.provider, settings)
    email = OutgoingEmail(
        from_address=mailbox.address,
        from_name=mailbox.display_name or settings.outreach_sender_name,
        to=o.contact or "",
        subject=msg.subject or "",
        text=msg.body,
        reply_to=mailbox.address,
        headers={"List-Unsubscribe": f"<mailto:{mailbox.address}?subject=STOP>"},
    )
    try:
        provider_id = provider.send(email)
    except SendError as exc:
        msg.status = MessageStatus.FAILED
        msg.error = str(exc)[:1000]
        report.errors += 1
        log.warning("envoi échoué outreach %d étape %d : %s", o.id, step, exc)
        return False
    msg.status = MessageStatus.SENT
    msg.sent_at = now
    msg.mailbox_id = mailbox.id
    msg.provider_message_id = provider_id
    mailbox.sent_total += 1
    o.mailbox_id = mailbox.id
    _advance(o, step, now, settings)
    report.sent += 1
    report.details.append(f"outreach {o.id} étape {step} → {o.contact} via {mailbox.address}")
    return True


CHANNEL_HANDLERS: dict[str, object] = {"email": send_email_step}


def run_outreach(
    now: datetime | None = None,
    limit: int | None = None,
    llm: LLM | None = None,
    settings: Settings | None = None,
    session: Session | None = None,
    force_window: bool = False,
) -> RunReport:
    """Point d'entrée C2 : enrôle puis envoie les étapes dues dans la fenêtre d'envoi."""
    from app.db import session_scope

    settings = settings or get_settings()
    now = now or utcnow()
    report = RunReport()
    if session is not None:
        _run(session, now, limit, llm, settings, report, force_window)
    else:
        with session_scope() as s:
            _run(s, now, limit, llm, settings, report, force_window)
    return report


def _run(session, now, limit, llm, settings, report, force_window) -> None:
    report.enrolled = enroll(session, now)
    if not force_window and not is_send_window(now, settings):
        report.skipped_window = True
        return
    llm = llm or get_llm()
    for o in due_outreach(session, now):
        if limit is not None and report.sent + report.prepared_manual >= limit:
            break
        if is_opted_out(session, o.contact):
            close(session, o, OutreachStatus.OPTED_OUT, note="opt-out (registre)", now=now)
            continue
        handler = CHANNEL_HANDLERS.get(o.channel)
        if handler is None:
            report.details.append(f"outreach {o.id} : canal {o.channel} non géré")
            continue
        try:
            handler(session, o, now, llm, settings, report)
        except Exception as exc:  # noqa: BLE001
            log.exception("outreach %d en erreur", o.id)
            report.errors += 1
            report.details.append(f"outreach {o.id} : {type(exc).__name__}: {exc}")
        session.flush()


# --- Événements entrants -------------------------------------------------------------------


def record_outcome(
    session: Session,
    o: Outreach,
    outcome: str,
    note: str | None = None,
    now: datetime | None = None,
    source: str = "manual",
) -> None:
    """`replied` | `yes` | `objection` | `opted_out` | `no` | `stopped`."""
    now = now or utcnow()
    mapping = {
        "replied": OutreachStatus.REPLIED,
        "yes": OutreachStatus.YES,
        "objection": OutreachStatus.OBJECTION,
        "opted_out": OutreachStatus.OPTED_OUT,
        "no": OutreachStatus.DONE,
        "stopped": OutreachStatus.STOPPED,
    }
    if outcome not in mapping:
        raise ValueError(f"outcome inconnu : {outcome}")
    if outcome == "opted_out" and o.contact:
        opt_out(session, o.contact, source=source, now=now)
    if o.status == OutreachStatus.ACTIVE or outcome in ("yes", "objection", "replied"):
        close(session, o, mapping[outcome], note=note, now=now)
    session.add(
        EmailEvent(
            outreach_id=o.id, type=outcome, source=source, payload={"note": note}, occurred_at=now
        )
    )


def mark_bounced(session: Session, msg: OutreachMessage, now: datetime | None = None) -> None:
    now = now or utcnow()
    msg.status = MessageStatus.BOUNCED
    msg.bounced_at = now
    o = msg.outreach
    if o.status == OutreachStatus.ACTIVE:
        close(session, o, OutreachStatus.BOUNCED, note="adresse en erreur (bounce)", now=now)
