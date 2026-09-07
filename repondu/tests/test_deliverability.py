"""C5 — Webhooks Resend/Brevo, compteurs par boîte, coupure automatique, santé dans /stats."""

import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

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
    utcnow,
)
from app.outreach.email_providers import parse_brevo_event, parse_resend_event
from app.outreach.events import apply_provider_event, verify_svix_signature
from app.outreach.mailboxes import check_health


def _seed(s, n_messages=1, mailbox_kwargs=None):
    mb = Mailbox(address="neo@repondu.fr", provider="resend", **(mailbox_kwargs or {}))
    s.add(mb)
    s.flush()
    ids = []
    for i in range(n_messages):
        p = Prospect(
            place_id=f"p{i}", name=f"Resto {i}", status=ProspectStatus.ENRICHED, email=f"g{i}@x.fr"
        )
        s.add(p)
        s.flush()
        o = Outreach(prospect_id=p.id, channel="email", contact=p.email, step=1, token=f"t{i}")
        s.add(o)
        s.flush()
        m = OutreachMessage(
            outreach_id=o.id,
            step=1,
            channel="email",
            body="x",
            status=MessageStatus.SENT,
            sent_at=utcnow(),
            mailbox_id=mb.id,
            provider_message_id=f"msg-{i}",
        )
        s.add(m)
        s.flush()
        mb.sent_total += 1
        ids.append((o.id, m.id))
    return mb, ids


def test_parse_events():
    e = parse_resend_event(
        {
            "type": "email.bounced",
            "created_at": "2026-09-07T10:00:00.000Z",
            "data": {"email_id": "abc", "to": ["g@x.fr"]},
        }
    )
    assert e.type == "bounced" and e.provider_message_id == "abc" and e.email == "g@x.fr"
    assert e.occurred_at.hour == 10
    assert parse_resend_event({"type": "email.clicked", "data": {}}).type == "other"
    b = parse_brevo_event(
        {"event": "hard_bounce", "email": "g@x.fr", "message-id": "<m1>", "ts_event": 1757239200}
    )
    assert b.type == "bounced" and b.provider_message_id == "<m1>"
    assert parse_brevo_event({"event": "spam"}).type == "complained"
    assert parse_brevo_event({"event": "unique_opened"}).type == "opened"


def test_delivered_opened_bounced_complained(db):
    with db() as s:
        mb, [(oid, mid)] = _seed(s)
        ev = parse_resend_event(
            {"type": "email.delivered", "data": {"email_id": "msg-0", "to": ["g0@x.fr"]}}
        )
        assert apply_provider_event(s, ev, "resend")["matched"] is True
        m = s.get(OutreachMessage, mid)
        assert m.status == MessageStatus.DELIVERED and m.delivered_at and mb.delivered_total == 1
        # Doublon ignoré
        assert (
            apply_provider_event(s, ev, "resend").get("duplicate") is True
            and mb.delivered_total == 1
        )
        ev = parse_resend_event({"type": "email.opened", "data": {"email_id": "msg-0"}})
        apply_provider_event(s, ev, "resend")
        apply_provider_event(s, ev, "resend")  # deuxième ouverture : pas recomptée
        assert m.status == MessageStatus.OPENED and mb.opened_total == 1
        ev = parse_resend_event({"type": "email.bounced", "data": {"email_id": "msg-0"}})
        apply_provider_event(s, ev, "resend")
        assert m.status == MessageStatus.BOUNCED and mb.bounced_total == 1
        assert s.get(Outreach, oid).status == OutreachStatus.BOUNCED
        # Plainte : opt-out du contact + coupure immédiate de la boîte
        ev = parse_resend_event(
            {"type": "email.complained", "data": {"email_id": "msg-0", "to": ["g0@x.fr"]}}
        )
        res = apply_provider_event(s, ev, "resend")
        assert res["mailbox_paused"].startswith("plainte spam")
        assert mb.active == 0 and s.query(OptOut).filter_by(contact="g0@x.fr").one()
        # Message inconnu
        assert (
            apply_provider_event(
                s,
                parse_resend_event({"type": "email.delivered", "data": {"email_id": "nope"}}),
                "resend",
            )["matched"]
            is False
        )


def test_bounce_rate_cutoff_after_minimum_sent(db):
    with db() as s:
        mb, ids = _seed(s, n_messages=25)
        # 1 bounce sur 25 = 4 % > 3 % → coupure (après le minimum de 20 envois)
        ev = parse_resend_event({"type": "email.bounced", "data": {"email_id": "msg-3"}})
        res = apply_provider_event(s, ev, "resend")
        assert res["mailbox_paused"].startswith("bounce 4.0 %") and mb.active == 0
        assert mb.paused_reason.startswith("bounce")


def test_no_cutoff_below_minimum_or_threshold(db):
    with db() as s:
        mb, ids = _seed(s, n_messages=10)
        apply_provider_event(
            s,
            parse_resend_event({"type": "email.bounced", "data": {"email_id": "msg-1"}}),
            "resend",
        )
        assert mb.active == 1  # 10 envois < 20 : pas de décision
        mb2 = Mailbox(
            address="b@repondu.fr", sent_total=100, bounced_total=2, complained_total=0, active=1
        )
        assert check_health(mb2) is None and mb2.active == 1  # 2 % ≤ 3 %


def test_svix_signature():
    secret_bytes = b"0123456789abcdef0123456789abcdef"
    secret = "whsec_" + base64.b64encode(secret_bytes).decode()
    body = b'{"type":"email.delivered"}'
    ts = str(int(time.time()))
    signed = f"m1.{ts}.".encode() + body
    sig = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    headers = {"svix-id": "m1", "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}
    assert verify_svix_signature(secret, headers, body)
    assert not verify_svix_signature(secret, headers, b'{"type":"x"}')
    assert not verify_svix_signature(
        secret, {**headers, "svix-timestamp": str(int(time.time()) - 1000)}, body
    )
    assert not verify_svix_signature(secret, {}, body)


def test_webhook_endpoints(db, monkeypatch):
    monkeypatch.setenv("BREVO_WEBHOOK_TOKEN", "brv")
    reset_settings()
    with db() as s:
        _seed(s, n_messages=2)
    from app.main import app

    with TestClient(app) as c:
        r = c.post(
            "/webhooks/resend",
            json={"type": "email.delivered", "data": {"email_id": "msg-0", "to": ["g0@x.fr"]}},
        )
        assert r.status_code == 200 and r.json()["matched"] is True
        assert (
            c.post("/webhooks/resend", json={"type": "email.clicked", "data": {}}).json()["ignored"]
            is True
        )
        assert (
            c.post(
                "/webhooks/brevo", json={"event": "delivered", "message-id": "msg-1"}
            ).status_code
            == 401
        )
        r = c.post(
            "/webhooks/brevo?token=brv",
            json=[{"event": "delivered", "message-id": "msg-1"}, {"event": "click"}],
        )
        assert r.json()["results"][0]["matched"] is True and r.json()["results"][1] == {
            "ignored": True
        }
        stats = c.get("/stats").json()
        assert (
            stats["mailboxes"][0]["delivered_total"] == 2
            and stats["mailboxes"][0]["active"] is True
        )
        assert stats["mailboxes"][0]["bounce_rate"] == 0.0


def test_resend_signature_enforced_when_configured(db, monkeypatch):
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", "whsec_" + base64.b64encode(b"k" * 32).decode())
    reset_settings()
    from app.main import app

    with TestClient(app) as c:
        r = c.post(
            "/webhooks/resend",
            content=json.dumps({"type": "email.delivered", "data": {}}),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 401
