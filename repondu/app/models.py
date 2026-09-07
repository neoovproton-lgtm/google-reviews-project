"""Tables SQLAlchemy."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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


# --- Phase B ---------------------------------------------------------------------------------


class Establishment(Base):
    """Client (restaurant en essai). Profil utilisé par le moteur de réponse (B3)."""

    __tablename__ = "establishments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    cuisine_type: Mapped[str | None] = mapped_column(String(128))
    tone: Mapped[str] = mapped_column(String(64), default="chaleureux et professionnel")
    signature: Mapped[str | None] = mapped_column(String(255))
    manager_first_name: Mapped[str | None] = mapped_column(String(128))
    never_say: Mapped[str | None] = mapped_column(Text)
    use_tutoiement: Mapped[int] = mapped_column(Integer, default=0)
    auto_publish_delay_h: Mapped[int] = mapped_column(Integer, default=24)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), index=True)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    replies: Mapped[list[Reply]] = relationship(back_populates="establishment")


class ReplyStatus:
    PENDING = "pending"  # en attente de validation / veto
    APPROVED = "approved"  # validé (humain ou délai écoulé sans veto)
    REJECTED = "rejected"
    PUBLISHED = "published"
    ALL = (PENDING, APPROVED, REJECTED, PUBLISHED)


class Reply(Base):
    """Brouillon de réponse à un avis (B1), avec drapeaux de sécurité (B2)."""

    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id", ondelete="CASCADE"), index=True)
    establishment_id: Mapped[int] = mapped_column(ForeignKey("establishments.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    detail_reused: Mapped[str | None] = mapped_column(String(255))
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    needs_human: Mapped[int] = mapped_column(Integer, default=0)
    safety_flags: Mapped[list | None] = mapped_column(JSON)
    check_issues: Mapped[list | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=ReplyStatus.PENDING, index=True)
    decision_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    establishment: Mapped[Establishment] = relationship(back_populates="replies")
    review: Mapped[Review] = relationship()


class TelegramSession(Base):
    """État du formulaire d'onboarding par conversation Telegram (B3)."""

    __tablename__ = "telegram_sessions"

    chat_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(64))
    answers: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# --- Phase C ---------------------------------------------------------------------------------


class Mailbox(Base):
    """Boîte d'envoi (C2/C5). Les identifiants IMAP/SMTP restent dans data/mailboxes.json."""

    __tablename__ = "mailboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(32), default="log")  # resend | brevo | log
    daily_quota: Mapped[int] = mapped_column(Integer, default=30)
    warmup_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    active: Mapped[int] = mapped_column(Integer, default=1)
    paused_reason: Mapped[str | None] = mapped_column(String(255))
    sent_total: Mapped[int] = mapped_column(Integer, default=0)
    delivered_total: Mapped[int] = mapped_column(Integer, default=0)
    bounced_total: Mapped[int] = mapped_column(Integer, default=0)
    complained_total: Mapped[int] = mapped_column(Integer, default=0)
    opened_total: Mapped[int] = mapped_column(Integer, default=0)
    last_inbox_check_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutreachStatus:
    ACTIVE = "active"
    REPLIED = "replied"
    YES = "yes"
    OBJECTION = "objection"
    OPTED_OUT = "opted_out"
    BOUNCED = "bounced"
    DONE = "done"  # séquence terminée sans réponse
    STOPPED = "stopped"  # arrêt manuel
    ALL = (ACTIVE, REPLIED, YES, OBJECTION, OPTED_OUT, BOUNCED, DONE, STOPPED)
    TERMINAL = (REPLIED, YES, OBJECTION, OPTED_OUT, BOUNCED, DONE, STOPPED)


class Outreach(Base):
    """Une séquence de prospection par prospect (un seul canal à la fois)."""

    __tablename__ = "outreach"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), unique=True, index=True)
    channel: Mapped[str] = mapped_column(
        String(16)
    )  # email | formulaire | instagram | facebook | sms
    contact: Mapped[str | None] = mapped_column(String(512))  # adresse, URL, handle ou numéro
    status: Mapped[str] = mapped_column(String(16), default=OutreachStatus.ACTIVE, index=True)
    step: Mapped[int] = mapped_column(Integer, default=0)  # dernière étape envoyée (0..3)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id"))
    examples: Mapped[list | None] = mapped_column(
        JSON
    )  # [{review_id, author, rating, text, reply}]
    token: Mapped[str | None] = mapped_column(String(32), unique=True)  # page publique /p/{token}
    outcome_note: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    prospect: Mapped[Prospect] = relationship()
    mailbox: Mapped[Mailbox | None] = relationship()
    messages: Mapped[list[OutreachMessage]] = relationship(
        back_populates="outreach", cascade="all, delete-orphan"
    )


class MessageStatus:
    PREPARED = "prepared"
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    BOUNCED = "bounced"
    FAILED = "failed"
    MANUAL_PENDING = "manual_pending"  # DM à envoyer à la main
    MANUAL_SENT = "manual_sent"
    ALL = (PREPARED, SENT, DELIVERED, OPENED, BOUNCED, FAILED, MANUAL_PENDING, MANUAL_SENT)
    SENT_LIKE = (SENT, DELIVERED, OPENED, BOUNCED, MANUAL_SENT)


class OutreachMessage(Base):
    __tablename__ = "outreach_messages"
    __table_args__ = (UniqueConstraint("outreach_id", "step", name="uq_outreach_step"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outreach_id: Mapped[int] = mapped_column(
        ForeignKey("outreach.id", ondelete="CASCADE"), index=True
    )
    step: Mapped[int] = mapped_column(Integer)  # 1 = J0, 2 = J+3, 3 = J+8
    channel: Mapped[str] = mapped_column(String(16))
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    subject: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=MessageStatus.PREPARED, index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    check_issues: Mapped[list | None] = mapped_column(JSON)
    prepared_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime)

    outreach: Mapped[Outreach] = relationship(back_populates="messages")
    mailbox: Mapped[Mailbox | None] = relationship()


class EmailEvent(Base):
    """Événement fournisseur (webhook) ou boîte de réception (IMAP)."""

    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_messages.id"), index=True)
    outreach_id: Mapped[int | None] = mapped_column(ForeignKey("outreach.id"), index=True)
    type: Mapped[str] = mapped_column(
        String(32), index=True
    )  # delivered|bounced|complained|opened|replied
    source: Mapped[str | None] = mapped_column(String(32))  # resend | brevo | imap | manual
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    payload: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OptOut(Base):
    """Registre des désinscriptions (email, handle, numéro), tous canaux."""

    __tablename__ = "optouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact: Mapped[str] = mapped_column(String(512), unique=True)
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
