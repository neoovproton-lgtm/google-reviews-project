"""D2/D3 — Relève des deux boîtes du service : compte gestionnaire (Google) et boîte de service."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm import LLM
from app.models import EmailEvent, utcnow
from app.outreach.inbox import Fetcher, fetch_unseen_imap, parse_rfc822
from app.service.loop import process_client_reply, process_establishment, refresh_reviews
from app.service.notifications import match_establishment, parse_notification
from app.telegram.client import Telegram

log = logging.getLogger(__name__)


def _cfg(host, port, user, password) -> dict | None:
    if not (host and user and password):
        return None
    return {"host": host, "port": port, "user": user, "password": password}


def poll_manager_inbox(
    session: Session,
    settings: Settings | None = None,
    telegram: Telegram | None = None,
    fetcher: Fetcher | None = None,
    llm: LLM | None = None,
    scraper=None,
    now: datetime | None = None,
) -> dict:
    """Notifications Google → rafraîchissement immédiat des avis de l'établissement concerné."""
    settings = settings or get_settings()
    now = now or utcnow()
    cfg = _cfg(
        settings.manager_imap_host,
        settings.manager_imap_port,
        settings.manager_imap_user,
        settings.manager_imap_password,
    )
    out = {
        "configured": cfg is not None,
        "messages": 0,
        "notifications": 0,
        "matched": 0,
        "new_reviews": 0,
        "drafts": 0,
    }
    if cfg is None:
        return out
    for uid, raw in (fetcher or fetch_unseen_imap)(cfg):
        out["messages"] += 1
        mail = parse_rfc822(raw, uid)
        notif = parse_notification(mail)
        if notif is None:
            continue
        out["notifications"] += 1
        est = match_establishment(session, notif.establishment_name)
        session.add(
            EmailEvent(
                type="google_review_notification",
                source="manager_imap",
                external_id=mail.message_id,
                payload={
                    "establishment": notif.establishment_name,
                    "author": notif.author,
                    "rating": notif.rating,
                    "matched": est.id if est else None,
                },
                occurred_at=now,
            )
        )
        if est is None:
            log.warning(
                "notification Google sans établissement connu : %s", notif.establishment_name
            )
            continue
        out["matched"] += 1
        new = refresh_reviews(session, est, now, settings, scraper=scraper)
        out["new_reviews"] += len(new)
        est = session.get(type(est), est.id)
        out["drafts"] += len(process_establishment(session, est, llm, telegram, settings, now))
    session.flush()
    return out


def poll_service_inbox(
    session: Session,
    settings: Settings | None = None,
    telegram: Telegram | None = None,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
) -> dict:
    """Réponses des clients aux mails de brouillon : OK → approuvé, VETO → refusé."""
    settings = settings or get_settings()
    now = now or utcnow()
    cfg = _cfg(
        settings.service_imap_host,
        settings.service_imap_port,
        settings.service_imap_user,
        settings.service_imap_password,
    )
    out = {
        "configured": cfg is not None,
        "messages": 0,
        "approved": 0,
        "rejected": 0,
        "ignored": 0,
        "unknown": 0,
        "duplicate": 0,
    }
    if cfg is None:
        return out
    for uid, raw in (fetcher or fetch_unseen_imap)(cfg):
        out["messages"] += 1
        mail = parse_rfc822(raw, uid)
        kind = process_client_reply(session, mail, now)
        out[kind] = out.get(kind, 0) + 1
        if kind in ("approved", "rejected") and telegram and settings.telegram_chat_id:
            telegram.send_message(
                settings.telegram_chat_id,
                f"Client {mail.from_address} : réponse "
                f"{'approuvée ✅' if kind == 'approved' else 'veto ❌'} par mail ({mail.subject}).",
            )
    session.flush()
    return out
