"""Métriques par fiche calculées sur l'échantillon d'avis (code pur, testé)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

DAYS_PER_MONTH = 30.44
NEGATIVE_MAX_RATING = 2  # ≤ 2★ = avis négatif


@dataclass(frozen=True)
class ReviewMetrics:
    sampled: int
    reviews_per_month: float | None
    response_rate: float | None
    unanswered_last_30d: int
    negative_unanswered_last_30d: int


def compute_metrics(
    reviews: list[dict[str, Any]], sample_size: int = 30, now: datetime | None = None
) -> ReviewMetrics:
    """`reviews` : dicts avec `date` (datetime|None), `rating` (int|None), `has_owner_response`.

    - `reviews_per_month` = taille de l'échantillon / mois écoulés depuis l'avis le plus ancien
      de l'échantillon (plancher : 1 mois). Les avis sans date sont comptés mais n'étendent
      pas la fenêtre.
    - `response_rate` = part des avis de l'échantillon ayant une réponse du propriétaire.
    """
    now = now or datetime.now(UTC).replace(tzinfo=None)
    dated = sorted((r for r in reviews if r.get("date")), key=lambda r: r["date"], reverse=True)
    undated = [r for r in reviews if not r.get("date")]
    sample = (dated + undated)[:sample_size]
    n = len(sample)
    if n == 0:
        return ReviewMetrics(0, None, None, 0, 0)

    dates = [r["date"] for r in sample if r.get("date")]
    if dates:
        months = max(1.0, (now - min(dates)).total_seconds() / 86400 / DAYS_PER_MONTH)
        per_month = round(n / months, 2)
    else:
        per_month = None

    answered = sum(1 for r in sample if r.get("has_owner_response"))
    response_rate = round(answered / n, 3)

    cutoff = now - timedelta(days=30)
    recent_unanswered = [
        r
        for r in sample
        if r.get("date") and r["date"] >= cutoff and not r.get("has_owner_response")
    ]
    negative = [
        r
        for r in recent_unanswered
        if r.get("rating") is not None and r["rating"] <= NEGATIVE_MAX_RATING
    ]
    return ReviewMetrics(n, per_month, response_rate, len(recent_unanswered), len(negative))
