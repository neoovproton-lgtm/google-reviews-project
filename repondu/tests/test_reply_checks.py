import pytest

from app.replies.checks import append_signature, check_reply, detail_is_in_review, word_count
from app.replies.prompt import Profile, ReviewInput

PROFILE = Profile(
    name="Chez Marcel", signature="Marc, Chez Marcel", never_say="travaux ; nouveau chef"
)
GOOD_5 = (
    "Merci Marie pour ce retour ! Ravi que le risotto aux cèpes vous ait plu, "
    "Julien sera touché. À très bientôt chez nous."
)
REVIEW_5 = ReviewInput(5, "Le risotto aux cèpes était crémeux et Julien adorable.", author="Marie")


def test_word_count():
    assert word_count("Un deux  trois\nquatre") == 4


def test_detail_in_review_ignores_accents_and_case():
    assert detail_is_in_review("Risotto aux Cèpes", "le risotto aux cepes etait bon")
    assert not detail_is_in_review("tiramisu", "le risotto était bon")
    assert not detail_is_in_review("", "le risotto était bon")


def test_good_reply_has_no_issue():
    assert check_reply(GOOD_5, "risotto aux cèpes", PROFILE, REVIEW_5) == []


def test_too_long_for_positive():
    reply = " ".join(["mot"] * 61)
    issues = check_reply(reply, "risotto", PROFILE, REVIEW_5)
    assert any(i.startswith("trop long") for i in issues)
    assert (
        check_reply(" ".join(["mot"] * 61), "risotto", PROFILE, ReviewInput(3, "risotto"))[0:0]
        == []
    )
    assert not any(
        i.startswith("trop long")
        for i in check_reply(" ".join(["mot"] * 90), "risotto", PROFILE, ReviewInput(3, "risotto"))
    )


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("Nous sommes désolés pour la gêne occasionnée lors de votre risotto.", "excuse générique"),
        ("Merci, le risotto sera offert la prochaine fois, revenez !", "promesse commerciale"),
        ("Merci, nous ferons un geste commercial sur le risotto.", "promesse commerciale"),
        ("Merci à toi pour ton retour sur le risotto, reviens vite !", "tutoiement"),
        (
            "Merci pour le risotto, message rédigé par une intelligence artificielle.",
            "mention d'IA",
        ),
        ("Merci pour le risotto ! 😊 À très vite chez nous.", "emoji"),
        (
            "Merci pour le risotto, notre nouveau chef sera ravi de vous revoir.",
            "élément interdit : « nouveau chef »",
        ),
        ("Merci pour le risotto, à bientôt. Marc, Chez Marcel", "signature incluse par le modèle"),
        (
            "Merci beaucoup pour votre visite et à très bientôt chez nous !",
            "aucun élément concret de l'avis repris",
        ),
        ("Merci", "réponse trop courte"),
    ],
)
def test_detects_each_issue(reply, expected):
    detail = "tiramisu" if "aucun élément" in expected else "risotto"
    assert expected in check_reply(reply, detail, PROFILE, REVIEW_5)


def test_negative_requires_offline_contact():
    review = ReviewInput(1, "Attente d'une heure pour le plat, personne ne nous a prévenus.")
    bad = "Nous comprenons votre agacement après une heure d'attente sans information, ce n'est pas ce que nous voulons offrir à nos clients."
    assert "pas d'invitation à échanger hors ligne" in check_reply(
        bad, "une heure d'attente", PROFILE, review
    )
    good = (
        bad
        + " Pouvez-vous nous écrire à contact@chezmarcel.fr pour que nous comprenions ce qui s'est passé ce soir-là ?"
    )
    assert check_reply(good, "une heure d'attente", PROFILE, review) == []


def test_no_text_review_skips_detail_check():
    assert (
        check_reply(
            "Merci beaucoup pour ces cinq étoiles, à très bientôt chez nous !",
            "",
            PROFILE,
            ReviewInput(5, ""),
        )
        == []
    )


def test_tutoiement_allowed_when_profile_says_so():
    p = Profile(name="Le Spot", use_tutoiement=True)
    assert "tutoiement" not in check_reply(
        "Merci à toi pour ton retour sur le risotto, reviens vite !", "risotto", p, REVIEW_5
    )


def test_append_signature():
    assert append_signature("Merci.  ", PROFILE) == "Merci.\n\nMarc, Chez Marcel"
    assert append_signature("Merci.", Profile(name="X")) == "Merci."
