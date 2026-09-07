"""C2 — Séquence J0 / J+3 / J+8 : fenêtre, quotas, rotation, idempotence, arrêts."""

from datetime import datetime, timedelta

from app.config import reset_settings
from app.models import (
    Mailbox,
    MessageStatus,
    OptOut,
    Outreach,
    OutreachMessage,
    OutreachStatus,
    Prospect,
    ProspectStatus,
    Review,
)
from app.outreach import sequence as seq
from app.outreach.email_providers import reset_providers, set_provider
from app.outreach.mailboxes import effective_quota, pick_mailbox, remaining_today
from tests.fakes import FakeEmailProvider, FakeLLM

MONDAY_10H_PARIS = datetime(2026, 9, 7, 8, 0)  # lundi 10 h à Paris (UTC+2)
REPLY = {
    "reply": "Merci beaucoup pour ce retour sur le risotto, à très bientôt chez nous !",
    "detail_reused": "risotto",
}
REPLY_NEG = {
    "reply": "Un risotto froid, ce n'est pas ce que nous voulons servir. Écrivez-nous à gerant@chezmarcel.fr pour en parler.",
    "detail_reused": "risotto froid",
}
EMAIL = {
    "subject": "14 avis sans réponse chez Chez Marcel",
    "opening": "Sur votre fiche Google, j'ai compté 14 avis sans réponse ces 30 jours, dont 3 négatifs. Répondu rédige et publie les réponses dans votre ton, sous 24 h, avec votre veto. Voici deux exemples écrits pour vos vrais avis :",
    "closing": "Essai gratuit 30 jours, sans engagement (39 €/mois ensuite). Pour commencer, répondez simplement « OK ».",
}
FOLLOWUP = {
    "text": "Je reviens vers vous au sujet des réponses à vos avis Google : l'essai de 30 jours reste gratuit. Voici une réponse rédigée pour un avis tout récent. Pour essayer, répondez « OK »."
}
LAST = {
    "text": "Dernier message de ma part : la proposition d'essai gratuit reste ouverte, il suffit de répondre « OK »."
}


def _prospect(s, place_id="p1", email="gerant@chezmarcel.fr", score=10, **kw):
    p = Prospect(
        place_id=place_id,
        name="Chez Marcel",
        city="Lyon",
        status=ProspectStatus.ENRICHED,
        email=email,
        canal_prioritaire="email" if email else kw.pop("canal", None),
        unanswered_last_30d=14,
        negative_unanswered_last_30d=3,
        score=score,
        **kw,
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
            Review(
                prospect_id=p.id,
                review_id=f"{place_id}-c",
                rating=3,
                text="Risotto moyen",
                author="Léa",
                date=MONDAY_10H_PARIS - timedelta(days=2),
            ),
        ]
    )
    s.flush()
    return p


def _mailbox(s, address="neo@repondu-a.fr", quota=30, weeks_ago=4):
    mb = Mailbox(
        address=address,
        provider="log",
        daily_quota=quota,
        warmup_started_at=MONDAY_10H_PARIS - timedelta(weeks=weeks_ago),
    )
    s.add(mb)
    s.flush()
    return mb


def _llm_full_sequence():
    # J0 : 2 exemples (2 réponses, dont une ≤2★ relue) + mail ; J+3 : 1 réponse + relance ; J+8 : dernier
    return FakeLLM([REPLY, REPLY_NEG, {"ok": True}, EMAIL, REPLY, FOLLOWUP, LAST])


def test_send_window():
    assert seq.is_send_window(MONDAY_10H_PARIS)
    assert not seq.is_send_window(MONDAY_10H_PARIS - timedelta(days=2))  # samedi
    assert not seq.is_send_window(datetime(2026, 9, 7, 5, 30))  # 7 h 30 à Paris
    assert not seq.is_send_window(datetime(2026, 9, 7, 16, 0))  # 18 h à Paris (fin exclue)


def test_effective_quota_ramp(db):
    with db() as s:
        for weeks, expected in [(0, 20), (1, 23), (2, 27), (3, 30), (8, 30)]:
            mb = _mailbox(s, address=f"w{weeks}@x.fr", weeks_ago=weeks)
            assert effective_quota(mb, MONDAY_10H_PARIS) == expected
        small = _mailbox(s, address="small@x.fr", quota=10, weeks_ago=8)
        assert effective_quota(small, MONDAY_10H_PARIS) == 10


def test_enroll_rules(db):
    with db() as s:
        _prospect(s, "p1")
        _prospect(s, "p2", email=None, canal="sms", mobile_phone="+33612345678")
        _prospect(s, "p3", email="stop@x.fr")
        _prospect(s, "p4", email=None, canal="email")  # canal sans contact
        p5 = _prospect(s, "p5")
        p5.status = ProspectStatus.QUALIFIED  # pas encore enrichi
        s.add(OptOut(contact="stop@x.fr", source="test"))
        s.flush()
        assert seq.enroll(s, MONDAY_10H_PARIS) == 2
        assert seq.enroll(s, MONDAY_10H_PARIS) == 0  # idempotent
        rows = {o.prospect.place_id: o for o in s.query(Outreach)}
        assert rows["p1"].channel == "email" and rows["p1"].contact == "gerant@chezmarcel.fr"
        assert rows["p2"].channel == "sms" and rows["p2"].contact == "+33612345678"
        assert rows["p1"].token and rows["p1"].next_action_at == MONDAY_10H_PARIS


def test_full_email_sequence_with_time_travel(db):
    provider = FakeEmailProvider()
    set_provider("log", provider)
    try:
        with db() as s:
            _prospect(s)
            _mailbox(s)
            llm = _llm_full_sequence()
            now = MONDAY_10H_PARIS
            r = seq.run_outreach(now=now, llm=llm, session=s)
            assert r.enrolled == 1 and r.sent == 1 and r.errors == 0
            o = s.query(Outreach).one()
            assert o.step == 1 and o.status == OutreachStatus.ACTIVE
            assert o.next_action_at == now + timedelta(days=3) and o.mailbox_id == 1
            m1 = o.messages[0]
            assert (
                m1.step == 1
                and m1.status == MessageStatus.SENT
                and m1.provider_message_id == "fake-1"
            )
            assert "14 avis sans réponse ces 30 jours, dont 3 négatifs" in m1.body
            assert "— Avis de Marie (★★★★★)" in m1.body and "— Avis de Bruno (★★)" in m1.body
            assert (
                provider.sent[0].to == "gerant@chezmarcel.fr"
                and provider.sent[0].from_address == "neo@repondu-a.fr"
            )
            assert (
                provider.sent[0].headers["List-Unsubscribe"].startswith("<mailto:neo@repondu-a.fr")
            )
            assert len(o.examples) == 2

            # Trop tôt : rien
            r = seq.run_outreach(now=now + timedelta(days=1), llm=llm, session=s)
            assert r.sent == 0 and o.step == 1

            # J+3 : relance avec 1 nouvel avis (Léa)
            r = seq.run_outreach(now=now + timedelta(days=3), llm=llm, session=s)
            assert r.sent == 1
            s.expire(o, ["messages"])
            m2 = [m for m in o.messages if m.step == 2][0]
            assert m2.subject == "Re: 14 avis sans réponse chez Chez Marcel"
            assert "— Avis de Léa (★★★)" in m2.body and len(o.examples) == 3
            assert o.next_action_at == now + timedelta(days=8)

            # J+8 : dernier message puis séquence terminée
            r = seq.run_outreach(now=now + timedelta(days=8), llm=llm, session=s)
            assert r.sent == 1
            assert o.step == 3 and o.status == OutreachStatus.DONE and o.next_action_at is None
            s.expire(o, ["messages"])
            assert "Dernier message" in [m for m in o.messages if m.step == 3][0].body
            assert s.get(Mailbox, 1).sent_total == 3
            assert len(llm.responses) == 0
    finally:
        reset_providers()


def test_idempotent_resume_after_crash(db):
    provider = FakeEmailProvider()
    set_provider("log", provider)
    try:
        with db() as s:
            p = _prospect(s)
            _mailbox(s)
            o = Outreach(
                prospect_id=p.id,
                channel="email",
                contact=p.email,
                next_action_at=MONDAY_10H_PARIS,
                token="t",
                examples=[],
            )
            s.add(o)
            s.flush()
            # Message déjà envoyé mais l'état de la séquence n'a pas été avancé (coupure)
            s.add(
                OutreachMessage(
                    outreach_id=o.id,
                    step=1,
                    channel="email",
                    subject="x",
                    body="y",
                    status=MessageStatus.SENT,
                    sent_at=MONDAY_10H_PARIS - timedelta(hours=1),
                    mailbox_id=1,
                )
            )
            s.flush()
            r = seq.run_outreach(now=MONDAY_10H_PARIS, llm=FakeLLM(), session=s)
            assert r.sent == 0 and provider.sent == []
            assert o.step == 1 and o.next_action_at == MONDAY_10H_PARIS - timedelta(
                hours=1
            ) + timedelta(days=3)
    finally:
        reset_providers()


def test_send_failure_then_retry(db):
    provider = FakeEmailProvider()
    provider.fail_next = True
    set_provider("log", provider)
    try:
        with db() as s:
            _prospect(s)
            _mailbox(s)
            llm = FakeLLM([REPLY, REPLY_NEG, {"ok": True}, EMAIL])
            r = seq.run_outreach(now=MONDAY_10H_PARIS, llm=llm, session=s)
            assert r.errors == 1 and r.sent == 0
            o = s.query(Outreach).one()
            assert o.step == 0 and o.messages[0].status == MessageStatus.FAILED
            assert "panne simulée" in o.messages[0].error
            r = seq.run_outreach(now=MONDAY_10H_PARIS + timedelta(hours=1), llm=llm, session=s)
            assert (
                r.sent == 1 and o.step == 1 and len(o.messages) == 1
            )  # même message, pas recomposé
            assert len(llm.responses) == 0
    finally:
        reset_providers()


def test_quota_and_rotation(db):
    provider = FakeEmailProvider()
    set_provider("log", provider)
    try:
        with db() as s:
            for i in range(5):
                _prospect(s, f"p{i}", email=f"g{i}@x.fr", score=10 - i)
            a = _mailbox(s, "a@repondu.fr", quota=2)
            b = _mailbox(s, "b@repondu.fr", quota=1)
            llm = FakeLLM()
            llm.default = EMAIL
            # les exemples : réponses génériques acceptables
            llm.responses.extend([REPLY, REPLY_NEG, {"ok": True}, EMAIL] * 5)
            r = seq.run_outreach(now=MONDAY_10H_PARIS, llm=llm, session=s)
            assert r.enrolled == 5 and r.sent == 3 and r.skipped_quota == 2
            senders = [e.from_address for e in provider.sent]
            assert senders == ["a@repondu.fr", "b@repondu.fr", "a@repondu.fr"]  # rotation
            assert (
                remaining_today(s, a, MONDAY_10H_PARIS) == 0
                and remaining_today(s, b, MONDAY_10H_PARIS) == 0
            )
            assert pick_mailbox(s, MONDAY_10H_PARIS) is None
            # Le lendemain, les quotas sont rendus et la boîte préférée conservée par séquence
            tomorrow = MONDAY_10H_PARIS + timedelta(days=1)
            assert remaining_today(s, a, tomorrow) == 2
            assert pick_mailbox(s, tomorrow, preferred_id=b.id).id == b.id
            assert s.get(Mailbox, a.id).sent_total == 2
    finally:
        reset_providers()


def test_stop_on_reply_and_opt_out(db):
    with db() as s:
        p1 = _prospect(s, "p1", email="a@x.fr")
        p2 = _prospect(s, "p2", email="b@x.fr")
        seq.enroll(s, MONDAY_10H_PARIS)
        o1, o2 = s.query(Outreach).order_by(Outreach.id).all()
        seq.record_outcome(s, o1, "yes", note="a répondu OK", now=MONDAY_10H_PARIS)
        assert o1.status == OutreachStatus.YES and o1.next_action_at is None and o1.replied_at
        seq.opt_out(s, "B@x.fr", source="imap", now=MONDAY_10H_PARIS)
        assert o2.status == OutreachStatus.OPTED_OUT and seq.is_opted_out(s, "b@x.fr")
        assert seq.due_outreach(s, MONDAY_10H_PARIS + timedelta(days=30)) == []
        # Un prospect opt-out ne sera plus jamais enrôlé
        p2.status = ProspectStatus.ENRICHED
        s.delete(o2)
        s.flush()
        assert seq.enroll(s, MONDAY_10H_PARIS) == 0
        assert p1.id and p2.id


def test_outside_window_only_enrolls(db, monkeypatch):
    with db() as s:
        _prospect(s)
        _mailbox(s)
        r = seq.run_outreach(now=MONDAY_10H_PARIS - timedelta(days=2), llm=FakeLLM(), session=s)
        assert r.enrolled == 1 and r.skipped_window and r.sent == 0
    monkeypatch.setenv("OUTREACH_SEND_HOURS", "0-24")
    reset_settings()
    assert seq.is_send_window(datetime(2026, 9, 7, 5, 30))


def test_api_outreach_endpoints(db):
    from fastapi.testclient import TestClient

    from app.main import app

    with db() as s:
        _prospect(s)
        _mailbox(s)
        seq.enroll(s, MONDAY_10H_PARIS)
    with TestClient(app) as c:
        rows = c.get("/outreach?status=active").json()
        assert (
            len(rows) == 1
            and rows[0]["prospect_name"] == "Chez Marcel"
            and rows[0]["messages"] == []
        )
        assert c.get("/outreach?status=bidon").status_code == 422
        detail = c.get(f"/outreach/{rows[0]['id']}").json()
        assert detail["channel"] == "email" and detail["messages"] == []
        r = c.post(
            f"/outreach/{rows[0]['id']}/outcome", json={"outcome": "objection", "note": "trop cher"}
        )
        assert r.json()["status"] == "objection" and r.json()["outcome_note"] == "trop cher"
        assert c.post("/outreach/999/outcome", json={"outcome": "yes"}).status_code == 404
        boxes = c.get("/mailboxes").json()
        assert boxes[0]["address"] == "neo@repondu-a.fr" and boxes[0]["quota_today"] == 30
        assert c.post("/mailboxes/1/resume").json() == {"id": 1, "active": True}
        assert c.post("/mailboxes/sync").json()["total"] == 0
        assert c.get("/outreach/999").status_code == 404
