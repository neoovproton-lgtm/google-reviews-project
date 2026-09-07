"""Enchaînement des étapes A1 → A4, utilisé par l'API et la CLI."""

from __future__ import annotations

from app.config import get_settings


def run_pipeline(
    mode: str,
    cities: list[str] | None = None,
    max_jobs: int | None = None,
    limit: int | None = None,
) -> dict:
    settings = get_settings()
    out: dict = {}
    if mode in ("places", "all"):
        from app.scraping.maps import scrape_cities

        r = scrape_cities(cities, max_jobs=max_jobs, settings=settings)
        out["places"] = {
            "jobs_planned": r.jobs_planned,
            "jobs_run": r.jobs_run,
            "jobs_done": r.jobs_done,
            "jobs_error": r.jobs_error,
        }
    if mode in ("reviews", "all"):
        from app.scraping.reviews import scrape_reviews

        out["reviews"] = scrape_reviews(limit=limit, cities=cities, settings=settings)
    if mode in ("score", "all"):
        from app.scoring import score_all

        out["score"] = score_all(settings=settings)
    if mode in ("enrich", "all"):
        from app.enrichment.run import enrich_all

        out["enrich"] = enrich_all(limit=limit, settings=settings)
    return out
