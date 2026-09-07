"""E1 — Questionnaire de fin d'essai et bilan."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.models import (
    ClientMessage,
    Establishment,
    OnboardingStatus,
    Prospect,
    ProspectStatus,
    TrialFeedback,
)
from app.outreach.email_providers import reset_providers, set_provider
from app.outreach.inbox import parse_rfc822
from app.service import survey
from app.service.inboxes import poll_service_inbox
from app.telegram.bot import handle_update
from tests.fakes import FakeEmailProvider, FakeTelegram

NOW = datetime(2026, 10, 10, 8, 0)


def _client(s, name="Chez Marcel", ends_in_days=-1, email="g@x.fr"):
    p = Prospect(place_id=name, name=name, status=ProspectStatus.ENRICHED, email=email)
    s.add(p)
    s.flush()
    est = Establishment(
        name=name,
        prospect_id=p.id,
        contact_email=email,
        onboarding_status=OnboardingStatus.MANAGER_ADDED,
        trial_started_at=NOW - timedelta(days=30),
        trial_ends_at=NOW + timedelta(days=ends_in_days),
    )
    s.add(est)
    s.flush()
    return est


def test_parse_answers_variants():
    a = survey.parse_answers("1. Oui\n2. 39 €\n3. Un rapport plus détaillé\n\n> ancien mail")
    assert a == {"would_continue": 1, "price_willing": 39.0, "missing": "Un rapport plus détaillé"}
    a = survey.parse_answers(
        "Non, pas pour l'instant. Peut-être 19,50 euros. Il manque TripAdvisor."
    )
    assert a["would_continue"] == 0 and a["price_willing"] == 19.5
    a = survey.parse_answers("oui\n29€\nrien")
    assert a == {"would_continue": 1, "price_willing": 29.0, "missing": None}
    a = survey.parse_answers("Je ne sais pas encore.")
    assert a["would_continue"] is None and a["price_willing"] is None


def test_survey_sent_once_at_trial_end(db):
    provider = FakeEmailProvider()
    set_provider("log", provider)
    try:
        with db() as s:
            done = _client(s, "Fini", ends_in_days=-1)
            _client(s, "En cours", ends_in_days=5)
            assert [e.name for e in survey.send_end_of_trial_surveys(s, now=NOW)] == ["Fini"]
            assert survey.send_end_of_trial_surveys(s, now=NOW) == []
            assert done.survey_sent_at == NOW
            mail = provider.sent[0]
            assert (
                mail.subject.startswith(f"[Répondu bilan #{done.id}]")
                and "3. Qu'est-ce qui manque" in mail.text
            )
            assert [m.kind for m in s.query(ClientMessage)] == ["survey"]
            assert (
                len(survey.send_end_of_trial_surveys(s, now=NOW, force=True)) == 1
            )  # « En cours » forcé
    finally:
        reset_providers()


def test_survey_reply_by_mail_and_bilan(db, monkeypatch):
    from app.config import reset_settings

    monkeypatch.setenv("SERVICE_IMAP_HOST", "h")
    monkeypatch.setenv("SERVICE_IMAP_USER", "u")
    monkeypatch.setenv("SERVICE_IMAP_PASSWORD", "p")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    reset_settings()
    tg = FakeTelegram()
    with db() as s:
        a = _client(s, "A")
        b = _client(s, "B", email="b@x.fr")
        survey.send_end_of_trial_surveys(s, now=NOW)
        raw = (
            f"From: g@x.fr\r\nSubject: Re: [Répondu bilan #{a.id}] Fin de l'essai\r\nMessage-ID: <s1>\r\n\r\n"
            "1. Oui\r\n2. 39 €\r\n3. Les réponses sur TripAdvisor"
        ).encode()
        out = poll_service_inbox(s, telegram=tg, fetcher=lambda cfg: [("1", raw)], now=NOW)
        assert out["survey"] == 1 and "Questionnaire de fin d'essai reçu" in tg.texts[-1]
        fb = s.query(TrialFeedback).one()
        assert (
            fb.would_continue == 1
            and fb.price_willing == 39.0
            and fb.missing == "Les réponses sur TripAdvisor"
        )
        assert a.onboarding_status == OnboardingStatus.ENDED and fb.source == "mail"
        # Appel de 5 min saisi sur Telegram pour B
        handle_update(
            {
                "message": {
                    "chat": {"id": 7},
                    "text": f"/bilan {b.id} non 19 € trop cher pour une brasserie",
                }
            },
            s,
            tg,
        )
        assert "enregistré : continuerait=0, prix=19.0" in tg.texts[-1]
        bl = survey.bilan(s, now=NOW)
        assert bl["trials_started"] == 2 and bl["surveys_sent"] == 2 and bl["surveys_answered"] == 2
        assert bl["would_continue"] == 1 and bl["would_pay"] == 1 and bl["would_pay_rate"] == 0.5
        assert bl["price_median"] == 29.0 and bl["missing"] == [
            "Les réponses sur TripAdvisor",
            "trop cher pour une brasserie",
        ]
        assert bl["decision_suggested"].startswith("structurer pour facturer")
        text = survey.format_bilan(bl)
        assert "« je paierais » : 1 (50 %, cible ≥ 50 %)" in text and "TripAdvisor" in text
        handle_update({"message": {"chat": {"id": 7}, "text": "/bilan"}}, s, tg)
        assert tg.texts[-1].startswith("📋 Bilan Répondu")
        handle_update({"message": {"chat": {"id": 7}, "text": "/bilan 99 oui"}}, s, tg)
        assert tg.texts[-1].startswith("Usage : /bilan")
        assert (
            survey.process_survey_reply(
                s, parse_rfc822(b"From: x@y.fr\r\nSubject: hello\r\n\r\nhi")
            )
            == "unknown"
        )


def test_bilan_decisions_and_api(db):
    with db() as s:
        assert survey.bilan(s)["decision_suggested"].startswith("attendre")
        est = _client(s, "A")
        survey.record_feedback(
            s, est, {"would_continue": 0, "price_willing": None, "missing": None}, "non", "api"
        )
        assert survey.bilan(s)["decision_suggested"].startswith("arrêter")
        survey.record_feedback(
            s, est, {"would_continue": 1, "price_willing": None, "missing": "x"}, "oui", "api"
        )
        assert survey.bilan(s)["decision_suggested"].startswith("pivoter")
        eid = est.id
    from app.main import app

    with TestClient(app) as c:
        r = c.post(
            f"/establishments/{eid}/feedback",
            json={"would_continue": True, "price_willing": 39, "missing": "rien"},
        )
        assert r.json()["price_willing"] == 39.0 and r.json()["source"] == "api"
        assert c.post("/establishments/999/feedback", json={}).status_code == 404
        assert c.get("/bilan").json()["would_pay_rate"] == 1.0
        assert c.get("/stats").json()["bilan"]["surveys_answered"] == 1
        assert c.post("/surveys/send?force=true").json()["sent"] == []  # déjà terminé (ended)
