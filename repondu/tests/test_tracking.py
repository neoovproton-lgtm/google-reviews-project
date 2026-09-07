"""C4 — Réponses IMAP (OK / STOP / autre), saisie manuelle, entonnoir, /stats Telegram."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.models import (
    Mailbox,
    MessageStatus,
    OptOut,
    Outreach,
    OutreachMessage,
    OutreachStatus,
    Prospect,
    ProspectStatus,
    utcnow,
)
from app.outreach import inbox
from app.outreach.stats import format_funnel, funnel
from app.telegram.bot import handle_update
from tests.fakes import FakeTelegram


def _raw(from_addr, body, subject="Re: 14 avis sans réponse", msg_id="<m1@x.fr>", html=False):
    ctype = "text/html" if html else "text/plain"
    return (
        f"From: Gérant <{from_addr}>\r\nTo: neo@repondu.fr\r\nSubject: {subject}\r\n"
        f"Message-ID: {msg_id}\r\nIn-Reply-To: <orig@repondu.fr>\r\nDate: Mon, 07 Sep 2026 10:00:00 +0200\r\n"
        f"Content-Type: {ctype}; charset=utf-8\r\n\r\n{body}"
    ).encode()


def _seed(s, n=3):
    mb = Mailbox(address="neo@repondu.fr", provider="log")
    s.add(mb)
    s.flush()
    ids = []
    for i in range(n):
        p = Prospect(
            place_id=f"p{i}", name=f"Resto {i}", status=ProspectStatus.ENRICHED, email=f"g{i}@x.fr"
        )
        s.add(p)
        s.flush()
        o = Outreach(
            prospect_id=p.id,
            channel="email",
            contact=p.email,
            step=1,
            token=f"t{i}",
            next_action_at=utcnow() + timedelta(days=3),
        )
        s.add(o)
        s.flush()
        s.add(
            OutreachMessage(
                outreach_id=o.id,
                step=1,
                channel="email",
                body="x",
                status=MessageStatus.DELIVERED,
                sent_at=utcnow(),
                delivered_at=utcnow(),
                mailbox_id=mb.id,
                provider_message_id=f"msg-{i}",
            )
        )
        ids.append(o.id)
    s.flush()
    return ids


def test_parse_rfc822_and_strip_quotes():
    raw = _raw(
        "g0@x.fr",
        "OK pour moi !\r\n\r\nLe lun. 7 sept. 2026 à 10:00, Neo <neo@repondu.fr> a écrit :\r\n> Bonjour,\r\n> 14 avis",
    )
    m = inbox.parse_rfc822(raw, uid="7")
    assert m.from_address == "g0@x.fr" and m.subject.startswith("Re:") and m.uid == "7"
    assert m.message_id == "<m1@x.fr>" and m.date.hour == 8  # UTC
    assert inbox.strip_quotes(m.body) == "OK pour moi !"
    html = inbox.parse_rfc822(_raw("g0@x.fr", "<p>Oui, allons-y</p><p>&nbsp;</p>", html=True))
    assert "allons-y" in html.body


def test_classify_reply():
    assert inbox.classify_reply("OK") == "yes"
    assert inbox.classify_reply("Ok, on essaie.\n\n> ancien mail") == "yes"
    assert inbox.classify_reply("Oui pourquoi pas, appelez-moi mardi.") == "yes"
    assert inbox.classify_reply("STOP") == "opted_out"
    assert inbox.classify_reply("Merci de me désinscrire de vos envois.") == "opted_out"
    assert inbox.classify_reply("Bonjour, combien ça coûte exactement ?") == "replied"
    assert inbox.classify_reply("") == "replied"
    assert inbox.classify_reply("Le lun. 7 sept. Neo a écrit :\n> OK ?") == "replied"  # tout cité


def test_process_inbound_outcomes(db):
    tg = FakeTelegram()
    with db() as s:
        o1, o2, o3 = _seed(s)
        assert (
            inbox.process_inbound(
                s, inbox.parse_rfc822(_raw("g0@x.fr", "OK", msg_id="<a>")), tg, "7"
            )
            == "yes"
        )
        assert (
            s.get(Outreach, o1).status == OutreachStatus.YES
            and s.get(Outreach, o1).outcome_note == "OK"
        )
        assert "🎉 OUI" in tg.texts[-1] and "Resto 0" in tg.texts[-1]
        assert (
            inbox.process_inbound(
                s, inbox.parse_rfc822(_raw("G1@x.fr", "STOP", msg_id="<b>")), tg, "7"
            )
            == "opted_out"
        )
        assert (
            s.get(Outreach, o2).status == OutreachStatus.OPTED_OUT
            and s.query(OptOut).filter_by(contact="g1@x.fr").one()
        )
        assert (
            inbox.process_inbound(
                s,
                inbox.parse_rfc822(_raw("g2@x.fr", "C'est quoi le prix ?", msg_id="<c>")),
                tg,
                "7",
            )
            == "replied"
        )
        assert s.get(Outreach, o3).status == OutreachStatus.REPLIED and f"/oui {o3}" in tg.texts[-1]
        # Doublon et expéditeur inconnu
        assert (
            inbox.process_inbound(
                s,
                inbox.parse_rfc822(_raw("g2@x.fr", "C'est quoi le prix ?", msg_id="<c>")),
                tg,
                "7",
            )
            == "duplicate"
        )
        assert (
            inbox.process_inbound(
                s, inbox.parse_rfc822(_raw("inconnu@x.fr", "Bonjour", msg_id="<d>")), tg, "7"
            )
            == "unknown"
        )
        assert len(tg.messages) == 3


def test_poll_inboxes_with_fake_fetcher(db):
    tg = FakeTelegram()
    entries = [
        {"address": "neo@repondu.fr", "imap": {"host": "h", "user": "u", "password": "p"}},
        {"address": "sans-imap@repondu.fr"},
        {"address": "panne@repondu.fr", "imap": {"host": "h", "user": "u", "password": "p"}},
    ]

    def fetcher(cfg):
        if cfg["user"] == "u" and fetcher.calls == 0:
            fetcher.calls += 1
            return [
                ("1", _raw("g0@x.fr", "ok", msg_id="<a>")),
                ("2", _raw("nobody@x.fr", "?", msg_id="<b>")),
            ]
        raise ConnectionError("imap down")

    fetcher.calls = 0
    with db() as s:
        _seed(s)
        counts = inbox.poll_inboxes(s, entries, tg, "7", fetcher=fetcher)
        assert counts["boxes"] == 2 and counts["messages"] == 2 and counts["yes"] == 1
        assert counts["unknown"] == 1 and counts["errors"] == 1
        assert s.query(Mailbox).filter_by(address="neo@repondu.fr").one().last_inbox_check_at


def test_funnel_and_telegram_stats(db):
    tg = FakeTelegram()
    with db() as s:
        o1, o2, o3 = _seed(s)
        from app.outreach.sequence import record_outcome

        record_outcome(s, s.get(Outreach, o1), "yes", now=utcnow())
        record_outcome(s, s.get(Outreach, o2), "objection", note="trop cher", now=utcnow())
        m = s.query(OutreachMessage).filter_by(outreach_id=o3).one()
        m.opened_at = utcnow()
        s.add(
            OutreachMessage(
                outreach_id=o3,
                step=2,
                channel="email",
                body="y",
                status=MessageStatus.SENT,
                sent_at=utcnow(),
                mailbox_id=1,
            )
        )
        s.flush()
        f = funnel(s)
        t = f["total"]
        assert t["enrolled"] == 3 and t["sent_step1"] == 3 and t["sent_step2"] == 1
        assert t["delivered"] == 3 and t["opened"] == 1 and t["yes"] == 1 and t["objection"] == 1
        assert t["rates"]["reply_of_contacted"] == round(2 / 3, 3) and t["rates"][
            "yes_of_contacted"
        ] == round(1 / 3, 3)
        assert t["rates"]["delivered_of_sent"] == 0.75 and f["by_channel"]["sms"]["enrolled"] == 0
        text = format_funnel(f)
        assert (
            "Contactés J0 3" in text
            and "OUI 1 (33 %)" in text
            and "email : 3 contactés, 2 réponses, 1 oui" in text
        )

        handle_update({"message": {"chat": {"id": 7}, "text": "/stats"}}, s, tg)
        assert (
            "📊 Entonnoir" in tg.texts[-1]
            and "📮 Boîtes" in tg.texts[-1]
            and "neo@repondu.fr" in tg.texts[-1]
        )
        handle_update({"message": {"chat": {"id": 7}, "text": f"/oui {o3}"}}, s, tg)
        assert s.get(Outreach, o3).status == OutreachStatus.YES and "→ yes" in tg.texts[-1]
        handle_update({"message": {"chat": {"id": 7}, "text": "/objection"}}, s, tg)
        assert tg.texts[-1] == "Usage : /objection <séquence> [note]"
        handle_update({"message": {"chat": {"id": 7}, "text": "/non 999"}}, s, tg)
        assert "introuvable" in tg.texts[-1]

    from app.main import app

    with TestClient(app) as c:
        stats = c.get("/stats").json()
        assert (
            stats["outreach"]["total"]["yes"] == 2
            and stats["mailboxes"][0]["address"] == "neo@repondu.fr"
        )


def test_scheduler_tick_is_resilient(db, monkeypatch, tmp_path):
    from app.config import reset_settings

    monkeypatch.setenv("MAILBOXES_FILE", str(tmp_path / "absent.json"))
    reset_settings()
    from app.scheduler import tick

    r = tick()
    assert r["inbox"]["boxes"] == 0 and r["outreach"]["enrolled"] == 0 and r["auto_approve"] == []
