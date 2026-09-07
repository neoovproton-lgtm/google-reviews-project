from datetime import datetime

import pytest

from app.models import Prospect, ProspectStatus
from app.scoring import compute_score, is_qualified, score_all


def test_compute_score():
    assert compute_score(20, 0.25) == 15.0
    assert compute_score(10, 0.0) == 10.0
    assert compute_score(10, 1.0) == 0.0
    assert compute_score(None, 0.1) is None
    assert compute_score(12, None) is None


@pytest.mark.parametrize(
    "rpm,rate,expected",
    [
        (10.0, 0.49, True),  # bords : 10 exact passe
        (10.0, 0.5, False),  # 50 % exact ne passe pas
        (9.99, 0.0, False),
        (30.0, 0.0, True),
        (None, 0.0, False),
        (30.0, None, False),
    ],
)
def test_is_qualified_edges(rpm, rate, expected):
    assert is_qualified(rpm, rate) is expected


def test_is_qualified_custom_thresholds():
    assert is_qualified(5, 0.7, min_reviews_per_month=5, max_response_rate=0.8)


def test_score_all_updates_statuses(db):
    scraped = datetime(2026, 9, 1)
    with db() as s:
        s.add_all(
            [
                Prospect(
                    place_id="a",
                    name="Top",
                    status=ProspectStatus.REVIEWS_SCRAPED,
                    reviews_scraped_at=scraped,
                    reviews_per_month=25,
                    response_rate=0.1,
                ),
                Prospect(
                    place_id="b",
                    name="Répond déjà",
                    status=ProspectStatus.REVIEWS_SCRAPED,
                    reviews_scraped_at=scraped,
                    reviews_per_month=25,
                    response_rate=0.9,
                ),
                Prospect(
                    place_id="c",
                    name="Trop calme",
                    status=ProspectStatus.REVIEWS_SCRAPED,
                    reviews_scraped_at=scraped,
                    reviews_per_month=3,
                    response_rate=0.0,
                ),
                Prospect(
                    place_id="d",
                    name="Déjà enrichi",
                    status=ProspectStatus.ENRICHED,
                    reviews_scraped_at=scraped,
                    reviews_per_month=12,
                    response_rate=0.2,
                    canal_prioritaire="email",
                ),
                Prospect(place_id="e", name="Pas encore d'avis", status=ProspectStatus.NEW),
            ]
        )
    assert score_all() == {"qualified": 2, "disqualified": 2}
    with db() as s:
        by = {p.name: p for p in s.query(Prospect)}
        assert by["Top"].status == ProspectStatus.QUALIFIED and by["Top"].score == 22.5
        assert by["Répond déjà"].status == ProspectStatus.DISQUALIFIED
        assert by["Trop calme"].status == ProspectStatus.DISQUALIFIED
        assert (
            by["Déjà enrichi"].status == ProspectStatus.ENRICHED and by["Déjà enrichi"].score == 9.6
        )
        assert (
            by["Pas encore d'avis"].status == ProspectStatus.NEW
            and by["Pas encore d'avis"].score is None
        )
    # Idempotent
    assert score_all() == {"qualified": 2, "disqualified": 2}
