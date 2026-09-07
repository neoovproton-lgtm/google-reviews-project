"""C2/C5 — Boîtes d'envoi : chargement, quotas avec montée en charge, rotation, santé."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Mailbox, MessageStatus, OutreachMessage, utcnow

log = logging.getLogger(__name__)

# Montée en charge PRD : 20/jour/boîte en semaine 1 → 30/jour à S+3.
WARMUP_RAMP = (20, 23, 27, 30)


def load_mailboxes_file(path: Path) -> list[dict]:
    """`data/mailboxes.json` : [{address, display_name, provider, daily_quota, warmup_started_at,
    imap: {host, port, user, password}}]. Les identifiants IMAP ne sont jamais copiés en base."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("mailboxes", data) if isinstance(data, dict) else data


def sync_mailboxes(session: Session, entries: list[dict]) -> int:
    """Upsert par adresse. Retourne le nombre de boîtes créées."""
    created = 0
    for e in entries:
        address = e["address"].strip().lower()
        mb = session.scalar(select(Mailbox).where(Mailbox.address == address))
        if mb is None:
            mb = Mailbox(address=address, warmup_started_at=utcnow())
            session.add(mb)
            created += 1
        mb.display_name = e.get("display_name") or mb.display_name
        mb.provider = e.get("provider") or mb.provider or "log"
        if e.get("daily_quota"):
            mb.daily_quota = int(e["daily_quota"])
        if e.get("warmup_started_at"):
            mb.warmup_started_at = datetime.fromisoformat(e["warmup_started_at"])
    session.flush()
    return created


def imap_config_for(address: str, entries: list[dict]) -> dict | None:
    for e in entries:
        if e.get("address", "").strip().lower() == address.lower():
            return e.get("imap")
    return None


def effective_quota(mb: Mailbox, now: datetime | None = None) -> int:
    now = now or utcnow()
    start = mb.warmup_started_at or mb.created_at or now
    week = max(0, (now - start).days // 7)
    ramp = WARMUP_RAMP[min(week, len(WARMUP_RAMP) - 1)]
    return min(ramp, mb.daily_quota)


def sent_today(session: Session, mailbox_id: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(
        session.scalar(
            select(func.count())
            .select_from(OutreachMessage)
            .where(
                OutreachMessage.mailbox_id == mailbox_id,
                OutreachMessage.sent_at >= day_start,
                OutreachMessage.sent_at < day_start + timedelta(days=1),
                OutreachMessage.status.in_(MessageStatus.SENT_LIKE),
            )
        )
        or 0
    )


def remaining_today(session: Session, mb: Mailbox, now: datetime | None = None) -> int:
    if not mb.active:
        return 0
    return max(0, effective_quota(mb, now) - sent_today(session, mb.id, now))


def pick_mailbox(
    session: Session, now: datetime | None = None, preferred_id: int | None = None
) -> Mailbox | None:
    """Boîte préférée si elle a du quota, sinon la boîte active la moins sollicitée aujourd'hui."""
    now = now or utcnow()
    boxes = list(session.scalars(select(Mailbox).where(Mailbox.active == 1).order_by(Mailbox.id)))
    if preferred_id is not None:
        pref = next((b for b in boxes if b.id == preferred_id), None)
        if pref and remaining_today(session, pref, now) > 0:
            return pref
    candidates = [(sent_today(session, b.id, now), b) for b in boxes]
    candidates = [(n, b) for n, b in candidates if effective_quota(b, now) - n > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1].id))
    return candidates[0][1]


def bounce_rate(mb: Mailbox) -> float | None:
    if mb.sent_total <= 0:
        return None
    return round(mb.bounced_total / mb.sent_total, 4)


def check_health(mb: Mailbox, settings: Settings | None = None) -> str | None:
    """Coupe la boîte si le taux de bounce dépasse le seuil (après un minimum d'envois) ou
    dès la première plainte spam. Retourne la raison si la boîte vient d'être coupée."""
    settings = settings or get_settings()
    if not mb.active:
        return None
    reason = None
    if mb.complained_total > 0:
        reason = f"plainte spam ({mb.complained_total})"
    elif mb.sent_total >= settings.outreach_min_sent_for_rate:
        rate = bounce_rate(mb) or 0.0
        if rate > settings.outreach_max_bounce_rate:
            reason = f"bounce {rate * 100:.1f} % > {settings.outreach_max_bounce_rate * 100:.0f} %"
    if reason:
        mb.active = 0
        mb.paused_reason = reason
        log.warning("boîte %s coupée : %s", mb.address, reason)
    return reason


def mailbox_health(session: Session, mb: Mailbox, now: datetime | None = None) -> dict:
    now = now or utcnow()
    return {
        "id": mb.id,
        "address": mb.address,
        "provider": mb.provider,
        "active": bool(mb.active),
        "paused_reason": mb.paused_reason,
        "quota_today": effective_quota(mb, now),
        "sent_today": sent_today(session, mb.id, now),
        "sent_total": mb.sent_total,
        "delivered_total": mb.delivered_total,
        "opened_total": mb.opened_total,
        "bounced_total": mb.bounced_total,
        "complained_total": mb.complained_total,
        "bounce_rate": bounce_rate(mb),
        "warmup_week": max(0, (now - (mb.warmup_started_at or mb.created_at or now)).days // 7) + 1,
    }
