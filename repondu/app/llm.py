"""Accès à Claude Sonnet (clé API). Interface minimale, remplaçable par un faux en tests."""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.config import Settings, get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Erreur définitive côté LLM (clé absente, refus, réponse invalide)."""


class LLM(Protocol):
    model: str

    def complete_json(self, system: str, user: str, schema: type[T], max_tokens: int = 1024) -> T:
        """Une requête → une réponse validée contre `schema`."""
        ...


class AnthropicLLM:
    """Implémentation réelle : `client.messages.parse` avec sortie structurée.

    - Prompt système stable + `cache_control` : le préfixe est mis en cache entre deux avis.
    - Thinking adaptatif, effort moyen : les réponses sont courtes, le coût reste minime.
    """

    def __init__(self, settings: Settings | None = None):
        import anthropic

        settings = settings or get_settings()
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY manquante")
        self.model = settings.anthropic_model
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)

    def complete_json(self, system: str, user: str, schema: type[T], max_tokens: int = 1024) -> T:
        import anthropic

        try:
            response = self._client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium"},
            )
        except anthropic.RateLimitError as exc:
            raise LLMError(f"limite de débit Anthropic : {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API Anthropic {exc.status_code} : {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connexion Anthropic : {exc}") from exc
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMError(f"refus du modèle ({getattr(details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMError("réponse tronquée (max_tokens)")
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("réponse non structurée")
        usage = response.usage
        log.info(
            "llm %s : in=%s cache_read=%s out=%s",
            self.model,
            usage.input_tokens,
            getattr(usage, "cache_read_input_tokens", None),
            usage.output_tokens,
        )
        return parsed


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = AnthropicLLM()
    return _llm


def set_llm(llm: LLM | None) -> None:
    """Injection pour les tests (FakeLLM) ou pour changer d'implémentation."""
    global _llm
    _llm = llm
