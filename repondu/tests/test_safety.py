import pytest

from app.replies.generator import generate_reply
from app.replies.prompt import Profile, ReviewInput
from app.replies.safety import needs_human_review, safety_flags
from tests.fakes import FakeLLM

POSITIVE_CASES = [
    ("J'ai vu un cafard courir sous la table, on est partis.", ["hygiene"]),
    ("Les toilettes étaient d'une saleté repoussante, vaisselle sale.", ["hygiene"]),
    ("Un cheveu dans mon assiette, et l'hygiène laisse à désirer.", ["hygiene"]),
    ("Le poisson sentait mauvais, clairement périmé.", ["hygiene"]),
    ("Intoxication alimentaire après les moules, deux jours au lit.", ["intoxication"]),
    ("Toute la famille a été malade la nuit suivante, vomissements.", ["intoxication"]),
    ("Gastro carabinée après le tartare, on a fini aux urgences.", ["intoxication"]),
    ("Le serveur a fait une remarque raciste sur mon accent.", ["discrimination"]),
    ("On nous a refusé l'entrée à cause de mon voile.", ["discrimination"]),
    ("Remarques sexistes envers ma femme, inadmissible.", ["discrimination"]),
    ("Aucune rampe, le gérant a dit que le handicap n'était pas son problème.", ["discrimination"]),
    ("Je vais porter plainte et contacter mon avocat.", ["legal"]),
    ("Signalé à la DGCCRF, c'est une arnaque pure et simple.", ["legal"]),
    ("Rendez-vous au tribunal, mise en demeure envoyée.", ["legal"]),
    ("On a appelé la police tellement le gérant était agressif.", ["discrimination", "legal"]),
    ("INTOXIQUÉ par le poulet, je vais porter PLAINTE !!!", ["intoxication", "legal"]),
    (
        "Des rats dans la cuisine visibles depuis la salle, on a été malades.",
        ["hygiene", "intoxication"],
    ),
    ("Serveuse humiliante et insultante, discrimination évidente.", ["discrimination"]),
]
NEGATIVE_CASES = [
    "Le risotto était crémeux à souhait, service impeccable.",
    "Un peu d'attente mais le tiramisu a sauvé la soirée.",
    "Plat tiède et serveur pressé, on n'y retournera pas.",
    "Trop cher pour ce que c'est, portions minuscules.",
    "Salle très bruyante, difficile de discuter.",
    "",
]


@pytest.mark.parametrize("text,expected", POSITIVE_CASES)
def test_sensitive_reviews_are_flagged(text, expected):
    assert safety_flags(text) == expected
    assert needs_human_review(text)


@pytest.mark.parametrize("text", NEGATIVE_CASES)
def test_ordinary_reviews_are_not_flagged(text):
    assert safety_flags(text) == [] and not needs_human_review(text)


def test_case_count_is_at_least_twenty():
    assert len(POSITIVE_CASES) + len(NEGATIVE_CASES) >= 20


def test_generator_forces_human_review_on_sensitive_review():
    profile = Profile(name="Chez Marcel", contact_email="contact@chezmarcel.fr")
    review = ReviewInput(1, "Intoxication alimentaire après les moules, deux jours au lit.")
    good = {
        "reply": "Nous prenons très au sérieux ce que vous décrivez après les moules de votre repas. "
        "Pouvez-vous nous écrire à contact@chezmarcel.fr pour que nous comprenions ce qui s'est passé ?",
        "detail_reused": "moules",
    }
    gen = generate_reply(profile, review, llm=FakeLLM([good, {"ok": True}]))
    assert gen.issues == []  # conforme…
    assert gen.safety_flags == ["intoxication"] and gen.needs_human  # …mais jamais auto
