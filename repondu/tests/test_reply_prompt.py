from app.replies.prompt import (
    MAX_WORDS_NEGATIVE,
    MAX_WORDS_POSITIVE,
    Profile,
    ReviewInput,
    build_reviewer_message,
    build_user_message,
    rating_rules,
)

PROFILE = Profile(
    name="Chez Marcel",
    cuisine_type="brasserie",
    signature="Marc, Chez Marcel",
    manager_first_name="Marc",
    never_say="travaux ; nouveau chef",
    contact_email="contact@chezmarcel.fr",
)


def test_rating_rules_limits():
    assert rating_rules(5)[1] == MAX_WORDS_POSITIVE
    assert rating_rules(4)[1] == MAX_WORDS_POSITIVE
    assert rating_rules(3)[1] == MAX_WORDS_NEGATIVE
    assert rating_rules(1)[1] == MAX_WORDS_NEGATIVE
    assert "hors ligne" in rating_rules(2)[0]


def test_user_message_contains_profile_and_review():
    msg = build_user_message(PROFILE, ReviewInput(5, "Superbe tartare", author="Léa"))
    assert "Chez Marcel" in msg and "brasserie" in msg and "Marc" in msg
    assert "Vouvoiement obligatoire" in msg
    assert "travaux ; nouveau chef" in msg
    assert "Superbe tartare" in msg and "Auteur : Léa" in msg
    assert "Maximum 60 mots" in msg
    assert "contact@chezmarcel.fr" not in msg  # seulement pour ≤ 2★
    assert "Chez Marcel" not in msg.split("## Avis")[1].split("## Consigne")[0]


def test_user_message_negative_adds_contact_and_feedback():
    msg = build_user_message(
        PROFILE, ReviewInput(1, "Attente interminable"), feedback=["trop long"]
    )
    assert "contact@chezmarcel.fr" in msg
    assert "Maximum 90 mots" in msg
    assert "- trop long" in msg


def test_tutoiement_option_and_empty_text():
    p = Profile(name="Le Spot", use_tutoiement=True)
    msg = build_user_message(p, ReviewInput(4, "   "))
    assert "Tutoiement autorisé" in msg and "(avis sans texte)" in msg


def test_reviewer_message():
    msg = build_reviewer_message(ReviewInput(2, "Plat froid"), "Merci pour votre retour")
    assert "Plat froid" in msg and "Merci pour votre retour" in msg and "2/5" in msg
