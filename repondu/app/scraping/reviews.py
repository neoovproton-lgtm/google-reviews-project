"""A2 — Pour chaque prospect : ouvrir la fiche, compléter les détails, lire les 30 derniers avis."""

from __future__ import annotations

import logging

from playwright.sync_api import BrowserContext, Page
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import session_scope
from app.metrics import compute_metrics
from app.models import Prospect, ProspectStatus, Review, utcnow
from app.scraping import dom
from app.scraping.browser import Pacer, browser_session, goto_with_retry
from app.scraping.maps import dismiss_consent
from app.scraping.parsers import extract_place_id, parse_place, parse_review

log = logging.getLogger(__name__)


def select_prospects(
    session: Session, limit: int | None, cities: list[str] | None, settings: Settings
) -> tuple[list[int], int]:
    """Prospects `new` à traiter (les plus commentés d'abord).

    Ceux qui ont trop peu d'avis au total sont marqués `disqualified` sans ouvrir la fiche
    (ils ne peuvent pas atteindre le seuil d'avis/mois). Retourne (ids à traiter, nb écartés).
    """
    stmt = select(Prospect).where(Prospect.status == ProspectStatus.NEW)
    if cities:
        stmt = stmt.where(Prospect.city.in_(cities))
    skipped = 0
    todo: list[Prospect] = []
    for p in session.scalars(stmt):
        if p.review_count is not None and p.review_count < settings.reviews_min_total:
            p.status = ProspectStatus.DISQUALIFIED
            p.reviews_sampled = 0
            p.scored_at = utcnow()
            skipped += 1
        else:
            todo.append(p)
    todo.sort(key=lambda p: (-(p.review_count or 0), p.id))
    if limit is not None:
        todo = todo[:limit]
    return [p.id for p in todo], skipped


def _first_click(page: Page, selectors: list[str]) -> bool:
    for selector in selectors:
        loc = page.locator(selector).first
        if loc.count():
            loc.click()
            return True
    return False


def open_reviews_tab(page: Page, pacer: Pacer) -> None:
    if _first_click(page, dom.REVIEWS_TAB_BUTTONS):
        pacer.wait(factor=0.5)
    if _first_click(page, dom.REVIEWS_SORT_BUTTONS):
        pacer.wait(factor=0.3)
        _first_click(page, dom.REVIEWS_SORT_NEWEST)
        pacer.wait(factor=0.5)
    page.wait_for_selector(dom.REVIEW_ITEM, timeout=15_000)


def collect_reviews(page: Page, pacer: Pacer, settings: Settings) -> list[dict]:
    wanted = settings.reviews_sample_size
    seen: dict[str, dict] = {}
    stale = 0
    for _ in range(settings.scrape_max_scrolls):
        before = len(seen)
        for raw in page.evaluate(dom.EXTRACT_REVIEWS_JS):
            seen.setdefault(raw["review_id"], raw)
        if len(seen) >= wanted:
            break
        stale = stale + 1 if len(seen) == before else 0
        if stale >= 3:
            break
        page.evaluate(dom.SCROLL_REVIEWS_JS)
        pacer.wait(factor=0.5)
    # Déplier les textes tronqués avant l'extraction finale
    for btn in page.locator(dom.REVIEW_MORE_BUTTONS).all()[:wanted]:
        try:
            btn.click(timeout=2_000)
        except Exception:  # noqa: BLE001 - bouton disparu, sans importance
            pass
    final: dict[str, dict] = {}
    for raw in page.evaluate(dom.EXTRACT_REVIEWS_JS):
        final.setdefault(raw["review_id"], raw)
    for rid, raw in seen.items():
        final.setdefault(rid, raw)
    return list(final.values())[:wanted]


def scrape_prospect(
    context: BrowserContext, prospect_id: int, pacer: Pacer, settings: Settings
) -> bool:
    """Retourne True si la fiche a été traitée (même avec 0 avis), False en erreur."""
    with session_scope() as session:
        p = session.get(Prospect, prospect_id)
        assert p is not None
        url = p.maps_url
        if not url:
            p.status = ProspectStatus.DISQUALIFIED
            p.enrich_error = "pas d'URL Maps"
            return False
    page = context.new_page()
    try:
        goto_with_retry(page, url, attempts=2, pacer=pacer)
        dismiss_consent(page)
        page.wait_for_selector(dom.PLACE_TITLE, timeout=20_000)
        details = parse_place(page.evaluate(dom.EXTRACT_PLACE_JS))
        chij = extract_place_id(page.url, page.content())
        open_reviews_tab(page, pacer)
        raw_reviews = collect_reviews(page, pacer, settings)
        now = utcnow()
        parsed = [r for r in (parse_review(r, now=now) for r in raw_reviews) if r]
        metrics = compute_metrics(parsed, settings.reviews_sample_size, now=now)
        with session_scope() as session:
            p = session.get(Prospect, prospect_id)
            assert p is not None
            for field in (
                "name",
                "rating",
                "review_count",
                "category",
                "phone",
                "website",
                "address",
                "lat",
                "lng",
            ):
                if details.get(field) not in (None, ""):
                    setattr(p, field, details[field])
            if chij and chij.startswith("ChIJ") and not p.place_id.startswith("ChIJ"):
                clash = session.scalar(select(Prospect.id).where(Prospect.place_id == chij))
                if clash is None:
                    p.place_id = chij
            existing = {
                r.review_id: r
                for r in session.scalars(select(Review).where(Review.prospect_id == p.id))
            }
            for r in parsed:
                row = existing.get(r["review_id"])
                if row is None:
                    row = Review(prospect_id=p.id, review_id=r["review_id"])
                    session.add(row)
                for k, v in r.items():
                    setattr(row, k, v)
            p.reviews_scraped_at = now
            p.reviews_sampled = metrics.sampled
            p.reviews_per_month = metrics.reviews_per_month
            p.response_rate = metrics.response_rate
            p.unanswered_last_30d = metrics.unanswered_last_30d
            p.negative_unanswered_last_30d = metrics.negative_unanswered_last_30d
            p.status = ProspectStatus.REVIEWS_SCRAPED
        log.info(
            "prospect %d : %d avis, %.1f/mois, %.0f%% de réponse",
            prospect_id,
            metrics.sampled,
            metrics.reviews_per_month or 0,
            (metrics.response_rate or 0) * 100,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.exception("prospect %d : erreur avis", prospect_id)
        with session_scope() as session:
            p = session.get(Prospect, prospect_id)
            if p is not None:
                p.enrich_error = f"reviews: {type(exc).__name__}: {exc}"[:2000]
        return False
    finally:
        page.close()
        pacer.wait()


def scrape_reviews(
    limit: int | None = None,
    cities: list[str] | None = None,
    settings: Settings | None = None,
) -> dict:
    """Point d'entrée A2."""
    settings = settings or get_settings()
    with session_scope() as session:
        ids, skipped = select_prospects(session, limit, cities, settings)
    done = errors = 0
    if ids:
        with browser_session(settings) as (context, pacer):
            for pid in ids:
                if scrape_prospect(context, pid, pacer, settings):
                    done += 1
                else:
                    errors += 1
    return {"selected": len(ids), "done": done, "errors": errors, "skipped_too_few": skipped}
