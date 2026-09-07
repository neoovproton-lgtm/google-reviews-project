"""D1 — Conversion d'un oui, invitation gestionnaire (4 étapes), rappel, démarrage d'essai."""

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.config import reset_settings
from app.models import (
    ClientMessage,
    Establishment,
    OnboardingStatus,
    Outreach,
    OutreachStatus,
    Prospect,
    ProspectStatus,
)
from app.outreach.email_providers import reset_providers, set_provider
from app.service import onboarding
from app.telegram.bot import handle_update
from tests.fakes import FakeEmailProvider, FakeTelegram

NOW = datetime(2026, 9, 7, 8, 0)


def _outreach(s, status=OutreachStatus.REPLIED):
    p = Prospect(
        place_id="p1",
        name="Chez Marcel",
        category="Brasserie",
        status=ProspectStatus.ENRICHED,
        email="gerant@chezmarcel.fr",
        mobile_phone="+33612345678",
        response_rate=0.1,
        rating=4.3,
    )
    s.add(p)
    s.flush()
    o = Outreach(
        prospect_id=p.id,
        channel="email",
        contact="gerant@chezmarcel.fr",
        status=status,
        step=1,
        token="t",
    )
    s.add(o)
    s.flush()
    return o


def test_convert_creates_client_with_baseline(db):
    with db() as s:
        o = _outreach(s)
        est = onboarding.convert_outreach(s, o, now=NOW)
        assert est.name == "Chez Marcel" and est.cuisine_type == "Brasserie"
        assert est.contact_email == "gerant@chezmarcel.fr" and est.mobile_phone == "+33612345678"
        assert est.baseline_response_rate == 0.1 and est.baseline_rating == 4.3
        assert (
            est.signature == "L'équipe de Chez Marcel"
            and est.onboarding_status == OnboardingStatus.CREATED
        )
        assert o.status == OutreachStatus.YES and o.outcome_note == "converti en client"
        assert onboarding.convert_outreach(s, o, now=NOW) is est  # idempotent


def test_invitation_email_and_attachments(db, monkeypatch, tmp_path):
    (tmp_path / "etape-1.png").write_bytes(b"\x89PNG1")
    (tmp_path / "etape-3.jpg").write_bytes(b"\xff\xd8jpg")
    monkeypatch.setenv("ONBOARDING_ASSETS_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_MANAGER_EMAIL", "gestion@repondu.fr")
    monkeypatch.setenv("SERVICE_EMAIL_PROVIDER", "resend")
    reset_settings()
    provider = FakeEmailProvider()
    set_provider("resend", provider)
    try:
        with db() as s:
            est = onboarding.convert_outreach(s, _outreach(s), now=NOW)
            onboarding.send_invitation(s, est, now=NOW)
            assert est.onboarding_status == OnboardingStatus.INVITED and est.invited_at == NOW
            mail = provider.sent[0]
            assert mail.to == "gerant@chezmarcel.fr" and "gestionnaire" in mail.subject
            assert (
                "gestion@repondu.fr" in mail.text
                and "1. Ouvrez https://business.google.com" in mail.text
            )
            assert "4. « Inviter »" in mail.text and "veto pendant 24 h" in mail.text
            assert [a[0] for a in mail.attachments] == ["etape-1.png", "etape-3.jpg"]
            log = s.query(ClientMessage).one()
            assert (
                log.kind == "invite"
                and log.channel == "email"
                and log.provider_message_id == "fake-1"
            )
    finally:
        reset_providers()


def test_reminder_after_three_days_once(db):
    with db() as s:
        est = onboarding.convert_outreach(s, _outreach(s), now=NOW)
        onboarding.send_invitation(s, est, now=NOW)
        assert onboarding.send_reminders(s, now=NOW + timedelta(days=2)) == []
        assert onboarding.send_reminders(s, now=NOW + timedelta(days=3)) == [est]
        assert est.reminded_at == NOW + timedelta(days=3)
        assert onboarding.send_reminders(s, now=NOW + timedelta(days=9)) == []
        kinds = [m.kind for m in s.query(ClientMessage).order_by(ClientMessage.id)]
        assert kinds == ["invite", "reminder"]
        onboarding.manager_added(s, est, now=NOW + timedelta(days=4))
        assert est.onboarding_status == OnboardingStatus.MANAGER_ADDED
        assert est.trial_ends_at == NOW + timedelta(days=34)
        assert onboarding.send_reminders(s, now=NOW + timedelta(days=30)) == []


def test_telegram_client_and_manager_commands(db):
    tg = FakeTelegram()
    with db() as s:
        o = _outreach(s)
        handle_update({"message": {"chat": {"id": 7}, "text": f"/client {o.id}"}}, s, tg)
        assert "Client #1 Chez Marcel créé" in tg.texts[-1] and "/gestionnaire 1" in tg.texts[-1]
        est = s.query(Establishment).one()
        assert est.onboarding_status == OnboardingStatus.INVITED
        handle_update({"message": {"chat": {"id": 7}, "text": "/gestionnaire 1"}}, s, tg)
        assert "essai démarré" in tg.texts[-1] and est.trial_started_at is not None
        handle_update({"message": {"chat": {"id": 7}, "text": "/client 99"}}, s, tg)
        assert tg.texts[-1] == "Usage : /client <séquence>"


def test_api_convert_invite_manager(db):
    with db() as s:
        o = _outreach(s)
        oid = o.id
    from app.main import app

    with TestClient(app) as c:
        r = c.post(f"/outreach/{oid}/convert")
        assert r.status_code == 201 and r.json()["onboarding_status"] == "invited"
        eid = r.json()["id"]
        assert c.post(f"/outreach/{oid}/convert").json()["id"] == eid  # idempotent
        r = c.post(f"/establishments/{eid}/manager-added")
        assert r.json()["onboarding_status"] == "manager_added" and r.json()["trial_ends_at"]
        msgs = c.get(f"/establishments/{eid}/messages").json()
        assert [m["kind"] for m in msgs] == ["invite"]
        assert c.post("/outreach/999/convert").status_code == 404
        assert c.post("/establishments/999/invite").status_code == 404
