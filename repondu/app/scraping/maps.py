"""A1 — Liste des restaurants d'une ville via la recherche Google Maps.

Pipeline : villes (cities.csv) → grille de points × requêtes → `scrape_jobs` → pour chaque job,
scroll de la liste de résultats → upsert `prospects` par place_id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from playwright.sync_api import BrowserContext, Page
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import session_scope
from app.models import Prospect, ProspectStatus, ScrapeJob, ScrapeJobStatus, utcnow
from app.scraping import dom
from app.scraping.browser import Pacer, browser_session, goto_with_retry
from app.scraping.geo import City, grid_points, load_cities
from app.scraping.parsers import parse_card

log = logging.getLogger(__name__)


# --- Jobs -----------------------------------------------------------------------------------


def plan_jobs(session: Session, cities: list[City], settings: Settings) -> int:
    """Crée les jobs manquants pour ces villes (idempotent). Retourne le nb de jobs créés."""
    created = 0
    for city in cities:
        existing = {
            (j.query, j.lat, j.lng)
            for j in session.scalars(select(ScrapeJob).where(ScrapeJob.city == city.name))
        }
        for query in settings.query_list:
            for lat, lng in grid_points(city, settings.scrape_grid_step_km):
                if (query, lat, lng) in existing:
                    continue
                session.add(
                    ScrapeJob(
                        city=city.name, query=query, lat=lat, lng=lng, zoom=settings.scrape_zoom
                    )
                )
                created += 1
    session.flush()
    return created


def pending_jobs(session: Session, cities: list[str] | None, settings: Settings) -> list[ScrapeJob]:
    stmt = select(ScrapeJob).where(
        ScrapeJob.status.in_([ScrapeJobStatus.PENDING, ScrapeJobStatus.ERROR]),
        ScrapeJob.attempts < settings.scrape_max_attempts,
    )
    if cities:
        stmt = stmt.where(ScrapeJob.city.in_(cities))
    return list(session.scalars(stmt.order_by(ScrapeJob.city, ScrapeJob.id)))


def search_url(settings: Settings, query: str, lat: float, lng: float, zoom: int) -> str:
    return f"{settings.maps_base_url}/maps/search/{quote(query)}/@{lat},{lng},{zoom}z?hl=fr"


# --- Navigation -----------------------------------------------------------------------------


def dismiss_consent(page: Page) -> None:
    if dom.CONSENT_URL_FRAGMENT not in page.url:
        return
    for selector in dom.CONSENT_BUTTONS:
        btn = page.locator(selector).first
        if btn.count():
            btn.click()
            page.wait_for_load_state("domcontentloaded")
            return
    log.warning("Page de consentement non gérée : %s", page.url)


def collect_cards(page: Page, pacer: Pacer, settings: Settings) -> list[dict]:
    """Scrolle la liste jusqu'à la fin (ou la limite) et renvoie les cartes brutes."""
    page.wait_for_selector(dom.RESULTS_FEED, timeout=20_000)
    cards: dict[str, dict] = {}
    stale_rounds = 0
    for _ in range(settings.scrape_max_scrolls):
        before = len(cards)
        for raw in page.evaluate(dom.EXTRACT_CARDS_JS):
            cards.setdefault(raw.get("href", ""), raw)
        if len(cards) >= settings.scrape_max_results_per_job:
            break
        if page.evaluate(dom.END_OF_LIST_JS):
            break
        stale_rounds = stale_rounds + 1 if len(cards) == before else 0
        if stale_rounds >= 3:
            break
        page.evaluate(dom.SCROLL_FEED_JS)
        pacer.wait(factor=0.5)
    return list(cards.values())


def upsert_prospect(session: Session, data: dict, city: str, query: str) -> bool:
    """Insère ou met à jour un prospect. Retourne True s'il est nouveau."""
    prospect = session.scalar(select(Prospect).where(Prospect.place_id == data["place_id"]))
    is_new = prospect is None
    if prospect is None:
        prospect = Prospect(place_id=data["place_id"], name=data["name"] or "?", city=city)
        prospect.source_query = query
        session.add(prospect)
    for field in (
        "name",
        "address",
        "category",
        "rating",
        "review_count",
        "phone",
        "website",
        "maps_url",
        "lat",
        "lng",
    ):
        value = data.get(field)
        if value not in (None, ""):
            setattr(prospect, field, value)
    if not prospect.city:
        prospect.city = city
    return is_new


def run_job(context: BrowserContext, job_id: int, pacer: Pacer, settings: Settings) -> None:
    """Exécute un job dans sa propre transaction ; les erreurs sont enregistrées sur le job."""
    with session_scope() as session:
        job = session.get(ScrapeJob, job_id)
        assert job is not None
        job.status = ScrapeJobStatus.RUNNING
        job.attempts += 1
        url = search_url(settings, job.query, job.lat, job.lng, job.zoom)
        city, query = job.city, job.query
    page = context.new_page()
    try:
        goto_with_retry(page, url, attempts=2, pacer=pacer)
        dismiss_consent(page)
        raw_cards = collect_cards(page, pacer, settings)
        parsed = [p for p in (parse_card(c) for c in raw_cards) if p]
        with session_scope() as session:
            new = sum(upsert_prospect(session, p, city, query) for p in parsed)
            job = session.get(ScrapeJob, job_id)
            assert job is not None
            job.status = ScrapeJobStatus.DONE
            job.places_found = len(parsed)
            job.places_new = new
            job.error = None
        log.info(
            "job %d %s@%s,%s : %d fiches (%d nouvelles)", job_id, query, url, "", len(parsed), new
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("job %d en erreur", job_id)
        with session_scope() as session:
            job = session.get(ScrapeJob, job_id)
            assert job is not None
            job.status = ScrapeJobStatus.ERROR
            job.error = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        page.close()
    pacer.wait()


@dataclass
class ScrapeReport:
    jobs_planned: int
    jobs_run: int
    jobs_done: int
    jobs_error: int
    started_at: datetime
    finished_at: datetime | None = None


def scrape_cities(
    city_names: list[str] | None = None,
    max_jobs: int | None = None,
    settings: Settings | None = None,
) -> ScrapeReport:
    """Point d'entrée A1 : planifie puis exécute les jobs en attente pour ces villes."""
    settings = settings or get_settings()
    all_cities = load_cities(settings.cities_file)
    if city_names:
        wanted = {c.lower() for c in city_names}
        cities = [c for c in all_cities if c.name.lower() in wanted]
        missing = wanted - {c.name.lower() for c in cities}
        if missing:
            raise ValueError(f"Villes inconnues dans {settings.cities_file}: {sorted(missing)}")
    else:
        cities = all_cities
    report = ScrapeReport(0, 0, 0, 0, started_at=utcnow())
    with session_scope() as session:
        report.jobs_planned = plan_jobs(session, cities, settings)
        jobs = pending_jobs(session, [c.name for c in cities], settings)
    if max_jobs is not None:
        jobs = jobs[:max_jobs]
    job_ids = [j.id for j in jobs]
    if not job_ids:
        report.finished_at = utcnow()
        return report
    with browser_session(settings) as (context, pacer):
        for job_id in job_ids:
            run_job(context, job_id, pacer, settings)
            report.jobs_run += 1
    with session_scope() as session:
        for job_id in job_ids:
            job = session.get(ScrapeJob, job_id)
            if job and job.status == ScrapeJobStatus.DONE:
                report.jobs_done += 1
            elif job and job.status == ScrapeJobStatus.ERROR:
                report.jobs_error += 1
    report.finished_at = utcnow()
    return report


def prospect_counts(session: Session) -> dict[str, int]:
    from sqlalchemy import func

    rows = session.execute(select(Prospect.status, func.count()).group_by(Prospect.status)).all()
    counts = {status: 0 for status in ProspectStatus.ALL}
    counts.update({s: n for s, n in rows})
    return counts
