"""A2 — test navigateur : fiche Maps (fixture) → détails + 30 avis + métriques."""

import pytest
from sqlalchemy import select

from app.config import reset_settings
from app.models import Prospect, ProspectStatus, Review
from app.scraping.reviews import scrape_reviews, select_prospects

pytestmark = pytest.mark.browser


@pytest.fixture
def seeded(monkeypatch, fixture_server, db):
    monkeypatch.setenv("MAPS_BASE_URL", fixture_server)
    reset_settings()
    with db() as s:
        s.add_all(
            [
                Prospect(
                    place_id="0x1:0x1",
                    name="Chez Fixture (liste)",
                    city="Lyon",
                    review_count=512,
                    maps_url=f"{fixture_server}/maps/place/Chez+Fixture/@45.76,4.83,17z/data=!1s0x1:0x1",
                ),
                Prospect(place_id="0x2:0x2", name="Petit", city="Lyon", review_count=12),
                Prospect(place_id="0x3:0x3", name="Sans URL", city="Lyon", review_count=None),
            ]
        )
    return db


def test_select_prospects_skips_too_few_reviews(seeded):
    from app.config import get_settings

    with seeded() as s:
        ids, skipped = select_prospects(s, None, None, get_settings())
        assert skipped == 1
        assert [s.get(Prospect, i).name for i in ids] == ["Chez Fixture (liste)", "Sans URL"]


def test_scrape_reviews_end_to_end(seeded):
    result = scrape_reviews()
    assert result == {"selected": 2, "done": 1, "errors": 1, "skipped_too_few": 1}
    with seeded() as s:
        p = s.scalars(select(Prospect).where(Prospect.name == "Chez Fixture")).one()
        assert p.status == ProspectStatus.REVIEWS_SCRAPED
        assert p.place_id == "ChIJfixture_ABCDEFGHIJKLMNOPQRS"
        assert p.phone == "+33478000010"
        assert p.website == "https://chezfixture.example/"
        assert p.address == "Adresse: 10 Rue de la Fixture, 69001 Lyon"
        assert p.rating == 4.3 and p.review_count == 512
        assert p.category == "Restaurant français"
        reviews = list(s.scalars(select(Review).where(Review.prospect_id == p.id)))
        assert len(reviews) == 30 and p.reviews_sampled == 30
        by_id = {r.review_id: r for r in reviews}
        assert by_id["rev000"].rating == 5 and by_id["rev000"].has_owner_response == 1
        assert by_id["rev000"].owner_response_text == "Merci pour votre visite n°0 !"
        assert by_id["rev001"].has_owner_response == 0 and by_id["rev001"].rating == 4
        assert by_id["rev002"].text == "Avis numéro 2, décevant."
        assert all(r.date is not None for r in reviews)
        # 10 réponses sur 30 (i % 3 == 0)
        assert p.response_rate == round(10 / 30, 3)
        # Le plus ancien des 30 : "il y a 20 mois" → ≈ 1,5 avis/mois
        assert 1.4 < p.reviews_per_month < 1.6
        # Fenêtre 30 j : rev000..rev005 ; sans réponse : 1,2,4,5 ; dont ≤2★ : rev002, rev004
        assert p.unanswered_last_30d == 4
        assert p.negative_unanswered_last_30d == 2
        sans_url = s.scalars(select(Prospect).where(Prospect.name == "Sans URL")).one()
        assert sans_url.status == ProspectStatus.DISQUALIFIED

    # Relance : idempotent, rien à faire
    assert scrape_reviews()["selected"] == 0
