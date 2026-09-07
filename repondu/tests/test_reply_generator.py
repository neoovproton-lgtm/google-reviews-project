from app.replies.generator import generate_reply
from app.replies.prompt import Profile, ReviewInput
from tests.fakes import FakeLLM

PROFILE = Profile(
    name="Chez Marcel", signature="Marc, Chez Marcel", contact_email="contact@chezmarcel.fr"
)
REVIEW_5 = ReviewInput(5, "Le risotto aux cèpes était crémeux et Julien adorable.", author="Marie")
REVIEW_1 = ReviewInput(1, "Attente d'une heure pour le plat, personne ne nous a prévenus.")
GOOD_5 = {
    "reply": "Merci Marie ! Ravi que le risotto aux cèpes vous ait plu, Julien sera touché.",
    "detail_reused": "risotto aux cèpes",
}
GOOD_1 = {
    "reply": "Une heure d'attente sans un mot de notre part, ce n'est pas acceptable et nous le comprenons. "
    "Pouvez-vous nous écrire à contact@chezmarcel.fr pour que nous comprenions ce qui s'est passé ce soir-là ?",
    "detail_reused": "une heure d'attente",
}


def test_positive_review_single_call_and_signature():
    llm = FakeLLM([GOOD_5])
    gen = generate_reply(PROFILE, REVIEW_5, llm=llm)
    assert gen.issues == [] and not gen.needs_human and gen.attempts == 1
    assert gen.text.endswith("\n\nMarc, Chez Marcel") and gen.body == GOOD_5["reply"]
    assert gen.word_count == 15 and gen.model == "fake-sonnet"
    assert len(llm.calls) == 1 and llm.calls[0]["schema"] == "ReplyOutput"
    assert "Vouvoiement obligatoire" in llm.calls[0]["user"]


def test_retry_with_feedback_then_success():
    bad = {"reply": "Merci, le risotto sera offert la prochaine fois !", "detail_reused": "risotto"}
    llm = FakeLLM([bad, GOOD_5])
    gen = generate_reply(PROFILE, REVIEW_5, llm=llm)
    assert gen.attempts == 2 and gen.issues == [] and not gen.needs_human
    assert "promesse commerciale" in llm.calls[1]["user"]


def test_two_failures_marks_needs_human_and_keeps_best():
    bad1 = {
        "reply": "Merci à toi, le risotto sera offert !",
        "detail_reused": "risotto",
    }  # 2 problèmes
    bad2 = {
        "reply": "Merci pour le risotto, ce sera offert la prochaine fois.",
        "detail_reused": "risotto",
    }  # 1
    gen = generate_reply(PROFILE, REVIEW_5, llm=FakeLLM([bad1, bad2]))
    assert gen.needs_human and gen.attempts == 2
    assert gen.body == bad2["reply"] and gen.issues == ["promesse commerciale"]


def test_negative_review_goes_through_reviewer():
    llm = FakeLLM([GOOD_1, {"ok": True, "issues": []}])
    gen = generate_reply(PROFILE, REVIEW_1, llm=llm)
    assert gen.issues == [] and len(llm.calls) == 2
    assert (
        llm.calls[1]["schema"] == "ReviewVerdict" and "Attente d'une heure" in llm.calls[1]["user"]
    )


def test_reviewer_rejection_triggers_regeneration():
    llm = FakeLLM([GOOD_1, {"ok": False, "issues": ["ton défensif"]}, GOOD_1, {"ok": True}])
    gen = generate_reply(PROFILE, REVIEW_1, llm=llm)
    assert gen.attempts == 2 and gen.issues == [] and not gen.needs_human
    assert "relecture : ton défensif" in llm.calls[2]["user"]


def test_reviewer_rejection_twice_needs_human():
    llm = FakeLLM([GOOD_1, {"ok": False, "issues": ["x"]}, GOOD_1, {"ok": False, "issues": []}])
    gen = generate_reply(PROFILE, REVIEW_1, llm=llm)
    assert gen.needs_human and gen.issues == ["relecture : x"]  # meilleure tentative conservée


def test_evaluation_writes_markdown(tmp_path):
    from pathlib import Path

    from app.replies.evaluate import run_evaluation

    llm = FakeLLM()
    llm.default = {
        "reply": "Merci beaucoup pour votre visite et ce retour, à très bientôt chez nous.",
        "detail_reused": "",
    }
    src = Path(__file__).parent.parent / "data/eval/reviews_test.json"
    path = run_evaluation(src, tmp_path, llm)
    text = path.read_text()
    assert text.count("- [ ] Acceptable sans retouche") == 30
    assert "fake-sonnet" in text and "Chez Fixture" in text
    assert "Réponses marquées à revoir par le code" in text
