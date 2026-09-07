"""C1 — Évaluation : 10 mails générés pour de vrais prospects → markdown à relire."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import LLM
from app.models import Prospect, ProspectStatus
from app.outreach.compose import build_examples, compose_first_email, facts_from_prospect


def run_outreach_eval(session: Session, llm: LLM, out_dir: Path, limit: int = 10) -> Path:
    settings = get_settings()
    prospects = list(
        session.scalars(
            select(Prospect)
            .where(Prospect.status == ProspectStatus.ENRICHED, Prospect.email.is_not(None))
            .order_by(Prospect.score.desc().nullslast())
            .limit(limit)
        )
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"eval-outreach-{stamp}.md"
    lines = [
        "# Évaluation C1 — mails de prospection",
        "",
        f"Modèle : `{getattr(llm, 'model', '?')}` · {len(prospects)} prospects.",
        "Cocher `[x]` chaque mail acceptable : 0 faute, 0 formule générique.",
        "",
    ]
    for p in prospects:
        facts = facts_from_prospect(p)
        examples = build_examples(session, p, llm)
        c = compose_first_email(facts, examples, llm, settings)
        lines += [
            f"## {p.name} ({p.city or '?'}) — {p.email}",
            "",
            f"**Objet :** {c.subject}",
            "",
            "```",
            c.body,
            "```",
            "",
            f"_{len(c.free_text.split())} mots rédigés · {c.attempts} tentative(s)_"
            + (f" · **à revoir** : {', '.join(c.issues)}" if c.issues else ""),
            "",
            "- [ ] Acceptable",
            "",
        ]
    if not prospects:
        lines.append("Aucun prospect `enriched` avec email : lancer A1 → A4 d'abord.")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
