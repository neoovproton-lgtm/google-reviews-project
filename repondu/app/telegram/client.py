"""Envoi de messages via l'API Bot Telegram. Remplaçable par un faux en tests."""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

Buttons = list[list[tuple[str, str]]]  # lignes de (libellé, callback_data)


class Telegram(Protocol):
    def send_message(self, chat_id: str, text: str, buttons: Buttons | None = None) -> None: ...

    def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...


class HttpTelegram:
    def __init__(self, token: str, timeout: float = 10.0):
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = httpx.Client(timeout=timeout)

    def _post(self, method: str, payload: dict) -> None:
        try:
            r = self._client.post(f"{self._base}/{method}", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("telegram %s : %s", method, exc)

    def send_message(self, chat_id: str, text: str, buttons: Buttons | None = None) -> None:
        payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons
                ]
            }
        self._post("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self._post("answerCallbackQuery", payload)

    def set_webhook(self, url: str, secret: str | None) -> dict:
        payload: dict = {"url": url, "allowed_updates": ["message", "callback_query"]}
        if secret:
            payload["secret_token"] = secret
        r = self._client.post(f"{self._base}/setWebhook", json=payload)
        r.raise_for_status()
        return r.json()


class NullTelegram:
    """Sans jeton configuré : on journalise au lieu d'envoyer."""

    def send_message(self, chat_id: str, text: str, buttons: Buttons | None = None) -> None:
        log.info("telegram (désactivé) → %s : %s", chat_id, text[:120])

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        pass


_telegram: Telegram | None = None


def get_telegram(settings: Settings | None = None) -> Telegram:
    global _telegram
    if _telegram is None:
        settings = settings or get_settings()
        _telegram = (
            HttpTelegram(settings.telegram_bot_token)
            if settings.telegram_bot_token
            else NullTelegram()
        )
    return _telegram


def set_telegram(telegram: Telegram | None) -> None:
    global _telegram
    _telegram = telegram
