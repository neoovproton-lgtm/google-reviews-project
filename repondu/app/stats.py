"""Statistiques pour GET /stats (comptes par statut, jobs, couverture contact)."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Prospect, ProspectStatus, ScrapeJob


def compute_stats(session: Session) -> dict:
    by_status = dict(
        session.execute(select(Prospect.status, func.count()).group_by(Prospect.status)).all()
    )
    prospects = {s: int(by_status.get(s, 0)) for s in ProspectStatus.ALL}
    prospects["total"] = sum(prospects.values())

    by_city = session.execute(
        select(Prospect.city, func.count()).group_by(Prospect.city).order_by(Prospect.city)
    ).all()

    jobs = dict(
        session.execute(select(ScrapeJob.status, func.count()).group_by(ScrapeJob.status)).all()
    )
    jobs = {k: int(v) for k, v in jobs.items()}
    jobs["total"] = sum(jobs.values())

    qualified_plus = [ProspectStatus.QUALIFIED, ProspectStatus.ENRICHED]
    n_qualified = (
        session.scalar(
            select(func.count()).select_from(Prospect).where(Prospect.status.in_(qualified_plus))
        )
        or 0
    )
    n_written = (
        session.scalar(
            select(func.count())
            .select_from(Prospect)
            .where(
                Prospect.status.in_(qualified_plus),
                Prospect.canal_prioritaire.in_(
                    ["email", "formulaire", "instagram", "facebook", "sms"]
                ),
            )
        )
        or 0
    )
    by_channel = dict(
        session.execute(
            select(Prospect.canal_prioritaire, func.count())
            .where(Prospect.status == ProspectStatus.ENRICHED)
            .group_by(Prospect.canal_prioritaire)
        ).all()
    )
    from app.models import Mailbox
    from app.outreach.mailboxes import mailbox_health

    mailboxes = [mailbox_health(session, mb) for mb in session.scalars(select(Mailbox))]

    from app.outreach.stats import funnel
    from app.service.loop import service_stats
    from app.service.survey import bilan

    return {
        "bilan": bilan(session),
        "service": service_stats(session),
        "outreach": funnel(session),
        "mailboxes": mailboxes,
        "prospects": prospects,
        "prospects_by_city": {c or "?": int(n) for c, n in by_city},
        "scrape_jobs": jobs,
        "qualified": {
            "total": int(n_qualified),
            "with_written_channel": int(n_written),
            "written_channel_rate": round(n_written / n_qualified, 3) if n_qualified else None,
            "by_channel": {k or "aucun": int(v) for k, v in by_channel.items()},
        },
    }
