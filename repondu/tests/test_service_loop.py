"""D2/D3/D4 — Notifications Google, rafraîchissement, boucle avis → réponse → veto → publication,
rapport hebdomadaire, boîtes du service, scheduler."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.config import reset_settings
from app.models import (
    ClientMessage,
    Establishment,
    OnboardingStatus,
    Prospect,
    ProspectStatus,
    Reply,
    ReplyStatus,
    Review,
    utcnow,
)
from app.outreach.email_providers import reset_providers, set_provider
from app.outreach.inbox import parse_rfc822
from app.outreach.sms import set_sms
from app.service import loop, notifications, report
from app.service.inboxes import poll_manager_inbox, poll_service_inbox
from app.telegram.bot import handle_update
from tests.fakes import FakeEmailProvider, FakeLLM, FakeTelegram

NOW = datetime(2026, 9, 7, 8, 0)  # lundi
REPLY_5 = {
    "reply": "Merci Marie, ravi que le risotto aux cèpes vous ait plu, à bientôt !",
    "detail_reused": "risotto",
}
REPLY_1 = {
    "reply": "Une heure d'attente sans un mot de notre part, nous le comprenons. Écrivez-nous à gerant@chezmarcel.fr pour en parler.",
    "detail_reused": "une heure d'attente",
}


def _client(s, chat_id=None, email="gerant@chezmarcel.fr", mobile=None, reviews=()):
    p = Prospect(
        place_id="p1",
        name="Chez Marcel",
        status=ProspectStatus.ENRICHED,
        email=email,
        response_rate=0.1,
        rating=4.0,
        maps_url="x",
    )
    s.add(p)
    s.flush()
    est = Establishment(
        name="Chez Marcel",
        prospect_id=p.id,
        contact_email=email,
        mobile_phone=mobile,
        telegram_chat_id=chat_id,
        onboarding_status=OnboardingStatus.MANAGER_ADDED,
        trial_started_at=NOW - timedelta(days=2),
        baseline_response_rate=0.1,
        baseline_rating=4.0,
        signature="Marc",
    )
    s.add(est)
    for rid, rating, text, author, days in reviews:
        s.add(
            Review(
                prospect_id=p.id,
                review_id=rid,
                rating=rating,
                text=text,
                author=author,
                date=NOW - timedelta(days=days),
            )
        )
    s.flush()
    return est


def _fake_scraper(new_reviews):
    """Simule scrape_prospect : insère les avis donnés (idempotent par review_id)."""

    def scraper(session, prospect_id):
        known = set(session.scalars(__import__("sqlalchemy").select(Review.review_id)))
        for rid, rating, text, author, days in new_reviews:
            if rid not in known:
                session.add(
                    Review(
                        prospect_id=prospect_id,
                        review_id=rid,
                        rating=rating,
                        text=text,
                        author=author,
                        date=NOW - timedelta(days=days),
                    )
                )
        session.flush()

    return scraper


def _google_mail(subject, body, sender="businessprofile-noreply@google.com", msg_id="<g1>"):
    return (
        f"From: Google Business Profile <{sender}>\r\nTo: gestion@repondu.fr\r\nSubject: {subject}\r\n"
        f"Message-ID: {msg_id}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    ).encode()


def test_parse_google_notification():
    mail = parse_rfc822(
        _google_mail(
            "Nouvel avis sur Chez Marcel",
            "Marie D. a laissé un avis 5 étoiles :\n« Le risotto aux cèpes était parfait »\nRépondre à l'avis",
        )
    )
    n = notifications.parse_notification(mail)
    assert n.establishment_name == "Chez Marcel" and n.author == "Marie D." and n.rating == 5
    assert n.snippet == "Le risotto aux cèpes était parfait"
    en = parse_rfc822(
        _google_mail("New review for Le Spot", 'John S. left a review ★★☆☆☆ "Cold food"')
    )
    n = notifications.parse_notification(en)
    assert n.establishment_name == "Le Spot" and n.rating == 2 and n.author == "John S."
    assert (
        notifications.parse_notification(
            parse_rfc822(_google_mail("Bonjour", "spam", sender="x@y.fr"))
        )
        is None
    )
    assert (
        notifications.parse_notification(parse_rfc822(_google_mail("Votre facture", "…"))) is None
    )


def test_match_establishment(db):
    with db() as s:
        _client(s)
        s.add(Establishment(name="Le Spot Lyon", active=1))
        s.flush()
        assert notifications.match_establishment(s, "chez marcel").name == "Chez Marcel"
        assert notifications.match_establishment(s, "Le Spot").name == "Le Spot Lyon"
        assert notifications.match_establishment(s, "Inconnu") is None
        assert notifications.match_establishment(s, None) is None


def test_refresh_detects_new_reviews_and_loop_notifies_by_email_and_sms(db):
    provider = FakeEmailProvider()
    set_provider("log", provider)
    from app.outreach.sms import LogSms

    sms = LogSms()
    set_sms(sms)
    try:
        with db() as s:
            est = _client(s, mobile="+33612345678", reviews=[("old", 4, "Bien", "Paul", 20)])
            s.query(Review).one().has_owner_response = 1
            scraper = _fake_scraper([("new1", 5, "Le risotto aux cèpes était parfait", "Marie", 0)])
            new = loop.refresh_reviews(s, est, now=NOW, scraper=scraper)
            assert [r.review_id for r in new] == ["new1"]
            est = s.get(Establishment, est.id)
            assert est.last_review_check_at == NOW
            drafts = loop.process_establishment(s, est, llm=FakeLLM([REPLY_5]), now=NOW)
            assert (
                len(drafts) == 1
                and drafts[0].status == ReplyStatus.PENDING
                and not drafts[0].needs_human
            )
            mail = provider.sent[0]
            assert (
                mail.subject.startswith("[Répondu #1]")
                and "publiée dans 24 h" in mail.text.lower()
                or "publié" in mail.text.lower()
            )
            assert "Merci Marie" in mail.text and "répondez « VETO »" in mail.text
            assert sms.sent[0][0] == "+33612345678" and "5★" in sms.sent[0][1]
            kinds = [(m.kind, m.channel) for m in s.query(ClientMessage).order_by(ClientMessage.id)]
            assert kinds == [("draft", "email"), ("draft", "sms")]
            # Deuxième passage : rien de nouveau, pas de doublon
            assert loop.refresh_reviews(s, est, now=NOW, scraper=scraper) == []
            assert (
                loop.process_establishment(s, s.get(Establishment, est.id), llm=FakeLLM(), now=NOW)
                == []
            )
    finally:
        reset_providers()
        set_sms(None)


def test_loop_notifies_by_telegram_with_buttons(db):
    tg = FakeTelegram()
    with db() as s:
        est = _client(
            s, chat_id="42", reviews=[("r1", 1, "Attente d'une heure pour le plat", "Bruno", 0)]
        )
        drafts = loop.process_establishment(
            s, est, llm=FakeLLM([REPLY_1, {"ok": True}]), telegram=tg, now=NOW
        )
        assert len(drafts) == 1 and tg.messages[0]["chat_id"] == "42"
        assert tg.messages[0]["buttons"][0][1] == ("❌ Veto", f"reject:{drafts[0].id}")
        assert s.query(ClientMessage).one().channel == "telegram"


def test_client_reply_by_mail_approves_or_vetoes(db):
    with db() as s:
        est = _client(
            s, reviews=[("r1", 5, "Risotto top", "Marie", 0), ("r2", 5, "Super risotto", "Léa", 0)]
        )
        r1, r2 = loop.process_establishment(s, est, llm=FakeLLM([REPLY_5, REPLY_5]), now=NOW)
        ok = parse_rfc822(
            b"From: gerant@chezmarcel.fr\r\nSubject: Re: [R\xc3\xa9pondu #1] Nouvel avis\r\nMessage-ID: <c1>\r\n\r\nOK merci !\r\n\r\n> ancien".replace(
                b"#1", f"#{r1.id}".encode()
            )
        )
        assert (
            loop.process_client_reply(s, ok, now=NOW) == "approved"
            and r1.status == ReplyStatus.APPROVED
        )
        assert r1.decision_by == "mail:gerant@chezmarcel.fr"
        veto = parse_rfc822(
            f"From: gerant@chezmarcel.fr\r\nSubject: Re: [Repondu #{r2.id}] x\r\nMessage-ID: <c2>\r\n\r\nNon, veto sur celle-ci.".encode()
        )
        assert (
            loop.process_client_reply(s, veto, now=NOW) == "rejected"
            and r2.status == ReplyStatus.REJECTED
        )
        assert loop.process_client_reply(s, veto, now=NOW) == "duplicate"
        other = parse_rfc822(b"From: x@y.fr\r\nSubject: Bonjour\r\nMessage-ID: <c3>\r\n\r\nSalut")
        assert loop.process_client_reply(s, other, now=NOW) == "unknown"
        again = parse_rfc822(
            f"From: gerant@chezmarcel.fr\r\nSubject: Re: [Répondu #{r1.id}] x\r\nMessage-ID: <c4>\r\n\r\nVETO".encode()
        )
        assert loop.process_client_reply(s, again, now=NOW) == "ignored"  # déjà approuvée


def test_publication_log_and_stats(db):
    tg = FakeTelegram()
    with db() as s:
        est = _client(s, reviews=[("r1", 5, "Risotto top", "Marie", 0)])
        (reply,) = loop.process_establishment(s, est, llm=FakeLLM([REPLY_5]), now=NOW)
        assert loop.to_publish(s) == []
        handle_update({"message": {"chat": {"id": 7}, "text": "/apublier"}}, s, tg)
        assert tg.texts[-1] == "Rien à publier."
        from app.replies.service import decide

        decide(s, reply, "approve", by="neo")
        handle_update({"message": {"chat": {"id": 7}, "text": "/apublier"}}, s, tg)
        assert f"À publier #{reply.id}" in tg.texts[-1] and f"/publie {reply.id}" in tg.texts[-1]
        s.query(Review).one().date = utcnow() - timedelta(hours=2)  # délai mesuré en temps réel
        handle_update({"message": {"chat": {"id": 7}, "text": f"/publie {reply.id}"}}, s, tg)
        assert tg.texts[-1] == f"Réponse #{reply.id} publiée ✅"
        assert reply.status == ReplyStatus.PUBLISHED and reply.published_at is not None
        review = s.query(Review).one()
        assert review.has_owner_response == 1 and review.owner_response_text == reply.text
        handle_update({"message": {"chat": {"id": 7}, "text": f"/publie {reply.id}"}}, s, tg)
        assert "non approuvé" in tg.texts[-1]
        stats = loop.service_stats(s)
        assert (
            stats["published"] == 1
            and stats["pct_under_24h"] == 1.0
            and stats["clients_active"] == 1
        )
        assert stats["median_delay_h"] is not None and stats["to_publish"] == 0

    from app.main import app

    with TestClient(app) as c:
        assert c.get("/stats").json()["service"]["published"] == 1
        assert c.get("/replies/to-publish").json() == []
        assert c.post("/replies/1/published").status_code == 409
        assert c.post("/replies/999/published").status_code == 404


def test_weekly_report_content_and_monday_rule(db):
    provider = FakeEmailProvider()
    set_provider("log", provider)
    try:
        with db() as s:
            est = _client(
                s,
                reviews=[
                    ("r1", 5, "Top", "Marie", 1),
                    ("r2", 3, "Bof", "Léa", 3),
                    ("r3", 4, "Ok", "Paul", 20),
                ],
            )
            r1 = s.query(Review).filter_by(review_id="r1").one()
            rep = Reply(
                review_id=r1.id,
                establishment_id=est.id,
                text="Merci",
                status=ReplyStatus.PUBLISHED,
                published_at=NOW - timedelta(hours=20),
            )
            r1.has_owner_response = 1
            s.add(rep)
            s.flush()
            data = report.compute_report(s, est, NOW)
            assert data["reviews_week"] == 2 and data["avg_rating_week"] == 4.0
            assert data["published_week"] == 1 and data["reviews_since_trial"] == 1
            assert data["response_rate_before"] == 0.1 and data["response_rate_after"] == 1.0
            subject, body = report.report_email(
                est, data, __import__("app.config").config.get_settings()
            )
            assert (
                "Chez Marcel" in subject
                and "Avis reçus : 2" in body
                and "10 % avant Répondu → 100 % depuis" in body
            )
            # Dimanche : rien ; lundi : un envoi ; lundi suivant après 6 jours : un autre ; deux fois le même lundi : non
            assert report.send_weekly_reports(s, now=NOW - timedelta(days=1)) == []
            assert report.send_weekly_reports(s, now=NOW) == [est]
            assert report.send_weekly_reports(s, now=NOW + timedelta(hours=3)) == []
            assert report.send_weekly_reports(s, now=NOW + timedelta(days=7)) == [est]
            assert len(provider.sent) == 2 and provider.sent[0].to == "gerant@chezmarcel.fr"
            assert [m.kind for m in s.query(ClientMessage)] == ["report", "report"]
            assert report.send_weekly_reports(s, now=NOW + timedelta(days=8), force=True) == [est]
    finally:
        reset_providers()


def test_service_inboxes_with_fake_fetchers(db, monkeypatch):
    monkeypatch.setenv("MANAGER_IMAP_HOST", "imap.x")
    monkeypatch.setenv("MANAGER_IMAP_USER", "gestion@repondu.fr")
    monkeypatch.setenv("MANAGER_IMAP_PASSWORD", "p")
    monkeypatch.setenv("SERVICE_IMAP_HOST", "imap.x")
    monkeypatch.setenv("SERVICE_IMAP_USER", "contact@repondu.fr")
    monkeypatch.setenv("SERVICE_IMAP_PASSWORD", "p")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "7")
    reset_settings()
    tg = FakeTelegram()
    with db() as s:
        est = _client(s)
        scraper = _fake_scraper([("g1", 5, "Le risotto aux cèpes était parfait", "Marie", 0)])
        notif = _google_mail(
            "Nouvel avis sur Chez Marcel", "Marie D. a laissé un avis 5 étoiles : « Le risotto »"
        )
        out = poll_manager_inbox(
            s,
            telegram=tg,
            fetcher=lambda cfg: [
                ("1", notif),
                ("2", _google_mail("Nouvel avis sur Inconnu", "x", msg_id="<g2>")),
            ],
            llm=FakeLLM([REPLY_5]),
            scraper=scraper,
            now=NOW,
        )
        assert out == {
            "configured": True,
            "messages": 2,
            "notifications": 2,
            "matched": 1,
            "new_reviews": 1,
            "drafts": 1,
        }
        reply = s.query(Reply).one()
        ok = (
            f"From: gerant@chezmarcel.fr\r\nSubject: Re: [Répondu #{reply.id}] Nouvel avis\r\nMessage-ID: <c1>\r\n\r\nOK"
        ).encode()
        out = poll_service_inbox(s, telegram=tg, fetcher=lambda cfg: [("1", ok)], now=NOW)
        assert out["approved"] == 1 and reply.status == ReplyStatus.APPROVED
        assert "approuvée ✅" in tg.texts[-1]
        assert est.id


def test_run_service_cycle_due_and_force(db, monkeypatch):
    with db() as s:
        est = _client(s, reviews=[])
        scraper = _fake_scraper([("n1", 5, "Le risotto aux cèpes était parfait", "Marie", 0)])
        out = loop.run_service_cycle(s, now=NOW, llm=FakeLLM([REPLY_5]), scraper=scraper)
        assert out == {"checked": 1, "new_reviews": 1, "drafts": 1, "errors": 0}
        # Vérifié il y a moins de 6 h : pas dû ; --force passe quand même
        assert (
            loop.run_service_cycle(s, now=NOW + timedelta(hours=1), llm=FakeLLM(), scraper=scraper)[
                "checked"
            ]
            == 0
        )
        assert (
            loop.run_service_cycle(
                s, now=NOW + timedelta(hours=1), llm=FakeLLM(), scraper=scraper, force=True
            )["checked"]
            == 1
        )
        assert (
            loop.run_service_cycle(s, now=NOW + timedelta(hours=7), llm=FakeLLM(), scraper=scraper)[
                "checked"
            ]
            == 1
        )
        assert est.id


def test_scheduler_service_tick_without_config(db):
    from app.scheduler import service_tick

    out = service_tick()
    assert (
        out["manager_inbox"]["configured"] is False and out["service_inbox"]["configured"] is False
    )
    assert out["reviews"]["checked"] == 0 and out["reminders"] == [] and out["reports"] == []
