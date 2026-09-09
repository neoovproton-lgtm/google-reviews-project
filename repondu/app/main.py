"""API HTTP pilotée par OpenClaw. L'appli exécute, OpenClaw orchestre."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session, init_db
from app.models import (
    Establishment,
    Mailbox,
    Outreach,
    OutreachStatus,
    Prospect,
    ProspectStatus,
    Reply,
    ReplyStatus,
    Review,
    utcnow,
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=get_settings().log_level)
    init_db()
    yield


app = FastAPI(title="Répondu", version="0.1.0", lifespan=lifespan)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    token = get_settings().api_token
    if not token:
        return
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Token invalide")


SessionDep = Annotated[Session, Depends(get_session)]
AuthDep = Depends(require_token)


# --- Jobs en arrière-plan (un seul à la fois : un seul navigateur) -------------------------


class JobState(BaseModel):
    id: str
    mode: str
    status: Literal["running", "done", "error"] = "running"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    result: dict | None = None
    error: str | None = None


_jobs: dict[str, JobState] = {}
_job_lock = threading.Lock()
_running = threading.Event()


def _run_in_thread(job: JobState, fn) -> None:
    def target():
        try:
            job.result = fn()
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s en erreur", job.id)
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = utcnow()
            _running.clear()

    threading.Thread(target=target, name=f"job-{job.id}", daemon=True).start()


class ScrapeRequest(BaseModel):
    mode: Literal["places", "reviews", "score", "enrich", "all"] = "places"
    cities: list[str] | None = None
    max_jobs: int | None = None
    limit: int | None = None


@app.post("/scrape", dependencies=[AuthDep], status_code=202)
def post_scrape(req: ScrapeRequest) -> JobState:
    with _job_lock:
        if _running.is_set():
            raise HTTPException(status_code=409, detail="Un job est déjà en cours")
        _running.set()
        job = JobState(id=uuid.uuid4().hex[:12], mode=req.mode)
        _jobs[job.id] = job
    _run_in_thread(job, lambda: run_mode(req))
    return job


def run_mode(req: ScrapeRequest) -> dict:
    from app.pipeline import run_pipeline

    return run_pipeline(req.mode, cities=req.cities, max_jobs=req.max_jobs, limit=req.limit)


@app.get("/jobs/{job_id}", dependencies=[AuthDep])
def get_job(job_id: str) -> JobState:
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job inconnu")
    return job


# --- Prospects ------------------------------------------------------------------------------


class ProspectOut(BaseModel):
    id: int
    place_id: str
    name: str
    city: str | None
    address: str | None
    category: str | None
    rating: float | None
    review_count: int | None
    phone: str | None
    website: str | None
    email: str | None
    instagram: str | None
    facebook: str | None
    maps_url: str | None
    status: str
    reviews_per_month: float | None
    response_rate: float | None
    unanswered_last_30d: int | None
    negative_unanswered_last_30d: int | None
    score: float | None
    contact_form_url: str | None
    mobile_phone: str | None
    canal_prioritaire: str | None

    model_config = {"from_attributes": True}


@app.get("/prospects", dependencies=[AuthDep])
def list_prospects(
    session: SessionDep,
    status: str | None = Query(default=None),
    city: str | None = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
) -> list[ProspectOut]:
    if status and status not in ProspectStatus.ALL:
        raise HTTPException(status_code=422, detail=f"status doit être parmi {ProspectStatus.ALL}")
    stmt = select(Prospect)
    if status:
        stmt = stmt.where(Prospect.status == status)
    if city:
        stmt = stmt.where(Prospect.city == city)
    stmt = stmt.order_by(Prospect.score.desc().nullslast(), Prospect.id).limit(limit).offset(offset)
    return [ProspectOut.model_validate(p) for p in session.scalars(stmt)]


@app.get("/prospects/export.csv", dependencies=[AuthDep])
def export_prospects(
    session: SessionDep,
    status: str | None = Query(default=ProspectStatus.QUALIFIED),
    city: str | None = None,
    limit: int = Query(default=300, le=10000),
) -> Response:
    from app.export import select_for_export, to_csv

    if status and status not in ProspectStatus.ALL:
        raise HTTPException(status_code=422, detail=f"status doit être parmi {ProspectStatus.ALL}")
    body = to_csv(select_for_export(session, status, limit, city))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="prospects-{status or "all"}.csv"'},
    )


@app.get("/prospects/{prospect_id}", dependencies=[AuthDep])
def get_prospect(prospect_id: int, session: SessionDep) -> ProspectOut:
    p = session.get(Prospect, prospect_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prospect inconnu")
    return ProspectOut.model_validate(p)


@app.get("/stats", dependencies=[AuthDep])
def stats(session: SessionDep) -> dict:
    from app.stats import compute_stats

    return compute_stats(session)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Phase B : établissements, brouillons, Telegram ----------------------------------------


class EstablishmentIn(BaseModel):
    name: str
    prospect_id: int | None = None
    cuisine_type: str | None = None
    tone: str = "chaleureux et professionnel"
    signature: str | None = None
    manager_first_name: str | None = None
    never_say: str | None = None
    use_tutoiement: bool = False
    auto_publish_delay_h: int = 24
    telegram_chat_id: str | None = None
    contact_email: str | None = None


class EstablishmentOut(EstablishmentIn):
    id: int
    active: bool
    onboarding_status: str = "created"
    outreach_id: int | None = None
    mobile_phone: str | None = None
    invited_at: datetime | None = None
    manager_added_at: datetime | None = None
    trial_started_at: datetime | None = None
    trial_ends_at: datetime | None = None
    baseline_response_rate: float | None = None
    baseline_rating: float | None = None
    model_config = {"from_attributes": True}


@app.post("/establishments", dependencies=[AuthDep], status_code=201)
def create_establishment(body: EstablishmentIn, session: SessionDep) -> EstablishmentOut:
    if body.prospect_id is not None and session.get(Prospect, body.prospect_id) is None:
        raise HTTPException(status_code=404, detail="Prospect inconnu")
    data = body.model_dump()
    data["use_tutoiement"] = int(data["use_tutoiement"])
    est = Establishment(**data)
    session.add(est)
    session.flush()
    return EstablishmentOut.model_validate(est)


@app.get("/establishments", dependencies=[AuthDep])
def list_establishments(session: SessionDep) -> list[EstablishmentOut]:
    return [EstablishmentOut.model_validate(e) for e in session.scalars(select(Establishment))]


class ReplyOut(BaseModel):
    id: int
    review_id: int
    establishment_id: int
    establishment_name: str
    review_rating: int | None
    review_author: str | None
    review_text: str | None
    text: str
    needs_human: bool
    safety_flags: list | None
    check_issues: list | None
    status: str
    created_at: datetime
    decided_at: datetime | None
    decision_by: str | None

    @classmethod
    def from_reply(cls, r: Reply) -> ReplyOut:
        return cls(
            id=r.id,
            review_id=r.review_id,
            establishment_id=r.establishment_id,
            establishment_name=r.establishment.name,
            review_rating=r.review.rating,
            review_author=r.review.author,
            review_text=r.review.text,
            text=r.text,
            needs_human=bool(r.needs_human),
            safety_flags=r.safety_flags,
            check_issues=r.check_issues,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.decided_at,
            decision_by=r.decision_by,
        )


class DraftRequest(BaseModel):
    limit: int | None = None
    notify: bool = True


@app.post("/establishments/{establishment_id}/draft", dependencies=[AuthDep])
def draft_replies(establishment_id: int, body: DraftRequest, session: SessionDep) -> list[ReplyOut]:
    from app.replies.service import draft_for_establishment
    from app.telegram.client import get_telegram

    est = session.get(Establishment, establishment_id)
    if est is None:
        raise HTTPException(status_code=404, detail="Établissement inconnu")
    drafts = draft_for_establishment(
        session, est, limit=body.limit, telegram=get_telegram() if body.notify else None
    )
    return [ReplyOut.from_reply(r) for r in drafts]


@app.get("/reviews/pending", dependencies=[AuthDep])
def reviews_pending(session: SessionDep, establishment_id: int | None = None) -> list[ReplyOut]:
    from app.replies.service import pending_replies

    return [ReplyOut.from_reply(r) for r in pending_replies(session, establishment_id)]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    text: str | None = None
    by: str = "api"


@app.post("/reviews/{review_id}/decision", dependencies=[AuthDep])
def review_decision(review_id: int, body: DecisionIn, session: SessionDep) -> ReplyOut:
    from app.replies.service import decide, latest_reply_for_review

    if session.get(Review, review_id) is None:
        raise HTTPException(status_code=404, detail="Avis inconnu")
    reply = latest_reply_for_review(session, review_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="Aucun brouillon pour cet avis")
    try:
        decide(session, reply, body.decision, by=body.by, text=body.text)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReplyOut.from_reply(reply)


@app.post("/reviews/auto-approve", dependencies=[AuthDep])
def reviews_auto_approve(session: SessionDep) -> dict:
    from app.replies.service import auto_approve_due

    approved = auto_approve_due(session)
    return {"approved": [r.id for r in approved]}


@app.get("/replies", dependencies=[AuthDep])
def list_replies(session: SessionDep, status: str | None = None) -> list[ReplyOut]:
    if status and status not in ReplyStatus.ALL:
        raise HTTPException(status_code=422, detail=f"status doit être parmi {ReplyStatus.ALL}")
    stmt = select(Reply)
    if status:
        stmt = stmt.where(Reply.status == status)
    return [ReplyOut.from_reply(r) for r in session.scalars(stmt.order_by(Reply.id))]


@app.post("/telegram/webhook")
def telegram_webhook(
    update: dict,
    session: SessionDep,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict:
    from app.telegram.bot import handle_update
    from app.telegram.client import get_telegram

    secret = get_settings().telegram_webhook_secret
    if secret and x_telegram_bot_api_secret_token != secret:
        raise HTTPException(status_code=401, detail="Secret invalide")
    handle_update(update, session, get_telegram())
    return {"ok": True}


# --- Phase C : prospection --------------------------------------------------------------------


class OutreachRunRequest(BaseModel):
    limit: int | None = None
    force_window: bool = False


@app.post("/outreach/run", dependencies=[AuthDep], status_code=202)
def post_outreach_run(req: OutreachRunRequest) -> JobState:
    from app.outreach.sequence import run_outreach

    with _job_lock:
        if _running.is_set():
            raise HTTPException(status_code=409, detail="Un job est déjà en cours")
        _running.set()
        job = JobState(id=uuid.uuid4().hex[:12], mode="outreach")
        _jobs[job.id] = job

    def work():
        r = run_outreach(limit=req.limit, force_window=req.force_window)
        return {
            "enrolled": r.enrolled,
            "sent": r.sent,
            "prepared_manual": r.prepared_manual,
            "skipped_window": r.skipped_window,
            "skipped_quota": r.skipped_quota,
            "errors": r.errors,
            "details": r.details[:50],
        }

    _run_in_thread(job, work)
    return job


class OutreachMessageOut(BaseModel):
    id: int
    step: int
    channel: str
    subject: str | None
    body: str
    status: str
    mailbox_id: int | None
    provider_message_id: str | None
    check_issues: list | None
    sent_at: datetime | None
    delivered_at: datetime | None
    opened_at: datetime | None
    bounced_at: datetime | None
    model_config = {"from_attributes": True}


class OutreachOut(BaseModel):
    id: int
    prospect_id: int
    prospect_name: str
    channel: str
    contact: str | None
    status: str
    step: int
    next_action_at: datetime | None
    mailbox_id: int | None
    token: str | None
    outcome_note: str | None
    started_at: datetime | None
    last_sent_at: datetime | None
    replied_at: datetime | None
    messages: list[OutreachMessageOut] = []

    @classmethod
    def from_outreach(cls, o: Outreach, with_messages: bool = False) -> OutreachOut:
        return cls(
            id=o.id,
            prospect_id=o.prospect_id,
            prospect_name=o.prospect.name,
            channel=o.channel,
            contact=o.contact,
            status=o.status,
            step=o.step,
            next_action_at=o.next_action_at,
            mailbox_id=o.mailbox_id,
            token=o.token,
            outcome_note=o.outcome_note,
            started_at=o.started_at,
            last_sent_at=o.last_sent_at,
            replied_at=o.replied_at,
            messages=[OutreachMessageOut.model_validate(m) for m in o.messages]
            if with_messages
            else [],
        )


@app.get("/outreach", dependencies=[AuthDep])
def list_outreach(
    session: SessionDep,
    status: str | None = None,
    channel: str | None = None,
    limit: int = Query(default=100, le=1000),
) -> list[OutreachOut]:
    if status and status not in OutreachStatus.ALL:
        raise HTTPException(status_code=422, detail=f"status doit être parmi {OutreachStatus.ALL}")
    stmt = select(Outreach)
    if status:
        stmt = stmt.where(Outreach.status == status)
    if channel:
        stmt = stmt.where(Outreach.channel == channel)
    return [
        OutreachOut.from_outreach(o)
        for o in session.scalars(stmt.order_by(Outreach.id).limit(limit))
    ]


@app.get("/outreach/{outreach_id}", dependencies=[AuthDep])
def get_outreach(outreach_id: int, session: SessionDep) -> OutreachOut:
    o = session.get(Outreach, outreach_id)
    if o is None:
        raise HTTPException(status_code=404, detail="Séquence inconnue")
    return OutreachOut.from_outreach(o, with_messages=True)


class OutcomeIn(BaseModel):
    outcome: Literal["replied", "yes", "objection", "opted_out", "no", "stopped"]
    note: str | None = None


@app.post("/outreach/{outreach_id}/outcome", dependencies=[AuthDep])
def post_outcome(outreach_id: int, body: OutcomeIn, session: SessionDep) -> OutreachOut:
    from app.outreach.sequence import record_outcome

    o = session.get(Outreach, outreach_id)
    if o is None:
        raise HTTPException(status_code=404, detail="Séquence inconnue")
    record_outcome(session, o, body.outcome, note=body.note, source="api")
    session.flush()
    return OutreachOut.from_outreach(o)


@app.get("/mailboxes", dependencies=[AuthDep])
def list_mailboxes(session: SessionDep) -> list[dict]:
    from app.outreach.mailboxes import mailbox_health

    return [
        mailbox_health(session, mb) for mb in session.scalars(select(Mailbox).order_by(Mailbox.id))
    ]


@app.post("/mailboxes/sync", dependencies=[AuthDep])
def sync_mailboxes_endpoint(session: SessionDep) -> dict:
    from app.outreach.mailboxes import load_mailboxes_file, sync_mailboxes

    entries = load_mailboxes_file(get_settings().mailboxes_file)
    return {"created": sync_mailboxes(session, entries), "total": len(entries)}


@app.post("/mailboxes/{mailbox_id}/resume", dependencies=[AuthDep])
def resume_mailbox(mailbox_id: int, session: SessionDep) -> dict:
    mb = session.get(Mailbox, mailbox_id)
    if mb is None:
        raise HTTPException(status_code=404, detail="Boîte inconnue")
    mb.active = 1
    mb.paused_reason = None
    return {"id": mb.id, "active": True}


# --- C3 : page publique (lien SMS), DM manuels ----------------------------------------------


@app.get("/p/{token}", response_class=HTMLResponse)
def public_examples(token: str, session: SessionDep) -> str:
    """Page minimale (sans jeton) : les réponses rédigées pour un prospect, liée depuis le SMS."""
    import html

    o = session.scalar(select(Outreach).where(Outreach.token == token))
    if o is None:
        raise HTTPException(status_code=404, detail="Lien inconnu")
    name = html.escape(o.prospect.name)
    blocks = []
    for e in o.examples or []:
        stars = "★" * int(e.get("rating") or 0)
        blocks.append(
            f"<section><p class=r>{stars} — {html.escape(e.get('author') or 'un client')} : "
            f"« {html.escape(e.get('text') or '')} »</p>"
            f"<p class=a>{html.escape(e.get('reply') or '').replace(chr(10), '<br>')}</p></section>"
        )
    body = "".join(blocks) or "<p>Réponses en préparation.</p>"
    head = (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        f"<title>Répondu — {name}</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:640px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.5}.r{color:#555}"
        ".a{background:#f4f4f4;padding:.8rem 1rem;border-radius:8px}</style></head>"
    )
    return (
        f"{head}<body><h1>Réponses rédigées pour {name}</h1>"
        "<p>Deux réponses prêtes à publier sur votre fiche Google. "
        "Essai gratuit 30 jours : répondez « OK » au SMS.</p>"
        f"{body}<p><small>Répondu — vous ne souhaitez plus être contacté : "
        "répondez STOP.</small></p></body></html>"
    )


class DmBatchRequest(BaseModel):
    chat_id: str | None = None
    limit: int | None = None


@app.post("/outreach/dm-batch", dependencies=[AuthDep])
def post_dm_batch(body: DmBatchRequest, session: SessionDep) -> dict:
    from app.outreach.channels import deliver_dm_batch
    from app.telegram.client import get_telegram

    settings = get_settings()
    chat_id = body.chat_id or settings.telegram_chat_id
    if not chat_id:
        raise HTTPException(status_code=422, detail="chat_id requis (ou TELEGRAM_CHAT_ID)")
    batch = deliver_dm_batch(
        session, get_telegram(), chat_id, body.limit or settings.outreach_dm_batch
    )
    return {"delivered": [m.id for m in batch]}


@app.post("/outreach/messages/{message_id}/sent", dependencies=[AuthDep])
def post_manual_sent(message_id: int, session: SessionDep) -> dict:
    from app.outreach.channels import mark_manual_sent

    msg = mark_manual_sent(session, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Message introuvable ou déjà traité")
    return {"id": msg.id, "status": msg.status}


# --- C5 : webhooks fournisseurs ----------------------------------------------------------------


@app.post("/webhooks/resend")
async def webhook_resend(request: Request) -> dict:
    from app.outreach.email_providers import parse_resend_event
    from app.outreach.events import apply_provider_event, verify_svix_signature

    body = await request.body()
    secret = get_settings().resend_webhook_secret
    if secret and not verify_svix_signature(secret, dict(request.headers), body):
        raise HTTPException(status_code=401, detail="Signature invalide")
    import json

    try:
        payload = json.loads(body or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON invalide") from exc
    event = parse_resend_event(payload)
    if event is None or event.type == "other":
        return {"ok": True, "ignored": True}
    from app.db import session_scope

    with session_scope() as session:
        return {"ok": True, **apply_provider_event(session, event, source="resend")}


@app.post("/webhooks/brevo")
async def webhook_brevo(request: Request, token: str | None = None) -> dict:
    from app.outreach.email_providers import parse_brevo_event
    from app.outreach.events import apply_provider_event

    expected = get_settings().brevo_webhook_token
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="Jeton invalide")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON invalide") from exc
    events = payload if isinstance(payload, list) else [payload]
    from app.db import session_scope

    results = []
    with session_scope() as session:
        for item in events:
            event = parse_brevo_event(item)
            if event is None or event.type == "other":
                results.append({"ignored": True})
            else:
                results.append(apply_provider_event(session, event, source="brevo"))
    return {"ok": True, "results": results}


# --- Phase D : onboarding, service, publication ---------------------------------------------


@app.post("/outreach/{outreach_id}/convert", dependencies=[AuthDep], status_code=201)
def convert_outreach_endpoint(outreach_id: int, session: SessionDep) -> EstablishmentOut:
    from app.service.onboarding import convert_outreach, send_invitation

    o = session.get(Outreach, outreach_id)
    if o is None:
        raise HTTPException(status_code=404, detail="Séquence inconnue")
    est = convert_outreach(session, o)
    if est.invited_at is None:
        send_invitation(session, est)
    return EstablishmentOut.model_validate(est)


@app.post("/establishments/{establishment_id}/invite", dependencies=[AuthDep])
def invite_endpoint(establishment_id: int, session: SessionDep) -> EstablishmentOut:
    from app.service.onboarding import send_invitation

    est = session.get(Establishment, establishment_id)
    if est is None:
        raise HTTPException(status_code=404, detail="Établissement inconnu")
    send_invitation(session, est)
    return EstablishmentOut.model_validate(est)


@app.post("/establishments/{establishment_id}/manager-added", dependencies=[AuthDep])
def manager_added_endpoint(establishment_id: int, session: SessionDep) -> EstablishmentOut:
    from app.service.onboarding import manager_added

    est = session.get(Establishment, establishment_id)
    if est is None:
        raise HTTPException(status_code=404, detail="Établissement inconnu")
    manager_added(session, est)
    return EstablishmentOut.model_validate(est)


@app.post("/service/run", dependencies=[AuthDep], status_code=202)
def service_run(force: bool = False) -> JobState:
    """Rafraîchit les avis des clients dus, rédige et notifie (job en arrière-plan)."""
    from app.db import session_scope
    from app.service.loop import run_service_cycle

    with _job_lock:
        if _running.is_set():
            raise HTTPException(status_code=409, detail="Un job est déjà en cours")
        _running.set()
        job = JobState(id=uuid.uuid4().hex[:12], mode="service")
        _jobs[job.id] = job

    def work():
        with session_scope() as s:
            return run_service_cycle(s, force=force)

    _run_in_thread(job, work)
    return job


@app.get("/replies/to-publish", dependencies=[AuthDep])
def replies_to_publish(session: SessionDep, establishment_id: int | None = None) -> list[ReplyOut]:
    from app.service.loop import to_publish

    return [ReplyOut.from_reply(r) for r in to_publish(session, establishment_id)]


@app.post("/replies/{reply_id}/published", dependencies=[AuthDep])
def reply_published(reply_id: int, session: SessionDep, by: str = "api") -> ReplyOut:
    from app.service.loop import mark_published

    reply = session.get(Reply, reply_id)
    if reply is None:
        raise HTTPException(status_code=404, detail="Brouillon inconnu")
    try:
        mark_published(session, reply, by=by)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ReplyOut.from_reply(reply)


@app.post("/reports/weekly", dependencies=[AuthDep])
def weekly_reports(session: SessionDep, force: bool = False) -> dict:
    from app.service.report import send_weekly_reports

    return {"sent": [e.id for e in send_weekly_reports(session, force=force)]}


@app.get("/establishments/{establishment_id}/messages", dependencies=[AuthDep])
def establishment_messages(establishment_id: int, session: SessionDep) -> list[dict]:
    from app.models import ClientMessage

    rows = session.scalars(
        select(ClientMessage)
        .where(ClientMessage.establishment_id == establishment_id)
        .order_by(ClientMessage.id)
    )
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "channel": m.channel,
            "to": m.to,
            "subject": m.subject,
            "sent_at": m.sent_at,
            "error": m.error,
            "reply_id": m.reply_id,
        }
        for m in rows
    ]


# --- Phase E : fin d'essai et bilan --------------------------------------------------------------


class FeedbackIn(BaseModel):
    would_continue: bool | None = None
    price_willing: float | None = None
    missing: str | None = None
    raw: str | None = None


@app.post("/establishments/{establishment_id}/feedback", dependencies=[AuthDep])
def post_feedback(establishment_id: int, body: FeedbackIn, session: SessionDep) -> dict:
    from app.service.survey import record_feedback

    est = session.get(Establishment, establishment_id)
    if est is None:
        raise HTTPException(status_code=404, detail="Établissement inconnu")
    answers = {
        "would_continue": None if body.would_continue is None else int(body.would_continue),
        "price_willing": body.price_willing,
        "missing": body.missing,
    }
    fb = record_feedback(session, est, answers, body.raw, "api")
    return {
        "establishment_id": est.id,
        "would_continue": fb.would_continue,
        "price_willing": fb.price_willing,
        "missing": fb.missing,
        "source": fb.source,
    }


@app.post("/surveys/send", dependencies=[AuthDep])
def send_surveys(session: SessionDep, force: bool = False) -> dict:
    from app.service.survey import send_end_of_trial_surveys

    return {"sent": [e.id for e in send_end_of_trial_surveys(session, force=force)]}


@app.get("/bilan", dependencies=[AuthDep])
def get_bilan(session: SessionDep) -> dict:
    from app.service.survey import bilan

    return bilan(session)


@app.get("/doctor", dependencies=[AuthDep])
def doctor_endpoint() -> dict:
    """État des secrets et dépendances (jamais les valeurs). Pour OpenClaw et Telegram."""
    from app.doctor import run_doctor

    return run_doctor()
