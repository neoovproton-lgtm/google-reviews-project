"""B3 — Formulaire d'onboarding Telegram (5 questions) et boutons de validation."""

from sqlalchemy import select

from app.config import reset_settings
from app.models import Establishment, Prospect, Reply, ReplyStatus, Review, TelegramSession
from app.telegram.bot import QUESTIONS, handle_update
from tests.fakes import FakeTelegram

CHAT = 4242


def msg(text, chat=CHAT):
    return {
        "update_id": 1,
        "message": {"chat": {"id": chat}, "from": {"first_name": "Neo"}, "text": text},
    }


def cb(data, chat=CHAT, cid="cb1"):
    return {
        "callback_query": {
            "id": cid,
            "data": data,
            "from": {"first_name": "Neo"},
            "message": {"chat": {"id": chat}},
        }
    }


def test_onboarding_five_questions_creates_profile(db):
    tg = FakeTelegram()
    answers = [
        "Chez Marcel",
        "bistrot français",
        "Marc",
        "chaleureux et direct ; Marc, Chez Marcel",
        "travaux ; concurrents",
    ]
    with db() as s:
        handle_update(msg("/onboard"), s, tg)
        assert QUESTIONS[0][1] in tg.texts[-1]
        for i, a in enumerate(answers):
            handle_update(msg(a), s, tg)
            if i < 4:
                assert QUESTIONS[i + 1][1] == tg.texts[-1]
        assert tg.texts[-1].startswith("Profil enregistré")
    with db() as s:
        est = s.scalars(select(Establishment)).one()
        assert est.name == "Chez Marcel" and est.cuisine_type == "bistrot français"
        assert est.manager_first_name == "Marc" and est.tone == "chaleureux et direct"
        assert est.signature == "Marc, Chez Marcel" and est.never_say == "travaux ; concurrents"
        assert est.telegram_chat_id == str(CHAT)
        state = s.get(TelegramSession, str(CHAT))
        assert state.state is None and state.answers == {}
    assert len(tg.messages) == 6


def test_onboarding_defaults_and_cancel(db):
    tg = FakeTelegram()
    with db() as s:
        handle_update(msg("/annuler"), s, tg)
        assert tg.texts[-1] == "Rien à annuler."
        handle_update(msg("/onboard"), s, tg)
        handle_update(msg("Le Spot"), s, tg)
        handle_update(msg("/annuler"), s, tg)
        assert tg.texts[-1] == "Formulaire annulé."
        handle_update(msg("/onboard"), s, tg)
        for a in ["Le Spot", "pizzeria", "Léa", "sobre", "aucun"]:
            handle_update(msg(a), s, tg)
    with db() as s:
        est = s.scalars(select(Establishment)).one()
        assert est.tone == "sobre" and est.signature == "Léa, Le Spot" and est.never_say is None


def test_start_help_and_profiles(db):
    tg = FakeTelegram()
    with db() as s:
        handle_update(msg("/start"), s, tg)
        assert "/onboard" in tg.texts[-1]
        handle_update(msg("bonjour"), s, tg)
        assert "/onboard" in tg.texts[-1]
        handle_update(msg("/profils"), s, tg)
        assert "Aucun profil" in tg.texts[-1]
        s.add(Establishment(name="Chez Marcel", telegram_chat_id=str(CHAT)))
        s.flush()
        handle_update(msg("/profils@NeoovBOT"), s, tg)
        assert "Chez Marcel" in tg.texts[-1]


def test_chat_allowlist(db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    reset_settings()
    tg = FakeTelegram()
    with db() as s:
        handle_update(msg("/onboard", chat=999), s, tg)
        assert tg.texts == ["Conversation non autorisée."]
        handle_update(cb("approve:1", chat=999), s, tg)
        assert tg.callbacks[-1] == ("cb1", "Non autorisé")


def _seed_reply(s):
    p = Prospect(place_id="p1", name="Chez Marcel")
    s.add(p)
    s.flush()
    r = Review(prospect_id=p.id, review_id="r1", rating=2, text="Plat tiède", author="Sarah")
    est = Establishment(name="Chez Marcel", prospect_id=p.id, telegram_chat_id=str(CHAT))
    s.add_all([r, est])
    s.flush()
    reply = Reply(review_id=r.id, establishment_id=est.id, text="Merci Sarah…", needs_human=1)
    s.add(reply)
    s.flush()
    return reply.id


def test_callback_approve_and_reject(db):
    tg = FakeTelegram()
    with db() as s:
        rid = _seed_reply(s)
        handle_update(cb(f"approve:{rid}"), s, tg)
        assert tg.callbacks[-1] == ("cb1", "Réponse approuvée ✅")
        assert s.get(Reply, rid).status == ReplyStatus.APPROVED
        assert s.get(Reply, rid).decision_by == "telegram:Neo"
        handle_update(cb(f"reject:{rid}", cid="cb2"), s, tg)
        assert tg.callbacks[-1] == ("cb2", "brouillon déjà approved")
        handle_update(cb("reject:999", cid="cb3"), s, tg)
        assert tg.callbacks[-1] == ("cb3", "Brouillon introuvable")
        handle_update(cb("bidule", cid="cb4"), s, tg)
        assert tg.callbacks[-1] == ("cb4", None)


def test_malformed_update_does_not_raise(db):
    tg = FakeTelegram()
    with db() as s:
        handle_update({"message": {"chat": {}}}, s, tg)
        handle_update({"weird": 1}, s, tg)
    assert tg.messages == []
