from datetime import datetime, timedelta

from app.metrics import compute_metrics

NOW = datetime(2026, 9, 7, 12, 0)


def _r(days_ago, rating=5, answered=False, dated=True):
    return {
        "date": NOW - timedelta(days=days_ago) if dated else None,
        "rating": rating,
        "has_owner_response": 1 if answered else 0,
    }


def test_empty():
    m = compute_metrics([], now=NOW)
    assert m.sampled == 0 and m.reviews_per_month is None and m.response_rate is None


def test_thirty_reviews_over_three_months():
    reviews = [_r(days_ago=3 * i + 1, answered=(i % 4 == 0)) for i in range(30)]
    m = compute_metrics(reviews, now=NOW)  # le plus ancien : 88 jours ≈ 2,89 mois
    assert m.sampled == 30
    assert abs(m.reviews_per_month - 30 / (88 / 30.44)) < 0.01
    assert m.response_rate == round(8 / 30, 3)


def test_sample_is_capped_to_most_recent():
    reviews = [_r(days_ago=i) for i in range(40)]
    m = compute_metrics(reviews, sample_size=30, now=NOW)
    assert m.sampled == 30
    assert m.reviews_per_month == 30.0  # 29 jours < 1 mois → plancher 1 mois


def test_floor_one_month():
    m = compute_metrics([_r(1), _r(2), _r(3)], now=NOW)
    assert m.reviews_per_month == 3.0


def test_unanswered_last_30d_and_negatives():
    reviews = [
        _r(5, rating=1),
        _r(10, rating=2, answered=True),
        _r(20, rating=3),
        _r(25, rating=5),
        _r(45, rating=1),  # trop ancien
    ]
    m = compute_metrics(reviews, now=NOW)
    assert m.unanswered_last_30d == 3
    assert m.negative_unanswered_last_30d == 1


def test_undated_reviews_count_but_do_not_extend_window():
    m = compute_metrics([_r(10), _r(0, dated=False)], now=NOW)
    assert m.sampled == 2 and m.reviews_per_month == 2.0
    m2 = compute_metrics([_r(0, dated=False)], now=NOW)
    assert m2.sampled == 1 and m2.reviews_per_month is None and m2.response_rate == 0.0
