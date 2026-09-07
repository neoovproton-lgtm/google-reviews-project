"""D4 — Rapport hebdomadaire client : avis reçus, réponses publiées, note, taux avant/après."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Establishment, OnboardingStatus, Reply, ReplyStatus, Review
from app.service.mailer import send_client_email

log = logging.getLogger(__name__)


def compute_report(session: Session, est: Establishment, now: datetime) -> dict:
    week_ago = now - timedelta(days=7)
    reviews = list(session.scalars(select(Review).where(Review.prospect_id == est.prospect_id)))
    since_trial = est.trial_started_at or est.created_at
    week = [r for r in reviews if r.date and r.date >= week_ago]
    trial = [r for r in reviews if r.date and r.date >= since_trial]
    published = list(
        session.scalars(
            select(Reply).where(
                Reply.establishment_id == est.id, Reply.status == ReplyStatus.PUBLISHED
            )
        )
    )
    published_week = [r for r in published if r.published_at and r.published_at >= week_ago]

    def avg(rs):
        rated = [r.rating for r in rs if r.rating]
        return round(sum(rated) / len(rated), 2) if rated else None

    def rate(rs):
        return round(sum(1 for r in rs if r.has_owner_response) / len(rs), 3) if rs else None

    return {
        "period_start": week_ago,
        "period_end": now,
        "reviews_week": len(week),
        "avg_rating_week": avg(week),
        "published_week": len(published_week),
        "published_total": len(published),
        "reviews_since_trial": len(trial),
        "response_rate_before": est.baseline_response_rate,
        "response_rate_after": rate(trial),
        "rating_before": est.baseline_rating,
        "rating_after": avg(trial) if trial else None,
        "pending": len([r for r in est.replies if r.status == ReplyStatus.PENDING]),
    }


def _pct(v):
    return "—" if v is None else f"{v * 100:.0f} %"


def _note(v):
    return "—" if v is None else f"{v:.1f}/5"


def report_email(est: Establishment, r: dict, settings: Settings) -> tuple[str, str]:
    start = r["period_start"].strftime("%d/%m")
    end = r["period_end"].strftime("%d/%m")
    subject = f"{est.name} — vos avis Google du {start} au {end}"
    body = f"""Bonjour,

Votre semaine sur Google ({start} → {end}) :

- Avis reçus : {r["reviews_week"]} (note moyenne de la semaine : {_note(r["avg_rating_week"])})
- Réponses publiées : {r["published_week"]} cette semaine, {r["published_total"]} depuis le début
- Taux de réponse : {_pct(r["response_rate_before"])} avant Répondu → \
{_pct(r["response_rate_after"])} depuis
- Note moyenne : {_note(r["rating_before"])} avant → {_note(r["rating_after"])} sur les avis \
reçus depuis
"""
    if r["pending"]:
        body += f"\n{r['pending']} réponse(s) attendent encore votre validation.\n"
    body += f"""
Une remarque, un ton à ajuster ? Répondez à ce mail.

{settings.outreach_signature}
"""
    return subject, body


def send_weekly_reports(
    session: Session,
    now: datetime | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> list[Establishment]:
    """Le lundi (heure UTC), un rapport par client actif, une seule fois par semaine."""
    from datetime import UTC

    now = now or datetime.now(UTC).replace(tzinfo=None)
    settings = settings or get_settings()
    if not force and now.weekday() != 0:
        return []
    sent = []
    stmt = select(Establishment).where(
        Establishment.active == 1, Establishment.onboarding_status == OnboardingStatus.MANAGER_ADDED
    )
    for est in session.scalars(stmt):
        if not force and est.last_report_at and est.last_report_at > now - timedelta(days=6):
            continue
        r = compute_report(session, est, now)
        subject, body = report_email(est, r, settings)
        send_client_email(session, est, "report", subject, body, settings=settings)
        est.last_report_at = now
        sent.append(est)
    return sent
