"""A4 — enrichissement bout en bout contre un mini-site servi en local (sans navigateur)."""

from fastapi.testclient import TestClient

from app.config import reset_settings
from app.enrichment.run import enrich_all
from app.enrichment.website import enrich_from_website
from app.models import Prospect, ProspectStatus


def test_enrich_from_website_follows_contact_page(fixture_server):
    ex = enrich_from_website(f"{fixture_server}/site/index.html")
    assert ex.emails[0] == "contact@chezfixture.example"
    assert ex.contact_form_url == f"{fixture_server}/contact"
    assert ex.instagram == "chezfixture" and ex.facebook == "ChezFixtureLyon"
    assert ex.mobile_phone == "+33612345678"


def test_social_or_platform_website_is_not_fetched():
    ex = enrich_from_website("https://www.facebook.com/ChezMarcel/")
    assert ex.facebook == "ChezMarcel" and ex.emails == []
    assert enrich_from_website("https://www.ubereats.com/fr/store/x").emails == []


def test_enrich_all_sets_channels_and_status(db, fixture_server, monkeypatch):
    monkeypatch.setenv("ENRICH_TIMEOUT_S", "2")
    reset_settings()
    with db() as s:
        s.add_all(
            [
                Prospect(
                    place_id="1",
                    name="Site complet",
                    status=ProspectStatus.QUALIFIED,
                    score=30,
                    website=f"{fixture_server}/site/index.html",
                    phone="+33478000010",
                ),
                Prospect(
                    place_id="2",
                    name="Site vide",
                    status=ProspectStatus.QUALIFIED,
                    score=20,
                    website=f"{fixture_server}/site/nosite.html",
                    phone="+33478000011",
                ),
                Prospect(
                    place_id="3",
                    name="Sans site, mobile",
                    status=ProspectStatus.QUALIFIED,
                    score=10,
                    phone="+33612345679",
                ),
                Prospect(
                    place_id="4",
                    name="Site injoignable",
                    status=ProspectStatus.QUALIFIED,
                    score=5,
                    website="http://127.0.0.1:9/",
                    phone="+33478000012",
                ),
                Prospect(
                    place_id="5",
                    name="Pas qualifié",
                    status=ProspectStatus.DISQUALIFIED,
                    website=f"{fixture_server}/site/index.html",
                ),
            ]
        )
    result = enrich_all()
    assert result["selected"] == 4 and result["done"] == 4 and result["errors"] == 0
    assert result["with_written_channel"] == 3 and result["written_channel_rate"] == 0.75
    with db() as s:
        by = {p.name: p for p in s.query(Prospect)}
        p = by["Site complet"]
        assert p.status == ProspectStatus.ENRICHED and p.enriched_at is not None
        assert p.email == "contact@chezfixture.example"
        assert p.contact_form_url == f"{fixture_server}/contact"
        assert p.instagram == "chezfixture" and p.facebook == "ChezFixtureLyon"
        assert p.mobile_phone == "+33612345678" and p.canal_prioritaire == "email"
        # Accueil sans contact, mais le sondage de /contact trouve l'email
        assert by["Site vide"].canal_prioritaire == "email"
        assert by["Sans site, mobile"].canal_prioritaire == "sms"
        assert by["Sans site, mobile"].mobile_phone == "+33612345679"
        assert by["Site injoignable"].status == ProspectStatus.ENRICHED
        assert by["Site injoignable"].canal_prioritaire == "telephone"
        assert by["Pas qualifié"].status == ProspectStatus.DISQUALIFIED
    assert enrich_all()["selected"] == 0  # idempotent

    from app.main import app

    with TestClient(app) as c:
        q = c.get("/stats").json()["qualified"]
        assert q["total"] == 4 and q["with_written_channel"] == 3
        assert q["written_channel_rate"] == 0.75
        assert q["by_channel"] == {"email": 2, "telephone": 1, "sms": 1}
