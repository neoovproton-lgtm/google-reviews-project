"""E1 — Questionnaire de fin d'essai (3 questions) et bilan J+45."""

from __future__ import annotations

import re
from datetime import datetime
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Establishment, OnboardingStatus, TrialFeedback, utcnow
from app.outreach.inbox import InboundMail, strip_quotes
from app.replies.checks import normalize
from app.service.mailer import send_client_email

SURVEY_TAG_RE = re.compile(r"\[R\S{0,2}pondu bilan #(\d+)\]", re.I)
PRICE_RE = re.compile(r"(\d{1,4})(?:[,.](\d{1,2}))?\s*(?:€|euros?|eur)(?![a-z])", re.I)
YES_RE = re.compile(r"\b(oui|yes|continuer|on continue|je continue|volontiers|bien sur|ok)\b")
NO_RE = re.compile(r"\b(non|no|pas pour l'instant|on arrete|j'arrete|pas convaincu|stop)\b")


def survey_email(est: Establishment, settings: Settings) -> tuple[str, str]:
    subject = f"[Répondu bilan #{est.id}] Fin de l'essai {est.name} : 3 questions"
    body = f"""Bonjour,

Les 30 jours d'essai de Répondu pour {est.name} se terminent. Trois questions, une ligne
chacune, pour décider de la suite :

1. Continueriez-vous ? (oui / non)
2. À quel prix par mois trouveriez-vous cela juste ? (ex. 39 €)
3. Qu'est-ce qui manque ?

Répondez simplement à ce mail, dans l'ordre. Si vous préférez, un appel de 5 minutes :
dites-nous un créneau.

Merci d'avoir testé, et pour vos retours.

{settings.outreach_signature}
"""
    return subject, body


def send_end_of_trial_surveys(
    session: Session,
    now: datetime | None = None,
    settings: Settings | None = None,
    force: bool = False,
) -> list[Establishment]:
    """Clients dont l'essai est fini et qui n'ont pas encore reçu le questionnaire."""
    now = now or utcnow()
    settings = settings or get_settings()
    stmt = select(Establishment).where(
        Establishment.onboarding_status == OnboardingStatus.MANAGER_ADDED,
        Establishment.survey_sent_at.is_(None),
    )
    if not force:
        stmt = stmt.where(Establishment.trial_ends_at <= now)
    sent = []
    for est in session.scalars(stmt):
        subject, body = survey_email(est, settings)
        send_client_email(session, est, "survey", subject, body, settings=settings)
        est.survey_sent_at = now
        sent.append(est)
    return sent


def parse_answers(text: str) -> dict:
    """Texte libre → {would_continue, price_willing, missing}. Tolérant à l'ordre."""
    own = strip_quotes(text)
    norm = normalize(own)
    lines = [ln.strip(" -•*\t") for ln in own.splitlines() if ln.strip(" -•*\t")]
    would = None
    first = normalize(lines[0]) if lines else norm
    first = re.sub(r"^\s*1[.)]?\s*", "", first)
    if NO_RE.search(first) and not YES_RE.search(first[:12]):
        would = 0
    elif YES_RE.search(first):
        would = 1
    elif NO_RE.search(norm):
        would = 0
    elif YES_RE.search(norm):
        would = 1
    price = None
    m = PRICE_RE.search(own)
    if m:
        price = float(f"{m.group(1)}.{m.group(2) or 0}")
    missing = None
    if len(lines) >= 3:
        missing = re.sub(r"^\s*3[.)]?\s*", "", lines[2]).strip() or None
    elif lines:
        rest = [PRICE_RE.sub("", ln).strip(" ,.;:-") for ln in lines[1:]]
        rest = [ln for ln in rest if ln]
        missing = re.sub(r"^\s*3[.)]?\s*", "", rest[-1]).strip() if rest else None
    if missing and normalize(missing) in ("rien", "ras", "non", "nothing", "-"):
        missing = None
    return {"would_continue": would, "price_willing": price, "missing": missing}


def record_feedback(
    session: Session,
    est: Establishment,
    answers: dict,
    raw: str | None,
    source: str,
    now: datetime | None = None,
) -> TrialFeedback:
    now = now or utcnow()
    fb = session.scalar(select(TrialFeedback).where(TrialFeedback.establishment_id == est.id))
    if fb is None:
        fb = TrialFeedback(establishment_id=est.id)
        session.add(fb)
    for key in ("would_continue", "price_willing", "missing"):
        if answers.get(key) is not None:
            setattr(fb, key, answers[key])
    fb.raw = raw
    fb.source = source
    fb.collected_at = now
    est.onboarding_status = OnboardingStatus.ENDED
    session.flush()
    return fb


def process_survey_reply(session: Session, mail: InboundMail, now: datetime | None = None) -> str:
    """`recorded` | `unknown` selon la balise de l'objet."""
    m = SURVEY_TAG_RE.search(mail.subject or "")
    if not m:
        return "unknown"
    est = session.get(Establishment, int(m.group(1)))
    if est is None:
        return "unknown"
    record_feedback(session, est, parse_answers(mail.body), strip_quotes(mail.body), "mail", now)
    return "recorded"


def bilan(session: Session, now: datetime | None = None) -> dict:
    """Chiffres du bilan J+45 : essais, questionnaires, oui, prix, manques, décision."""
    from app.outreach.stats import funnel
    from app.service.loop import service_stats

    now = now or utcnow()
    trials = list(
        session.scalars(select(Establishment).where(Establishment.trial_started_at.is_not(None)))
    )
    feedbacks = list(session.scalars(select(TrialFeedback)))
    yes = [f for f in feedbacks if f.would_continue == 1]
    prices = [f.price_willing for f in feedbacks if f.price_willing]
    would_pay = [f for f in feedbacks if f.would_continue == 1 and f.price_willing]
    surveyed = session.scalar(
        select(func.count())
        .select_from(Establishment)
        .where(Establishment.survey_sent_at.is_not(None))
    )
    answered = len(feedbacks)
    pay_rate = round(len(would_pay) / answered, 3) if answered else None
    prospects_yes = funnel(session)["total"]["yes"]
    service = service_stats(session, now)
    if answered == 0:
        decision = "attendre les réponses au questionnaire"
    elif pay_rate is not None and pay_rate >= 0.5:
        decision = "structurer pour facturer (≥ 50 % paieraient)"
    elif yes:
        decision = "pivoter : des « oui » sans prix, creuser les manques"
    else:
        decision = "arrêter ou changer de verticale (aucun « je paierais »)"
    return {
        "trials_started": len(trials),
        "trials_ended": len([e for e in trials if e.onboarding_status == OnboardingStatus.ENDED]),
        "prospects_yes": prospects_yes,
        "surveys_sent": int(surveyed or 0),
        "surveys_answered": answered,
        "would_continue": len(yes),
        "would_pay": len(would_pay),
        "would_pay_rate": pay_rate,
        "price_median": round(median(prices), 2) if prices else None,
        "prices": sorted(prices),
        "missing": [f.missing for f in feedbacks if f.missing],
        "service": {
            "published": service["published"],
            "median_delay_h": service["median_delay_h"],
            "pct_under_24h": service["pct_under_24h"],
        },
        "decision_suggested": decision,
    }


def format_bilan(b: dict) -> str:
    pct = "—" if b["would_pay_rate"] is None else f"{b['would_pay_rate'] * 100:.0f} %"
    u = b["service"]["pct_under_24h"]
    under24 = "—" if u is None else f"{u * 100:.0f} %"
    lines = [
        "📋 Bilan Répondu (J+45)",
        f"Prospects ayant dit oui : {b['prospects_yes']} · essais démarrés : {b['trials_started']} "
        f"· terminés : {b['trials_ended']}",
        f"Questionnaires envoyés {b['surveys_sent']} · réponses {b['surveys_answered']}",
        f"Continueraient : {b['would_continue']} · « je paierais » : {b['would_pay']} "
        f"({pct}, cible ≥ 50 %)",
        f"Prix médian : {b['price_median'] or '—'} €/mois · réponses : {b['prices'] or '—'}",
        f"Réponses publiées : {b['service']['published']} · délai médian "
        f"{b['service']['median_delay_h'] or '—'} h · < 24 h : "
        f"{under24}",
    ]
    if b["missing"]:
        lines.append("Ce qui manque : " + " · ".join(b["missing"]))
    lines.append(f"Décision suggérée : {b['decision_suggested']}")
    lines.append("Options PRD : arrêter · pivoter verticale · structurer pour facturer")
    return "\n".join(lines)
