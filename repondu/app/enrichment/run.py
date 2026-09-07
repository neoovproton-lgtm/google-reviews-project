"""A4 — Boucle d'enrichissement des prospects qualifiés."""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import session_scope
from app.enrichment.channel import WRITTEN_CHANNELS, choose_channel
from app.enrichment.extract import Extraction
from app.enrichment.website import enrich_from_website, make_client
from app.models import Prospect, ProspectStatus, utcnow
from app.scraping.parsers import is_mobile_phone

log = logging.getLogger(__name__)


def apply_extraction(p: Prospect, ex: Extraction) -> None:
    if ex.emails and not p.email:
        p.email = ex.emails[0]
    if ex.contact_form_url and not p.contact_form_url:
        p.contact_form_url = ex.contact_form_url
    if ex.instagram and not p.instagram:
        p.instagram = ex.instagram
    if ex.facebook and not p.facebook:
        p.facebook = ex.facebook
    if not p.mobile_phone:
        p.mobile_phone = ex.mobile_phone or (p.phone if is_mobile_phone(p.phone) else None)
    p.canal_prioritaire = choose_channel(
        p.email, p.contact_form_url, p.instagram, p.facebook, p.mobile_phone, p.phone
    )


def enrich_all(
    limit: int | None = None, retry_errors: bool = False, settings: Settings | None = None
) -> dict:
    settings = settings or get_settings()
    with session_scope() as session:
        stmt = select(Prospect.id).where(Prospect.status == ProspectStatus.QUALIFIED)
        if retry_errors:
            stmt = select(Prospect.id).where(
                Prospect.status == ProspectStatus.ENRICHED, Prospect.enrich_error.is_not(None)
            )
        stmt = stmt.order_by(Prospect.score.desc().nullslast(), Prospect.id)
        if limit:
            stmt = stmt.limit(limit)
        ids = list(session.scalars(stmt))

    done = errors = written = 0
    with make_client(settings) as client:
        for pid in ids:
            with session_scope() as session:
                p = session.get(Prospect, pid)
                assert p is not None
                ex = Extraction()
                p.enrich_error = None
                if p.website:
                    try:
                        ex = enrich_from_website(p.website, client=client, settings=settings)
                    except Exception as exc:  # noqa: BLE001
                        log.exception("prospect %d : erreur enrichissement", pid)
                        p.enrich_error = f"{type(exc).__name__}: {exc}"[:2000]
                        errors += 1
                apply_extraction(p, ex)
                p.status = ProspectStatus.ENRICHED
                p.enriched_at = utcnow()
                done += 1
                if p.canal_prioritaire in WRITTEN_CHANNELS:
                    written += 1
    return {
        "selected": len(ids),
        "done": done,
        "errors": errors,
        "with_written_channel": written,
        "written_channel_rate": round(written / done, 3) if done else None,
    }
