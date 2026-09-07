"""API HTTP pilotée par OpenClaw. L'appli exécute, OpenClaw orchestre."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session, init_db
from app.models import Establishment, Prospect, ProspectStatus, Reply, ReplyStatus, Review, utcnow

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
