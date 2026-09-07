"""C3 — Formulaire (Playwright), DM via Telegram, SMS, un seul canal par prospect."""

from datetime import timedelta
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.models import MessageStatus, Outreach, OutreachStatus, Prospect, ProspectStatus, Review
from app.outreach import sequence as seq
from app.outreach.sms import set_sms
from app.telegram.bot import handle_update
from tests.conftest import _FixtureHandler
from tests.fakes import FakeLLM, FakeTelegram
from tests.test_outreach_sequence import MONDAY_10H_PARIS, REPLY, REPLY_NEG

DM = {
    "text": "Bonjour ! En regardant votre fiche Google, j'ai vu 14 avis sans réponse ces 30 jours, dont 3 négatifs. Répondu rédige et publie les réponses dans votre ton. Voici deux réponses écrites pour vos vrais avis. Essai gratuit 30 jours, répondez « OK »."
}


def _prospect(s, place_id, canal, **contact):
    p = Prospect(
        place_id=place_id,
        name="Chez Marcel",
        city="Lyon",
        status=ProspectStatus.ENRICHED,
        canal_prioritaire=canal,
        unanswered_last_30d=14,
        negative_unanswered_last_30d=3,
        score=10,
        email="gerant@chezmarcel.fr" if canal == "email" else None,
        **contact,
    )
    s.add(p)
    s.flush()
    s.add_all(
        [
            Review(
                prospect_id=p.id,
                review_id=f"{place_id}-a",
                rating=5,
                text="Risotto parfait",
                author="Marie",
                date=MONDAY_10H_PARIS,
            ),
            Review(
                prospect_id=p.id,
                review_id=f"{place_id}-b",
                rating=2,
                text="Risotto froid",
                author="Bruno",
                date=MONDAY_10H_PARIS - timedelta(days=1),
            ),
        ]
    )
    s.flush()
    return p


@pytest.mark.browser
def test_contact_form_is_filled_and_submitted(fixture_server):
    from app.outreach.forms import submit_contact_form

    result = submit_contact_form(
        f"{fixture_server}/site/contact_form.html",
        name="Neo",
        email="neo@repondu.fr",
        phone="+33612345678",
        subject="Vos avis",
        message="Bonjour, voici deux réponses.",
    )
    assert result.ok and result.verified, result.detail
    posted = parse_qs(_FixtureHandler.last_post)
    assert posted["votre_nom"] == ["Neo"] and posted["courriel"] == ["neo@repondu.fr"]
    assert posted["telephone"] == ["+33612345678"] and posted["objet"] == ["Vos avis"]
    assert posted["message"] == ["Bonjour, voici deux réponses."] and "rgpd" in posted


@pytest.mark.browser
def test_form_channel_end_to_end(db, fixture_server):
    with db() as s:
        _prospect(
            s, "f1", "formulaire", contact_form_url=f"{fixture_server}/site/contact_form.html"
        )
        llm = FakeLLM([REPLY, REPLY_NEG, {"ok": True}])
        r = seq.run_outreach(now=MONDAY_10H_PARIS, llm=llm, session=s)
        assert r.enrolled == 1 and r.sent == 1 and r.errors == 0
        o = s.query(Outreach).one()
        assert o.channel == "formulaire" and o.step == 1 and o.next_action_at is None
        assert o.messages[0].status == MessageStatus.SENT and "Avis de Marie" in o.messages[0].body
        assert "14 avis sans réponse" in parse_qs(_FixtureHandler.last_post)["message"][0]
        # Rien de plus au passage suivant, puis clôture après 10 jours sans réponse
        assert (
            seq.run_outreach(now=MONDAY_10H_PARIS + timedelta(days=1), llm=llm, session=s).sent == 0
        )
        r = seq.run_outreach(now=MONDAY_10H_PARIS + timedelta(days=11), llm=llm, session=s)
        assert r.expired == 1 and o.status == OutreachStatus.DONE


@pytest.mark.browser
def test_form_channel_failure_stops_sequence(db):
    with db() as s:
        _prospect(s, "f2", "formulaire", contact_form_url="http://127.0.0.1:9/contact")
        r = seq.run_outreach(
            now=MONDAY_10H_PARIS, llm=FakeLLM([REPLY, REPLY_NEG, {"ok": True}]), session=s
        )
        assert r.errors == 1
        o = s.query(Outreach).one()
        assert o.status == OutreachStatus.STOPPED and o.messages[0].status == MessageStatus.FAILED


def test_dm_channel_prepares_and_delivers_batch(db, monkeypatch):
    monkeypatch.setenv("OUTREACH_DM_BATCH", "1")
    reset_settings()
    tg = FakeTelegram()
    with db() as s:
        _prospect(s, "d1", "instagram", instagram="chezmarcel")
        _prospect(s, "d2", "facebook", facebook="ChezMarcelLyon")
        llm = FakeLLM([REPLY, REPLY_NEG, {"ok": True}, DM] * 2)
        r = seq.run_outreach(now=MONDAY_10H_PARIS, llm=llm, session=s)
        assert r.prepared_manual == 2 and r.sent == 0
        msgs = {m.outreach.channel: m for o in s.query(Outreach) for m in o.messages}
        assert msgs["instagram"].status == MessageStatus.MANUAL_PENDING
        assert "STOP" not in msgs["instagram"].body and "Avis de Marie" in msgs["instagram"].body
        # Lot de 1 (OUTREACH_DM_BATCH) via la commande Telegram
        handle_update({"message": {"chat": {"id": 7}, "text": "/dm"}}, s, tg)
        assert "instagram.com/chezmarcel" in tg.texts[0] and "/envoye" in tg.texts[0]
        assert tg.texts[1] == "1 DM livré(s). Marquez chacun avec /envoye <id>."
        handle_update({"message": {"chat": {"id": 7}, "text": "/dm"}}, s, tg)
        assert "facebook.com/ChezMarcelLyon" in tg.texts[2]
        handle_update({"message": {"chat": {"id": 7}, "text": "/dm"}}, s, tg)
        assert tg.texts[-1] == "Aucun DM en attente."
        mid = msgs["instagram"].id
        handle_update({"message": {"chat": {"id": 7}, "text": f"/envoye {mid}"}}, s, tg)
        assert tg.texts[-1] == f"DM #{mid} marqué envoyé ✅"
        assert msgs["instagram"].status == MessageStatus.MANUAL_SENT and msgs["instagram"].sent_at
        handle_update({"message": {"chat": {"id": 7}, "text": f"/envoye {mid}"}}, s, tg)
        assert "déjà traité" in tg.texts[-1]
        handle_update({"message": {"chat": {"id": 7}, "text": "/envoye"}}, s, tg)
        assert tg.texts[-1] == "Usage : /envoye <id>"


def test_sms_channel_and_public_page(db, monkeypatch):
    from app.outreach.sms import LogSms

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://r.example")
    reset_settings()
    sms = LogSms()
    set_sms(sms)
    try:
        with db() as s:
            _prospect(s, "s1", "sms", mobile_phone="+33612345678")
            r = seq.run_outreach(
                now=MONDAY_10H_PARIS, llm=FakeLLM([REPLY, REPLY_NEG, {"ok": True}]), session=s
            )
            assert r.sent == 1
            o = s.query(Outreach).one()
            to, text = sms.sent[0]
            assert (
                to == "+33612345678" and f"https://r.example/p/{o.token}" in text and "STOP" in text
            )
            assert o.messages[0].status == MessageStatus.SENT and o.step == 1
            token = o.token
        from app.main import app

        with TestClient(app) as c:
            page = c.get(f"/p/{token}")
            assert page.status_code == 200 and "Chez Marcel" in page.text and "Marie" in page.text
            assert "Risotto froid" in page.text and "noindex" in page.text
            assert c.get("/p/nope").status_code == 404
    finally:
        set_sms(None)


def test_one_channel_at_a_time(db):
    with db() as s:
        p = _prospect(s, "m1", "email", instagram="chezmarcel", mobile_phone="+33612345678")
        assert seq.enroll(s, MONDAY_10H_PARIS) == 1
        o = s.query(Outreach).one()
        assert o.channel == "email"
        p.canal_prioritaire = "instagram"  # même si le canal change, pas de 2e séquence
        assert seq.enroll(s, MONDAY_10H_PARIS) == 0


def test_dm_batch_api_and_manual_sent(db):
    from app.main import app
    from app.telegram.client import set_telegram

    tg = FakeTelegram()
    set_telegram(tg)
    try:
        with db() as s:
            _prospect(s, "d1", "instagram", instagram="chezmarcel")
            seq.run_outreach(
                now=MONDAY_10H_PARIS, llm=FakeLLM([REPLY, REPLY_NEG, {"ok": True}, DM]), session=s
            )
        with TestClient(app) as c:
            assert c.post("/outreach/dm-batch", json={}).status_code == 422  # pas de chat
            r = c.post("/outreach/dm-batch", json={"chat_id": "9"})
            assert r.json() == {"delivered": [1]} and tg.messages[0]["chat_id"] == "9"
            assert c.post("/outreach/messages/1/sent").json() == {"id": 1, "status": "manual_sent"}
            assert c.post("/outreach/messages/1/sent").status_code == 404
    finally:
        set_telegram(None)
