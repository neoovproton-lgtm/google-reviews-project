"""C3 — Envoi de SMS B2B (Brevo transactionnel). Remplaçable par un faux en tests."""

from __future__ import annotations

import logging
import uuid
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.outreach.email_providers import SendError

log = logging.getLogger(__name__)


class SmsProvider(Protocol):
    name: str

    def send(self, to: str, text: str, sender: str) -> str: ...


class LogSms:
    name = "log"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, to: str, text: str, sender: str) -> str:
        self.sent.append((to, text))
        log.info("sms (non envoyé) → %s : %s", to, text)
        return f"sms-log-{uuid.uuid4().hex[:10]}"


class BrevoSms:
    name = "brevo"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self._client = httpx.Client(
            base_url="https://api.brevo.com/v3", headers={"api-key": api_key}, timeout=timeout
        )

    def send(self, to: str, text: str, sender: str) -> str:
        payload = {"sender": sender[:11], "recipient": to, "content": text, "type": "transactional"}
        try:
            r = self._client.post("/transactionalSMS/sms", json=payload)
            r.raise_for_status()
            return str(r.json().get("messageId") or r.json().get("reference") or "")
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(f"brevo sms : {exc}") from exc


_sms: SmsProvider | None = None


def get_sms(settings: Settings | None = None) -> SmsProvider:
    global _sms
    if _sms is None:
        settings = settings or get_settings()
        _sms = BrevoSms(settings.brevo_api_key) if settings.brevo_api_key else LogSms()
    return _sms


def set_sms(provider: SmsProvider | None) -> None:
    global _sms
    _sms = provider
