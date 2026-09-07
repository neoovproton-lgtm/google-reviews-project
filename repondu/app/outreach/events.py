"""C5 — Application des événements fournisseur (webhooks) : messages, boîtes, séquences."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import EmailEvent, MessageStatus, OutreachMessage
from app.outreach.email_providers import ProviderEvent
from app.outreach.mailboxes import check_health
from app.outreach.sequence import mark_bounced, opt_out

log = logging.getLogger(__name__)


def apply_provider_event(
    session: Session, event: ProviderEvent, source: str, settings: Settings | None = None
) -> dict:
    """Retourne un résumé {matched, type, mailbox_paused}. Idempotent par (source, id, type)."""
    settings = settings or get_settings()
    msg = None
    if event.provider_message_id:
        msg = session.scalar(
            select(OutreachMessage).where(
                OutreachMessage.provider_message_id == event.provider_message_id
            )
        )
    if msg is None:
        log.info("événement %s/%s sans message connu (%s)", source, event.raw_type, event.email)
        return {"matched": False, "type": event.type, "mailbox_paused": None}

    dup = session.scalar(
        select(EmailEvent.id).where(
            EmailEvent.message_id == msg.id,
            EmailEvent.type == event.type,
            EmailEvent.source == source,
        )
    )
    if dup is not None and event.type in ("delivered", "bounced", "complained"):
        return {"matched": True, "type": event.type, "mailbox_paused": None, "duplicate": True}

    session.add(
        EmailEvent(
            mailbox_id=msg.mailbox_id,
            message_id=msg.id,
            outreach_id=msg.outreach_id,
            type=event.type,
            source=source,
            external_id=event.provider_message_id,
            payload={"raw_type": event.raw_type, "email": event.email},
            occurred_at=event.occurred_at,
        )
    )
    mb = msg.mailbox
    paused = None
    if event.type == "delivered":
        if msg.status == MessageStatus.SENT:
            msg.status = MessageStatus.DELIVERED
        msg.delivered_at = msg.delivered_at or event.occurred_at
        if mb:
            mb.delivered_total += 1
    elif event.type == "opened":
        if msg.status in (MessageStatus.SENT, MessageStatus.DELIVERED):
            msg.status = MessageStatus.OPENED
        if msg.opened_at is None:
            msg.opened_at = event.occurred_at
            if mb:
                mb.opened_total += 1
    elif event.type == "bounced":
        mark_bounced(session, msg, now=event.occurred_at)
        if mb:
            mb.bounced_total += 1
    elif event.type == "complained":
        if mb:
            mb.complained_total += 1
        contact = msg.outreach.contact or event.email
        if contact:
            opt_out(session, contact, source=f"plainte {source}", now=event.occurred_at)
    if mb:
        paused = check_health(mb, settings)
    session.flush()
    return {"matched": True, "type": event.type, "mailbox_paused": paused}


# --- Vérification des webhooks ---------------------------------------------------------------


def verify_svix_signature(
    secret: str, headers: dict, body: bytes, now: datetime | None = None
) -> bool:
    """Signature Svix (Resend) : HMAC-SHA256 de `id.timestamp.body` avec le secret `whsec_…`."""
    msg_id = headers.get("svix-id")
    ts = headers.get("svix-timestamp")
    sig_header = headers.get("svix-signature")
    if not (msg_id and ts and sig_header):
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    now = now or datetime.now(UTC)
    if abs(now.timestamp() - ts_int) > 300:
        return False
    raw_secret = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(raw_secret)
    except ValueError:
        return False
    signed = f"{msg_id}.{ts}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in sig_header.split():
        _, _, value = part.partition(",")
        if hmac.compare_digest(value, expected):
            return True
    return False
