"""Fournisseurs d'envoi d'email (Resend, Brevo) et normalisation de leurs webhooks."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class SendError(RuntimeError):
    pass


@dataclass
class OutgoingEmail:
    from_address: str
    from_name: str
    to: str
    subject: str
    text: str
    reply_to: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class EmailProvider(Protocol):
    name: str

    def send(self, email: OutgoingEmail) -> str:
        """Envoie et retourne l'identifiant fournisseur du message. Lève SendError."""
        ...


class LogProvider:
    """Sans clé API : journalise, n'envoie rien (dev / dry-run)."""

    name = "log"

    def __init__(self):
        self.sent: list[OutgoingEmail] = []

    def send(self, email: OutgoingEmail) -> str:
        self.sent.append(email)
        log.info("email (non envoyé) %s → %s : %s", email.from_address, email.to, email.subject)
        return f"log-{uuid.uuid4().hex[:12]}"


class ResendProvider:
    name = "resend"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self._client = httpx.Client(
            base_url="https://api.resend.com",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def send(self, email: OutgoingEmail) -> str:
        payload = {
            "from": f"{email.from_name} <{email.from_address}>",
            "to": [email.to],
            "subject": email.subject,
            "text": email.text,
            "headers": email.headers or None,
        }
        if email.reply_to:
            payload["reply_to"] = email.reply_to
        try:
            r = self._client.post("/emails", json=payload)
            r.raise_for_status()
            return str(r.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SendError(f"resend : {exc}") from exc


class BrevoProvider:
    name = "brevo"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self._client = httpx.Client(
            base_url="https://api.brevo.com/v3",
            headers={"api-key": api_key, "accept": "application/json"},
            timeout=timeout,
        )

    def send(self, email: OutgoingEmail) -> str:
        payload = {
            "sender": {"name": email.from_name, "email": email.from_address},
            "to": [{"email": email.to}],
            "subject": email.subject,
            "textContent": email.text,
        }
        if email.reply_to:
            payload["replyTo"] = {"email": email.reply_to}
        if email.headers:
            payload["headers"] = email.headers
        try:
            r = self._client.post("/smtp/email", json=payload)
            r.raise_for_status()
            return str(r.json()["messageId"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise SendError(f"brevo : {exc}") from exc


_providers: dict[str, EmailProvider] = {}


def get_provider(name: str, settings: Settings | None = None) -> EmailProvider:
    """Un fournisseur par nom (`resend`, `brevo`, `log`). Sans clé API → LogProvider."""
    settings = settings or get_settings()
    if name in _providers:
        return _providers[name]
    if name == "resend" and settings.resend_api_key:
        provider: EmailProvider = ResendProvider(settings.resend_api_key)
    elif name == "brevo" and settings.brevo_api_key:
        provider = BrevoProvider(settings.brevo_api_key)
    else:
        if name not in ("log", ""):
            log.warning("fournisseur %s sans clé API : envois journalisés seulement", name)
        provider = _providers.get("log") or LogProvider()
    _providers[name] = provider
    return provider


def set_provider(name: str, provider: EmailProvider | None) -> None:
    """Injection pour les tests."""
    if provider is None:
        _providers.pop(name, None)
    else:
        _providers[name] = provider


def reset_providers() -> None:
    _providers.clear()


# --- Webhooks ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderEvent:
    type: str  # delivered | bounced | complained | opened | other
    provider_message_id: str | None
    email: str | None
    occurred_at: datetime
    raw_type: str


_RESEND_TYPES = {
    "email.delivered": "delivered",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.opened": "opened",
}
_BREVO_TYPES = {
    "delivered": "delivered",
    "hard_bounce": "bounced",
    "soft_bounce": "bounced",
    "blocked": "bounced",
    "invalid_email": "bounced",
    "spam": "complained",
    "complaint": "complained",
    "opened": "opened",
    "unique_opened": "opened",
}


def _parse_date(value) -> datetime:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            pass
    return datetime.now(UTC).replace(tzinfo=None)


def parse_resend_event(payload: dict) -> ProviderEvent | None:
    raw = str(payload.get("type") or "")
    data = payload.get("data") or {}
    to = data.get("to")
    email = to[0] if isinstance(to, list) and to else (to if isinstance(to, str) else None)
    return ProviderEvent(
        type=_RESEND_TYPES.get(raw, "other"),
        provider_message_id=data.get("email_id"),
        email=email,
        occurred_at=_parse_date(payload.get("created_at")),
        raw_type=raw,
    )


def parse_brevo_event(payload: dict) -> ProviderEvent | None:
    raw = str(payload.get("event") or "")
    return ProviderEvent(
        type=_BREVO_TYPES.get(raw, "other"),
        provider_message_id=payload.get("message-id") or payload.get("messageId"),
        email=payload.get("email"),
        occurred_at=_parse_date(payload.get("ts_event") or payload.get("date")),
        raw_type=raw,
    )
