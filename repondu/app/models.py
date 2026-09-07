"""Tables SQLAlchemy."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ProspectStatus:
    NEW = "new"  # A1 : listé sur Maps
    REVIEWS_SCRAPED = "reviews_scraped"  # A2 : avis extraits, taux calculés
    QUALIFIED = "qualified"  # A3 : passe le filtre
    DISQUALIFIED = "disqualified"  # A3 : ne passe pas le filtre
    ENRICHED = "enriched"  # A4 : contacts enrichis (qualifiés seulement)
    ALL = (NEW, REVIEWS_SCRAPED, QUALIFIED, DISQUALIFIED, ENRICHED)


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(512))
    city: Mapped[str | None] = mapped_column(String(128), index=True)
    category: Mapped[str | None] = mapped_column(String(128))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    phone: Mapped[str | None] = mapped_column(String(64))
    website: Mapped[str | None] = mapped_column(String(512))
    instagram: Mapped[str | None] = mapped_column(String(255))
    facebook: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    maps_url: Mapped[str | None] = mapped_column(String(1024))
    source_query: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default=ProspectStatus.NEW, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    reviews: Mapped[list[Review]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan"
    )

    # A2
    reviews_scraped_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviews_sampled: Mapped[int | None] = mapped_column(Integer)
    reviews_per_month: Mapped[float | None] = mapped_column(Float)
    response_rate: Mapped[float | None] = mapped_column(Float)
    unanswered_last_30d: Mapped[int | None] = mapped_column(Integer)
    negative_unanswered_last_30d: Mapped[int | None] = mapped_column(Integer)

    # A3
    score: Mapped[float | None] = mapped_column(Float, index=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime)

    # A4
    contact_form_url: Mapped[str | None] = mapped_column(String(1024))
    mobile_phone: Mapped[str | None] = mapped_column(String(64))
    canal_prioritaire: Mapped[str | None] = mapped_column(String(32), index=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime)
    enrich_error: Mapped[str | None] = mapped_column(Text)


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"))
    review_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    author: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[datetime | None] = mapped_column(DateTime)
    date_text: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text)
    has_owner_response: Mapped[int] = mapped_column(Integer, default=0)
    owner_response_text: Mapped[str | None] = mapped_column(Text)
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    prospect: Mapped[Prospect] = relationship(back_populates="reviews")


class ScrapeJobStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class ScrapeJob(Base):
    """Une unité de scraping Maps : (ville, requête, point de grille). Permet la reprise."""

    __tablename__ = "scrape_jobs"
    __table_args__ = (UniqueConstraint("city", "query", "lat", "lng", name="uq_scrape_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city: Mapped[str] = mapped_column(String(128), index=True)
    query: Mapped[str] = mapped_column(String(128))
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    zoom: Mapped[int] = mapped_column(Integer, default=15)
    status: Mapped[str] = mapped_column(String(16), default=ScrapeJobStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    places_found: Mapped[int] = mapped_column(Integer, default=0)
    places_new: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
