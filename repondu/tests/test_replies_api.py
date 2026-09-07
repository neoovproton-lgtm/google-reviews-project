"""B3 — Établissements, brouillons, veto/validation via l'API ; webhook Telegram."""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import reset_settings
from app.llm import set_llm
from app.models import Prospect, Reply, ReplyStatus, Review, utcnow
from app.telegram.client import set_telegram
from tests.fakes import FakeLLM, FakeTelegram

GOOD_5 = {
    "reply": "Merci Marie ! Ravi que le risotto aux cèpes vous ait plu.",
    "detail_reused": "risotto aux cèpes",
}
GOOD_1 = {
    "reply": "Nous prenons au sérieux ce que vous décrivez après les moules. "
    "Pouvez-vous nous écrire à contact@chezmarcel.fr pour que nous comprenions ce qui s'est passé ?",
    "detail_reused": "moules",
}


def _seed(db):
    with db() as s:
        p = Prospect(place_id="p1", name="Chez Marcel")
        s.add(p)
        s.flush()
        s.add_all(
            [
                Review(
                    prospect_id=p.id,
                    review_id="r1",
                    rating=5,
                    text="Le risotto aux cèpes était top",
                    author="Marie",
                    date=utcnow(),
                ),
                Review(
                    prospect_id=p.id,
                    review_id="r2",
                    rating=1,
                    text="Intoxication après les moules",
                    author="Bruno",
                    date=utcnow() - timedelta(days=1),
                ),
                Review(
                    prospect_id=p.id,
                    review_id="r3",
                    rating=4,
                    text="Déjà répondu",
                    has_owner_response=1,
                ),
            ]
        )
        return p.id


def test_full_flow_draft_pending_decision(db):
    pid = _seed(db)
    llm = FakeLLM([GOOD_5, GOOD_1, {"ok": True}])
    tg = FakeTelegram()
    set_llm(llm)
    set_telegram(tg)
    try:
        from app.main import app

        with TestClient(app) as c:
            r = c.post(
                "/establishments",
                json={
                    "name": "Chez Marcel",
                    "prospect_id": pid,
                    "signature": "Marc",
                    "telegram_chat_id": "42",
                    "contact_email": "contact@chezmarcel.fr",
                },
            )
            assert r.status_code == 201
            est_id = r.json()["id"]
            assert (
                c.post("/establishments", json={"name": "X", "prospect_id": 999}).status_code == 404
            )
            assert len(c.get("/establishments").json()) == 1

            drafts = c.post(f"/establishments/{est_id}/draft", json={}).json()
            assert len(drafts) == 2  # r3 déjà répondu
            by_review = {d["review_author"]: d for d in drafts}
            assert by_review["Marie"]["needs_human"] is False and by_review["Marie"][
                "text"
            ].endswith("\n\nMarc")
            assert by_review["Bruno"]["needs_human"] is True and by_review["Bruno"][
                "safety_flags"
            ] == ["intoxication"]
            assert len(tg.messages) == 2 and tg.messages[0]["chat_id"] == "42"
            assert tg.messages[0]["buttons"][0][0][1] == f"approve:{drafts[0]['id']}"
            assert "Validation humaine obligatoire" in [m for m in tg.texts if "Bruno" in m][0]
            assert "sauf veto" in [m for m in tg.texts if "Marie" in m][0]

            # Idempotent : plus rien à rédiger
            assert c.post(f"/establishments/{est_id}/draft", json={}).json() == []
            assert c.post("/establishments/999/draft", json={}).status_code == 404

            pending = c.get("/reviews/pending").json()
            assert [p["status"] for p in pending] == ["pending", "pending"]
            assert len(c.get(f"/reviews/pending?establishment_id={est_id}").json()) == 2

            marie = by_review["Marie"]
            r = c.post(
                f"/reviews/{marie['review_id']}/decision",
                json={"decision": "approve", "text": "Merci Marie, texte corrigé.", "by": "neo"},
            )
            assert (
                r.status_code == 200
                and r.json()["status"] == "approved"
                and r.json()["text"] == "Merci Marie, texte corrigé."
            )
            assert (
                c.post(
                    f"/reviews/{marie['review_id']}/decision", json={"decision": "reject"}
                ).status_code
                == 409
            )
            bruno = by_review["Bruno"]
            assert (
                c.post(
                    f"/reviews/{bruno['review_id']}/decision", json={"decision": "reject"}
                ).json()["status"]
                == "rejected"
            )
            assert c.post("/reviews/999/decision", json={"decision": "approve"}).status_code == 404
            assert c.get("/reviews/pending").json() == []
            assert [r["status"] for r in c.get("/replies").json()] == ["approved", "rejected"]
            assert len(c.get("/replies?status=rejected").json()) == 1
            assert c.get("/replies?status=nope").status_code == 422
    finally:
        set_llm(None)
        set_telegram(None)


def test_auto_approve_respects_delay_and_needs_human(db):
    _seed(db)
    with db() as s:
        from app.models import Establishment

        est = Establishment(name="Chez Marcel", prospect_id=1, auto_publish_delay_h=24)
        s.add(est)
        s.flush()
        old = utcnow() - timedelta(hours=25)
        s.add_all(
            [
                Reply(review_id=1, establishment_id=est.id, text="ok", created_at=old),
                Reply(
                    review_id=2,
                    establishment_id=est.id,
                    text="sensible",
                    needs_human=1,
                    created_at=old,
                ),
                Reply(review_id=3, establishment_id=est.id, text="récent", created_at=utcnow()),
            ]
        )
    from app.main import app

    with TestClient(app) as c:
        assert c.post("/reviews/auto-approve").json() == {"approved": [1]}
        statuses = {r["id"]: (r["status"], r["decision_by"]) for r in c.get("/replies").json()}
        assert statuses[1] == (ReplyStatus.APPROVED, "auto (délai écoulé)")
        assert statuses[2][0] == ReplyStatus.PENDING and statuses[3][0] == ReplyStatus.PENDING


def test_webhook_secret_and_dispatch(db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    reset_settings()
    tg = FakeTelegram()
    set_telegram(tg)
    try:
        from app.main import app

        with TestClient(app) as c:
            update = {"message": {"chat": {"id": 7}, "text": "/start"}}
            assert c.post("/telegram/webhook", json=update).status_code == 401
            r = c.post(
                "/telegram/webhook",
                json=update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
            )
            assert r.status_code == 200 and r.json() == {"ok": True}
            assert tg.messages[0]["chat_id"] == "7" and "Bienvenue" in tg.texts[0]
    finally:
        set_telegram(None)
