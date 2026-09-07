from datetime import timedelta

import pytest

from app.config import get_settings
from app.models import Prospect, ProspectStatus, Review, utcnow
from app.outreach import compose as c
from tests.fakes import FakeLLM

FACTS = c.ProspectFacts(
    name="Chez Marcel",
    city="Lyon",
    category="Brasserie",
    unanswered_last_30d=14,
    negative_unanswered_last_30d=3,
    reviews_per_month=22.0,
    response_rate=0.1,
)
EXAMPLES = [
    {
        "review_id": 1,
        "author": "Marie",
        "rating": 5,
        "text": "Risotto parfait",
        "reply": "Merci Marie !\n\nL'équipe de Chez Marcel",
    },
    {
        "review_id": 2,
        "author": "Bruno",
        "rating": 2,
        "text": "Attente d'une heure",
        "reply": "Une heure d'attente, nous comprenons. Écrivez-nous.\n\nL'équipe de Chez Marcel",
    },
]
GOOD = {
    "subject": "14 avis sans réponse chez Chez Marcel",
    "opening": "Sur votre fiche Google, j'ai compté 14 avis sans réponse ces 30 jours, dont 3 négatifs. "
    "Répondu rédige et publie les réponses dans votre ton, sous 24 h, avec votre veto. "
    "Voici deux exemples écrits pour vos vrais avis :",
    "closing": "Si vous voulez essayer 30 jours, gratuitement et sans engagement (39 €/mois ensuite), "
    "répondez simplement « OK ».",
}


def test_constat_wording():
    assert FACTS.constat == "14 avis sans réponse ces 30 jours, dont 3 négatifs"
    f = c.ProspectFacts("X", None, None, 5, 1, None, None)
    assert f.constat == "5 avis sans réponse ces 30 jours, dont 1 négatif"
    f = c.ProspectFacts("X", None, None, 5, 0, None, None)
    assert f.constat == "5 avis sans réponse ces 30 jours"


def test_check_text_good():
    assert c.check_text(f"{GOOD['opening']}\n{GOOD['closing']}", FACTS, 120) == []


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "J'espère que ce message vous trouve bien. 14 avis, dont 3 négatifs, répondez OK.",
            "formule générique : « j'espere que ce message vous trouve »",
        ),
        (
            "Notre solution innovante traite vos 14 avis dont 3 négatifs. Répondez OK svp.",
            "formule générique : « solution »",
        ),
        (
            "Tu as 14 avis sans réponse dont 3 négatifs, réponds OK si tu veux essayer.",
            "tutoiement",
        ),
        (
            "Vous avez 14 avis sans réponse ces 30 jours, dont 3 négatifs. Essai gratuit 30 jours.",
            "l'action « répondre OK » manque",
        ),
        (
            "Vous avez beaucoup d'avis sans réponse, dont 3 négatifs. Répondez OK pour essayer.",
            "le nombre d'avis sans réponse manque",
        ),
        (
            "Vous avez 14 avis sans réponse ces 30 jours. Répondez OK pour essayer gratuitement.",
            "le nombre d'avis négatifs manque",
        ),
        ("Trop court OK 14 3.", "trop court"),
    ],
)
def test_check_text_issues(text, expected):
    assert expected in c.check_text(text, FACTS, 120)


def test_check_text_length():
    long = "mot " * 121 + "14 3 OK"
    assert any(i.startswith("trop long") for i in c.check_text(long, FACTS, 120))


def test_assemble_email_structure():
    body = c.assemble_email(GOOD["opening"], EXAMPLES, GOOD["closing"], get_settings())
    assert body.startswith("Bonjour,\n\n")
    assert "— Avis de Marie (★★★★★) : « Risotto parfait »" in body
    assert "Réponse proposée : Merci Marie !" in body
    assert body.rstrip().endswith("répondez simplement « STOP ».")
    assert get_settings().outreach_signature in body


def test_compose_first_email_single_attempt():
    llm = FakeLLM([GOOD])
    out = c.compose_first_email(FACTS, EXAMPLES, llm)
    assert out.subject == GOOD["subject"] and out.issues == [] and out.attempts == 1
    assert "14 avis sans réponse ces 30 jours, dont 3 négatifs" in out.body
    assert "Constat chiffré à reprendre tel quel" in llm.calls[0]["user"]
    assert "39 €/mois" in llm.calls[0]["user"]


def test_compose_first_email_retries_with_feedback():
    bad = dict(GOOD, closing="Essai gratuit 30 jours, n'hésitez pas à répondre OK.")
    llm = FakeLLM([bad, GOOD])
    out = c.compose_first_email(FACTS, EXAMPLES, llm)
    assert out.attempts == 2 and out.issues == []
    assert "formule générique" in llm.calls[1]["user"]
    llm = FakeLLM([bad, bad])
    out = c.compose_first_email(FACTS, EXAMPLES, llm)
    assert out.issues == ["formule générique : « n'hesitez pas »"] and out.attempts == 2


def test_compose_followup_last_dm_sms():
    llm = FakeLLM(
        [
            {
                "text": "Je reviens vers vous au sujet des réponses à vos avis Google : l'essai de 30 jours reste gratuit. Un nouvel avis de Léa (3★) vient d'arriver, voici la réponse rédigée. Pour essayer, répondez « OK »."
            },
            {
                "text": "Dernier message de ma part : la proposition d'essai gratuit reste ouverte, il suffit de répondre « OK »."
            },
            {
                "text": "Bonjour ! En regardant votre fiche Google, j'ai vu 14 avis sans réponse ces 30 jours, dont 3 négatifs. Répondu rédige et publie les réponses dans votre ton. Voici deux réponses écrites pour vos vrais avis. Essai gratuit 30 jours, répondez « OK »."
            },
        ]
    )
    ex = {
        "review_id": 3,
        "author": "Léa",
        "rating": 3,
        "text": "Service lent",
        "reply": "Merci Léa.",
    }
    fu = c.compose_followup(FACTS, ex, llm)
    assert fu.issues == [] and "— Avis de Léa (★★★)" in fu.body and "STOP" in fu.body
    last = c.compose_last(FACTS, llm)
    assert last.issues == [] and "Dernier message" in last.body
    dm = c.compose_dm(FACTS, EXAMPLES, llm)
    assert dm.issues == [] and "Marie" in dm.body and "STOP" not in dm.body
    sms = c.compose_sms(FACTS, "https://r.example/p/abc")
    assert sms.issues == [] and "https://r.example/p/abc" in sms.body and "STOP" in sms.body
    assert len(sms.body) <= 320


def test_pick_example_reviews_prefers_mixed_and_skips_flagged(db):
    now = utcnow()
    with db() as s:
        p = Prospect(place_id="p", name="Chez Marcel")
        s.add(p)
        s.flush()
        s.add_all(
            [
                Review(
                    prospect_id=p.id, review_id="a", rating=5, text="Excellent risotto", date=now
                ),
                Review(
                    prospect_id=p.id,
                    review_id="b",
                    rating=1,
                    text="Cafard dans l'assiette",
                    date=now - timedelta(days=1),
                ),
                Review(
                    prospect_id=p.id,
                    review_id="c",
                    rating=2,
                    text="Plat froid",
                    date=now - timedelta(days=2),
                ),
                Review(
                    prospect_id=p.id,
                    review_id="d",
                    rating=4,
                    text="Bien",
                    date=now - timedelta(days=3),
                    has_owner_response=1,
                ),
                Review(
                    prospect_id=p.id, review_id="e", rating=5, text="", date=now - timedelta(days=4)
                ),
                Review(
                    prospect_id=p.id,
                    review_id="f",
                    rating=4,
                    text="Bon accueil",
                    date=now - timedelta(days=5),
                ),
            ]
        )
        s.flush()
        chosen = c.pick_example_reviews(s, p.id, 2)
        assert [r.review_id for r in chosen] == ["a", "c"]  # b signalé, d répondu, e vide
        chosen = c.pick_example_reviews(s, p.id, 1, exclude_ids=(chosen[0].id,))
        assert [r.review_id for r in chosen] == ["c"]


def test_build_examples_uses_reply_generator(db):
    with db() as s:
        p = Prospect(place_id="p", name="Chez Marcel", category="Brasserie", email="c@m.fr")
        s.add(p)
        s.flush()
        s.add(
            Review(
                prospect_id=p.id, review_id="a", rating=5, text="Excellent risotto", author="Marie"
            )
        )
        s.flush()
        llm = FakeLLM(
            [
                {
                    "reply": "Merci Marie, ravi que le risotto vous ait plu.",
                    "detail_reused": "risotto",
                }
            ]
        )
        ex = c.build_examples(s, p, llm)
        assert ex[0]["author"] == "Marie" and ex[0]["reply"].endswith("L'équipe de Chez Marcel")
        assert ex[0]["needs_human"] is False
        assert "Brasserie" in llm.calls[0]["user"]


def test_outreach_eval_writes_file(db, tmp_path):
    from app.outreach.evaluate import run_outreach_eval

    with db() as s:
        p = Prospect(
            place_id="p",
            name="Chez Marcel",
            city="Lyon",
            status=ProspectStatus.ENRICHED,
            email="c@m.fr",
            unanswered_last_30d=14,
            negative_unanswered_last_30d=3,
            score=10,
        )
        s.add(p)
        s.flush()
        s.add(
            Review(
                prospect_id=p.id, review_id="a", rating=5, text="Excellent risotto", author="Marie"
            )
        )
        s.flush()
        llm = FakeLLM(
            [
                {
                    "reply": "Merci Marie, ravi que le risotto vous ait plu.",
                    "detail_reused": "risotto",
                },
                GOOD,
            ]
        )
        path = run_outreach_eval(s, llm, tmp_path)
    text = path.read_text()
    assert (
        "Chez Marcel (Lyon) — c@m.fr" in text and "- [ ] Acceptable" in text and "14 avis" in text
    )
