"""Doubles de test : LLM et Telegram sans réseau."""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel


class FakeLLM:
    """Renvoie des réponses scriptées, dans l'ordre. Enregistre chaque appel."""

    model = "fake-sonnet"

    def __init__(self, responses: list[dict[str, Any] | BaseModel] | None = None):
        self.responses: deque = deque(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.default: dict[str, Any] | None = None

    def complete_json(
        self, system: str, user: str, schema: type[BaseModel], max_tokens: int = 1024
    ):
        self.calls.append({"system": system, "user": user, "schema": schema.__name__})
        if self.responses:
            raw = self.responses.popleft()
        elif self.default is not None:
            raw = self.default
        else:
            raise AssertionError(f"FakeLLM : aucune réponse prévue pour {schema.__name__}")
        return raw if isinstance(raw, BaseModel) else schema.model_validate(raw)
