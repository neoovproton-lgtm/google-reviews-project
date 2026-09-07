"""API HTTP pilotée par OpenClaw. L'appli exécute, OpenClaw orchestre."""

from __future__ import annotations

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session, init_db
from app.models import Prospect, ProspectStatus, utcnow

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
