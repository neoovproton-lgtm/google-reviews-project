"""Configuration (variables d'environnement / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # Base de données
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'repondu.db'}"
    data_dir: Path = ROOT_DIR / "data"

    # API
    api_token: str | None = None  # si défini : header Authorization: Bearer <token>

    # Navigateur / proxies
    proxy_url: str | None = None  # http://user:pass@host:port
    chromium_executable: str | None = None  # None = Chromium fourni par Playwright
    headless: bool = True
    browser_locale: str = "fr-FR"

    # Scraping Maps (A1)
    maps_base_url: str = "https://www.google.com"
    scrape_min_delay_s: float = 2.0
    scrape_max_delay_s: float = 6.0
    scrape_queries: str = "restaurant"  # séparées par des virgules
    scrape_grid_step_km: float = 1.2
    scrape_zoom: int = 15
    scrape_max_results_per_job: int = 120
    scrape_max_scrolls: int = 40
    scrape_max_attempts: int = 3
    cities_file: Path = ROOT_DIR / "data" / "cities.csv"

    # Avis (A2)
    reviews_sample_size: int = 30
    reviews_min_total: int = 30  # ne pas ouvrir les fiches avec moins d'avis au total

    # Scoring (A3)
    score_min_reviews_per_month: float = 10.0
    score_max_response_rate: float = 0.5

    # Enrichissement (A4)
    enrich_timeout_s: float = 15.0
    enrich_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # LLM (Phase B)
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"

    # Telegram (Phase B)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    log_level: str = Field(default="INFO")

    @property
    def query_list(self) -> list[str]:
        return [q.strip() for q in self.scrape_queries.split(",") if q.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Pour les tests : vide le cache après modification de l'environnement."""
    get_settings.cache_clear()
