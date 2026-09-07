"""A1 — test navigateur sur fixture locale : liste Maps → prospects, reprise des jobs."""

import pytest
from sqlalchemy import select

from app.config import get_settings, reset_settings
from app.models import Prospect, ProspectStatus, ScrapeJob, ScrapeJobStatus
from app.scraping.maps import scrape_cities

pytestmark = pytest.mark.browser


@pytest.fixture
def maps_env(monkeypatch, fixture_server, tmp_path, db):
    monkeypatch.setenv("MAPS_BASE_URL", fixture_server)
    cities = tmp_path / "cities.csv"
    cities.write_text("city,lat,lng,radius_km\nLyon,45.764,4.8357,0.5\nParis,48.85,2.35,0.5\n")
    monkeypatch.setenv("CITIES_FILE", str(cities))
    monkeypatch.setenv("SCRAPE_GRID_STEP_KM", "1.0")
    monkeypatch.setenv("SCRAPE_QUERIES", "restaurant,pizzeria")
    reset_settings()
    return db


def test_scrape_city_fills_prospects_and_resumes(maps_env):
    report = scrape_cities(["Lyon"])
    assert report.jobs_planned == 2  # 1 point × 2 requêtes
    assert report.jobs_run == 2 and report.jobs_done == 2 and report.jobs_error == 0

    with maps_env() as s:
        prospects = list(s.scalars(select(Prospect).order_by(Prospect.id)))
        assert len(prospects) == 7  # dédoublonnés entre les deux requêtes
        p = prospects[0]
        assert p.place_id == "0x47f4ea516ae88791:0x408ab2ae4bb21f1"
        assert p.name == "Restaurant Fixture 1"
        assert p.city == "Lyon" and p.status == ProspectStatus.NEW
        assert p.rating == 4.1 and p.review_count == 137
        assert p.phone == "+33472000001"
        assert p.website == "https://fixture1.example/"
        assert p.category == "Restaurant français"
        assert p.address == "1 Rue de la Fixture"
        assert p.lat == 45.761 and p.lng == 4.831
        assert prospects[1].website is None
        jobs = list(s.scalars(select(ScrapeJob)))
        assert all(j.status == ScrapeJobStatus.DONE for j in jobs)
        assert jobs[0].places_found == 7 and jobs[0].places_new == 7
        assert jobs[1].places_new == 0

    # Reprise : rien à refaire pour Lyon, Paris est planifiée mais pas exécutée (max_jobs=0)
    report2 = scrape_cities(["Lyon"])
    assert report2.jobs_planned == 0 and report2.jobs_run == 0
    report3 = scrape_cities(None, max_jobs=0)
    assert report3.jobs_planned == 2 and report3.jobs_run == 0


def test_job_error_is_recorded_and_retried(maps_env, monkeypatch):
    monkeypatch.setenv("MAPS_BASE_URL", "http://127.0.0.1:9")  # port fermé → erreur réseau
    reset_settings()
    report = scrape_cities(["Lyon"], max_jobs=1)
    assert report.jobs_error == 1
    with maps_env() as s:
        job = s.scalars(select(ScrapeJob).where(ScrapeJob.status == ScrapeJobStatus.ERROR)).one()
        assert job.attempts == 1 and job.error
    # Le job en erreur est repris tant que scrape_max_attempts n'est pas atteint
    assert get_settings().scrape_max_attempts == 3
    report = scrape_cities(["Lyon"], max_jobs=1)
    assert report.jobs_run == 1


def test_unknown_city(maps_env):
    with pytest.raises(ValueError):
        scrape_cities(["Atlantide"])
