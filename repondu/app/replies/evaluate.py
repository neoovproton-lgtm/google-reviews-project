"""B1 — Évaluation : 30 avis test → 30 réponses → fichier markdown à relire (DoD ≥ 27/30)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.llm import LLM
from app.replies.generator import generate_reply
from app.replies.prompt import Profile, ReviewInput

DEFAULT_PROFILE = Profile(
    name="Chez Fixture",
    cuisine_type="bistrot français",
    tone="chaleureux et professionnel",
    signature="Marc, Chez Fixture",
    manager_first_name="Marc",
    never_say="nouveau chef ; travaux ; concurrents",
    contact_email="contact@chezfixture.example",
)


def run_evaluation(
    reviews_file: Path, out_dir: Path, llm: LLM, profile: Profile = DEFAULT_PROFILE
) -> Path:
    reviews = json.loads(reviews_file.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"eval-replies-{stamp}.md"
    lines = [
        "# Évaluation B1 — réponses générées",
        "",
        f"Modèle : `{getattr(llm, 'model', '?')}` · profil : {profile.name} · {len(reviews)} avis.",
        "Cocher `[x]` chaque réponse acceptable **sans retouche**. Objectif : ≥ 27/30.",
        "",
    ]
    flagged = 0
    for r in reviews:
        review = ReviewInput(
            rating=int(r["rating"]), text=r.get("text", ""), author=r.get("author")
        )
        gen = generate_reply(profile, review, llm=llm)
        flagged += int(gen.needs_human)
        lines += [
            f"## {r['id']}. {review.rating}★ — {review.author or 'anonyme'}",
            "",
            f"> {review.text or '(sans texte)'}",
            "",
            gen.text,
            "",
            f"_{gen.word_count} mots · {gen.attempts} tentative(s) · élément repris : "
            f"{gen.detail_reused or '—'}_"
            + (f" · **à revoir** : {', '.join(gen.issues)}" if gen.issues else ""),
            "",
            "- [ ] Acceptable sans retouche",
            "",
        ]
    lines.append(f"Réponses marquées à revoir par le code : {flagged}/{len(reviews)}.")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
