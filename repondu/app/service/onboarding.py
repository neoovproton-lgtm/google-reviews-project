"""D1 — Conversion d'un « oui », mail d'invitation gestionnaire (4 étapes), rappel, essai."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Establishment, OnboardingStatus, Outreach, OutreachStatus, utcnow
from app.outreach.sequence import record_outcome
from app.service.mailer import load_attachments, send_client_email

log = logging.getLogger(__name__)

STEP_NAMES = ["etape-1", "etape-2", "etape-3", "etape-4"]


def convert_outreach(
    session: Session,
    o: Outreach,
    now: datetime | None = None,
    settings: Settings | None = None,
    note: str | None = None,
) -> Establishment:
    """Crée le client à partir du prospect. Idempotent : renvoie l'établissement existant."""
    now = now or utcnow()
    settings = settings or get_settings()
    existing = session.scalar(select(Establishment).where(Establishment.outreach_id == o.id))
    if existing:
        return existing
    p = o.prospect
    if o.status != OutreachStatus.YES:
        record_outcome(
            session, o, "yes", note=note or "converti en client", now=now, source="convert"
        )
    est = Establishment(
        name=p.name,
        prospect_id=p.id,
        outreach_id=o.id,
        cuisine_type=p.category,
        signature=f"L'équipe de {p.name}",
        contact_email=p.email if o.channel != "sms" or p.email else None,
        mobile_phone=p.mobile_phone,
        baseline_response_rate=p.response_rate,
        baseline_rating=p.rating,
        onboarding_status=OnboardingStatus.CREATED,
    )
    if o.channel == "email" and o.contact:
        est.contact_email = o.contact
    session.add(est)
    session.flush()
    return est


def invitation_email(est: Establishment, settings: Settings) -> tuple[str, str]:
    subject = f"{est.name} : nous ajouter comme gestionnaire (30 secondes)"
    body = f"""Bonjour,

Merci pour votre « OK ». Pour que nous puissions publier les réponses à vos avis, il faut nous
ajouter comme gestionnaire de votre fiche Google. Quatre étapes, 30 secondes, captures jointes :

1. Ouvrez https://business.google.com et choisissez la fiche « {est.name} ».
2. Menu « Paramètres de la fiche » (ou les trois points) → « Gestionnaires ».
3. « Ajouter » → saisissez {settings.google_manager_email} → rôle « Gestionnaire ».
4. « Inviter ». C'est tout : nous acceptons l'invitation dans la journée.

Ensuite, chaque nouvel avis reçoit une réponse rédigée dans votre ton. Vous la recevez avant
publication et pouvez y opposer votre veto pendant 24 h. Rien à installer, rien à payer pendant
les 30 jours d'essai.

Une question ? Répondez à ce mail{" ou appelez-nous" if not est.telegram_chat_id else ""}.

{settings.outreach_signature}
"""
    return subject, body


def send_invitation(
    session: Session,
    est: Establishment,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> None:
    now = now or utcnow()
    settings = settings or get_settings()
    subject, body = invitation_email(est, settings)
    attachments = load_attachments(settings.onboarding_assets_dir, STEP_NAMES)
    send_client_email(session, est, "invite", subject, body, attachments, settings=settings)
    est.invited_at = now
    est.onboarding_status = OnboardingStatus.INVITED


def reminder_email(est: Establishment, settings: Settings) -> tuple[str, str]:
    subject = f"Re: {est.name} : nous ajouter comme gestionnaire"
    body = f"""Bonjour,

Petit rappel : pour démarrer l'essai, il reste à nous ajouter comme gestionnaire de la fiche
Google de {est.name} (adresse : {settings.google_manager_email}, rôle « Gestionnaire »).
Les quatre étapes sont dans le mail précédent, 30 secondes suffisent.

Si quelque chose bloque, répondez à ce mail et nous le faisons ensemble en visio.

{settings.outreach_signature}
"""
    return subject, body


def send_reminders(
    session: Session, now: datetime | None = None, settings: Settings | None = None
) -> list[Establishment]:
    """Invités sans ajout comme gestionnaire depuis `ONBOARDING_REMINDER_DAYS` : un seul rappel."""
    now = now or utcnow()
    settings = settings or get_settings()
    cutoff = now - timedelta(days=settings.onboarding_reminder_days)
    reminded = []
    stmt = select(Establishment).where(
        Establishment.onboarding_status == OnboardingStatus.INVITED,
        Establishment.reminded_at.is_(None),
        Establishment.invited_at <= cutoff,
    )
    for est in session.scalars(stmt):
        subject, body = reminder_email(est, settings)
        send_client_email(session, est, "reminder", subject, body, settings=settings)
        est.reminded_at = now
        reminded.append(est)
    return reminded


def manager_added(
    session: Session,
    est: Establishment,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> Establishment:
    now = now or utcnow()
    settings = settings or get_settings()
    est.manager_added_at = est.manager_added_at or now
    est.trial_started_at = est.trial_started_at or now
    est.trial_ends_at = est.trial_ends_at or now + timedelta(days=settings.trial_days)
    est.onboarding_status = OnboardingStatus.MANAGER_ADDED
    est.active = 1
    return est
