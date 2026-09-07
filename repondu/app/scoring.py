"""A3 — score = avis_par_mois × (1 − taux_reponse) ; filtre ≥ 10 avis/mois et < 50 % réponse."""

from __future__ import annotations

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import session_scope
from app.models import Prospect, ProspectStatus, utcnow


def compute_score(reviews_per_month: float | None, response_rate: float | None) -> float | None:
    if reviews_per_month is None or response_rate is None:
        return None
    return round(reviews_per_month * (1 - response_rate), 3)


def is_qualified(
    reviews_per_month: float | None,
    response_rate: float | None,
    min_reviews_per_month: float = 10.0,
    max_response_rate: float = 0.5,
) -> bool:
    """≥ seuil d'avis/mois **et** strictement < seuil de réponse (50 % exact = non qualifié)."""
    if reviews_per_month is None or response_rate is None:
        return False
    return reviews_per_month >= min_reviews_per_month and response_rate < max_response_rate


def score_all(settings: Settings | None = None) -> dict:
    """Recalcule score et statut pour tous les prospects ayant des métriques.

    Les `enriched` gardent leur statut (déjà qualifiés et enrichis) mais leur score est rafraîchi.
    """
    settings = settings or get_settings()
    qualified = disqualified = 0
    with session_scope() as session:
        stmt = select(Prospect).where(
            Prospect.status.in_(
                [
                    ProspectStatus.REVIEWS_SCRAPED,
                    ProspectStatus.QUALIFIED,
                    ProspectStatus.DISQUALIFIED,
                    ProspectStatus.ENRICHED,
                ]
            ),
            Prospect.reviews_scraped_at.is_not(None),
        )
        now = utcnow()
        for p in session.scalars(stmt):
            p.score = compute_score(p.reviews_per_month, p.response_rate)
            p.scored_at = now
            ok = is_qualified(
                p.reviews_per_month,
                p.response_rate,
                settings.score_min_reviews_per_month,
                settings.score_max_response_rate,
            )
            if ok:
                qualified += 1
                if p.status != ProspectStatus.ENRICHED:
                    p.status = ProspectStatus.QUALIFIED
            else:
                disqualified += 1
                p.status = ProspectStatus.DISQUALIFIED
    return {"qualified": qualified, "disqualified": disqualified}
