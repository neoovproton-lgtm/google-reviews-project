"""Export CSV des prospects (liste qualifiée pour la prospection)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Prospect, ProspectStatus

COLUMNS = [
    "id",
    "place_id",
    "name",
    "city",
    "address",
    "category",
    "rating",
    "review_count",
    "reviews_per_month",
    "response_rate",
    "unanswered_last_30d",
    "negative_unanswered_last_30d",
    "score",
    "phone",
    "mobile_phone",
    "website",
    "email",
    "contact_form_url",
    "instagram",
    "facebook",
    "canal_prioritaire",
    "status",
    "maps_url",
]


def select_for_export(
    session: Session, status: str | None = None, limit: int | None = None, city: str | None = None
) -> list[Prospect]:
    stmt = select(Prospect)
    if status == ProspectStatus.QUALIFIED:
        # « qualifié » au sens large : qualifié ou déjà enrichi
        stmt = stmt.where(Prospect.status.in_([ProspectStatus.QUALIFIED, ProspectStatus.ENRICHED]))
    elif status:
        stmt = stmt.where(Prospect.status == status)
    if city:
        stmt = stmt.where(Prospect.city == city)
    stmt = stmt.order_by(Prospect.score.desc().nullslast(), Prospect.id)
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def to_csv(prospects: list[Prospect]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for p in prospects:
        writer.writerow({c: getattr(p, c) for c in COLUMNS})
    return buf.getvalue()


def write_export(session: Session, out_dir: Path, status: str | None, limit: int | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"prospects-{status or 'all'}-{stamp}.csv"
    path.write_text(to_csv(select_for_export(session, status, limit)), encoding="utf-8")
    return path
