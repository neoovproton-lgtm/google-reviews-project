"""Envoi de messages aux clients (mail de service, SMS, Telegram) avec journal `client_messages`."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import ClientMessage, Establishment, utcnow
from app.outreach.email_providers import OutgoingEmail, SendError, get_provider
from app.outreach.sms import get_sms
from app.telegram.client import Telegram, get_telegram

log = logging.getLogger(__name__)


def send_client_email(
    session: Session,
    est: Establishment,
    kind: str,
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
    reply_id: int | None = None,
    settings: Settings | None = None,
    to: str | None = None,
) -> ClientMessage:
    settings = settings or get_settings()
    to = to or est.contact_email or ""
    record = ClientMessage(
        establishment_id=est.id,
        reply_id=reply_id,
        kind=kind,
        channel="email",
        to=to,
        subject=subject,
        body=body,
    )
    session.add(record)
    if not to:
        record.error = "pas d'adresse email"
        session.flush()
        return record
    email = OutgoingEmail(
        from_address=settings.service_from_address,
        from_name=settings.service_from_name,
        to=to,
        subject=subject,
        text=body,
        reply_to=settings.service_reply_address or settings.service_from_address,
        attachments=attachments or [],
    )
    try:
        record.provider_message_id = get_provider(settings.service_email_provider, settings).send(
            email
        )
    except SendError as exc:
        record.error = str(exc)[:1000]
        log.warning("mail client %s (%s) : %s", est.name, kind, exc)
    session.flush()
    return record


def send_client_sms(
    session: Session,
    est: Establishment,
    kind: str,
    text: str,
    reply_id: int | None = None,
    settings: Settings | None = None,
) -> ClientMessage | None:
    settings = settings or get_settings()
    if not est.mobile_phone:
        return None
    record = ClientMessage(
        establishment_id=est.id,
        reply_id=reply_id,
        kind=kind,
        channel="sms",
        to=est.mobile_phone,
        body=text,
    )
    session.add(record)
    try:
        record.provider_message_id = get_sms(settings).send(
            est.mobile_phone, text, settings.sms_sender
        )
    except SendError as exc:
        record.error = str(exc)[:500]
    session.flush()
    return record


def send_client_telegram(
    session: Session,
    est: Establishment,
    kind: str,
    text: str,
    buttons=None,
    reply_id: int | None = None,
    telegram: Telegram | None = None,
) -> ClientMessage | None:
    if not est.telegram_chat_id:
        return None
    (telegram or get_telegram()).send_message(est.telegram_chat_id, text, buttons=buttons)
    record = ClientMessage(
        establishment_id=est.id,
        reply_id=reply_id,
        kind=kind,
        channel="telegram",
        to=est.telegram_chat_id,
        body=text,
    )
    session.add(record)
    session.flush()
    return record


def load_attachments(directory: Path, names: list[str]) -> list[tuple[str, bytes, str]]:
    out = []
    for name in names:
        for ext, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg")):
            path = directory / f"{name}{ext}"
            if path.exists():
                out.append((path.name, path.read_bytes(), mime))
                break
    return out


def now_str() -> str:
    return utcnow().strftime("%d/%m/%Y")
