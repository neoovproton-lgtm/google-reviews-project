"""CLI `repondu` (typer)."""

from __future__ import annotations

import logging

import typer

from app.config import get_settings
from app.db import init_db, session_scope

app = typer.Typer(help="Répondu — outils de qualification des prospects.", no_args_is_help=True)


def _setup() -> None:
    logging.basicConfig(
        level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    init_db()


@app.command()
def initdb() -> None:
    """Crée/migre la base SQLite."""
    _setup()
    typer.echo(f"Base prête : {get_settings().database_url}")


CityOpt = typer.Option(None, "--city", "-c", help="Ville (répétable). Défaut : toutes.")
MaxJobsOpt = typer.Option(None, help="Limite le nombre de jobs pour cette exécution.")


@app.command()
def scrape(city: list[str] = CityOpt, max_jobs: int | None = MaxJobsOpt) -> None:
    """A1 — Liste les restaurants des villes sur Google Maps → table prospects."""
    _setup()
    from app.scraping.maps import prospect_counts, scrape_cities

    report = scrape_cities(city or None, max_jobs=max_jobs)
    with session_scope() as session:
        counts = prospect_counts(session)
    typer.echo(
        f"Jobs planifiés {report.jobs_planned}, exécutés {report.jobs_run} "
        f"(ok {report.jobs_done}, erreur {report.jobs_error}). Prospects : {counts}"
    )


LimitOpt = typer.Option(None, "--limit", "-n", help="Nombre max de prospects à traiter.")


@app.command()
def reviews(limit: int | None = LimitOpt, city: list[str] = CityOpt) -> None:
    """A2 — Ouvre chaque fiche, lit les 30 derniers avis, calcule taux de réponse et avis/mois."""
    _setup()
    from app.scraping.reviews import scrape_reviews

    typer.echo(scrape_reviews(limit=limit, cities=city or None))


@app.command()
def score() -> None:
    """A3 — score = avis/mois × (1 − taux de réponse) ; qualifie ≥ 10 avis/mois et < 50 %."""
    _setup()
    from app.scoring import score_all

    typer.echo(score_all())


StatusOpt = typer.Option("qualified", "--status", "-s", help="Statut à exporter (qualified…).")
ExportLimitOpt = typer.Option(300, "--limit", "-n", help="Nombre max de lignes.")


@app.command()
def export(status: str = StatusOpt, limit: int = ExportLimitOpt) -> None:
    """A3 — Exporte la liste (CSV) dans data/exports/, triée par score décroissant."""
    _setup()
    from app.export import write_export

    with session_scope() as session:
        path = write_export(session, get_settings().data_dir / "exports", status, limit)
    typer.echo(f"Export : {path}")


RetryOpt = typer.Option(False, "--retry-errors", help="Rejoue les enrichissements en erreur.")


@app.command()
def enrich(limit: int | None = LimitOpt, retry_errors: bool = RetryOpt) -> None:
    """A4 — Email, formulaire, Instagram/Facebook, mobile depuis le site ; canal prioritaire."""
    _setup()
    from app.enrichment.run import enrich_all

    typer.echo(enrich_all(limit=limit, retry_errors=retry_errors))


ReviewsFileOpt = typer.Option(None, "--reviews", help="Fichier JSON d'avis test.")


@app.command("eval-replies")
def eval_replies(reviews: str | None = ReviewsFileOpt) -> None:
    """B1 — Génère les réponses aux 30 avis test et écrit un markdown à relire (clé API requise)."""
    _setup()
    from pathlib import Path

    from app.llm import get_llm
    from app.replies.evaluate import run_evaluation

    settings = get_settings()
    src = (
        Path(reviews)
        if reviews
        else Path(__file__).resolve().parent.parent / "data/eval/reviews_test.json"
    )
    path = run_evaluation(src, settings.data_dir / "exports", get_llm())
    typer.echo(f"Relecture : {path}")


EstOpt = typer.Option(..., "--establishment", "-e", help="Identifiant de l'établissement.")


@app.command()
def draft(establishment: int = EstOpt, limit: int | None = LimitOpt) -> None:
    """B3 — Brouillons de réponse pour les avis sans réponse, envoyés sur Telegram."""
    _setup()
    from app.models import Establishment
    from app.replies.service import draft_for_establishment
    from app.telegram.client import get_telegram

    with session_scope() as session:
        est = session.get(Establishment, establishment)
        if est is None:
            raise typer.BadParameter("établissement inconnu")
        drafts = draft_for_establishment(session, est, limit=limit, telegram=get_telegram())
        for r in drafts:
            flag = " [validation humaine]" if r.needs_human else ""
            typer.echo(f"#{r.id} avis {r.review_id}{flag} : {r.text.splitlines()[0][:80]}")
    typer.echo(f"{len(drafts)} brouillon(s)")


@app.command("auto-approve")
def auto_approve() -> None:
    """Approuve les brouillons dont le délai de veto est écoulé (à planifier toutes les heures)."""
    _setup()
    from app.replies.service import auto_approve_due

    with session_scope() as session:
        approved = auto_approve_due(session)
    typer.echo(f"{len(approved)} brouillon(s) approuvé(s) : {[r.id for r in approved]}")


WebhookUrlOpt = typer.Option(..., "--url", help="URL publique HTTPS de POST /telegram/webhook")


@app.command("telegram-webhook")
def telegram_webhook(url: str = WebhookUrlOpt) -> None:
    """Enregistre l'URL du webhook auprès de Telegram (TELEGRAM_BOT_TOKEN requis)."""
    _setup()
    from app.telegram.client import HttpTelegram

    settings = get_settings()
    if not settings.telegram_bot_token:
        raise typer.BadParameter("TELEGRAM_BOT_TOKEN manquant")
    result = HttpTelegram(settings.telegram_bot_token).set_webhook(
        url, settings.telegram_webhook_secret
    )
    typer.echo(result)


EvalLimitOpt = typer.Option(10, "--limit", "-n", help="Nombre de prospects.")


@app.command("outreach-eval")
def outreach_eval(limit: int = EvalLimitOpt) -> None:
    """C1 — Génère N mails de prospection pour de vrais prospects → markdown à relire."""
    _setup()
    from app.llm import get_llm
    from app.outreach.evaluate import run_outreach_eval

    with session_scope() as session:
        path = run_outreach_eval(session, get_llm(), get_settings().data_dir / "exports", limit)
    typer.echo(f"Relecture : {path}")


ForceWindowOpt = typer.Option(
    False, "--force-window", help="Ignore la fenêtre jours ouvrés/heures."
)


@app.command("outreach-run")
def outreach_run(limit: int | None = LimitOpt, force_window: bool = ForceWindowOpt) -> None:
    """C2 — Enrôle les prospects enrichis et envoie les étapes dues (J0, J+3, J+8)."""
    _setup()
    from app.outreach.sequence import run_outreach

    r = run_outreach(limit=limit, force_window=force_window)
    typer.echo(
        f"Enrôlés {r.enrolled} · envoyés {r.sent} · préparés (manuel) {r.prepared_manual} · "
        f"hors fenêtre {r.skipped_window} · sans quota {r.skipped_quota} · erreurs {r.errors}"
    )
    for d in r.details[:30]:
        typer.echo("  " + d)


SyncOpt = typer.Option(False, "--sync", help="Charge data/mailboxes.json en base.")
ResumeOpt = typer.Option(None, "--resume", help="Réactive la boîte (adresse).")


@app.command()
def mailboxes(sync: bool = SyncOpt, resume: str | None = ResumeOpt) -> None:
    """C2/C5 — État des boîtes d'envoi (quota du jour, bounces, statut)."""
    _setup()
    from sqlalchemy import select

    from app.models import Mailbox
    from app.outreach.mailboxes import load_mailboxes_file, mailbox_health, sync_mailboxes

    with session_scope() as session:
        if sync:
            n = sync_mailboxes(session, load_mailboxes_file(get_settings().mailboxes_file))
            typer.echo(f"{n} boîte(s) créée(s)")
        if resume:
            mb = session.scalar(select(Mailbox).where(Mailbox.address == resume.lower()))
            if mb is None:
                raise typer.BadParameter("boîte inconnue")
            mb.active, mb.paused_reason = 1, None
            typer.echo(f"{mb.address} réactivée")
        for mb in session.scalars(select(Mailbox).order_by(Mailbox.id)):
            h = mailbox_health(session, mb)
            state = "actif" if h["active"] else f"COUPÉE ({h['paused_reason']})"
            typer.echo(
                f"{h['address']:35s} {h['provider']:7s} {h['sent_today']}/{h['quota_today']} "
                f"aujourd'hui · total {h['sent_total']} · bounce {h['bounce_rate']} · {state}"
            )


ChatOpt = typer.Option(None, "--chat", help="Chat Telegram (défaut TELEGRAM_CHAT_ID).")


@app.command("dm-batch")
def dm_batch(chat: str | None = ChatOpt, limit: int | None = LimitOpt) -> None:
    """C3 — Livre sur Telegram le lot de DM Instagram/Facebook à envoyer à la main."""
    _setup()
    from app.outreach.channels import deliver_dm_batch
    from app.telegram.client import get_telegram

    settings = get_settings()
    chat_id = chat or settings.telegram_chat_id
    if not chat_id:
        raise typer.BadParameter("--chat ou TELEGRAM_CHAT_ID requis")
    with session_scope() as session:
        batch = deliver_dm_batch(
            session, get_telegram(), chat_id, limit or settings.outreach_dm_batch
        )
    typer.echo(f"{len(batch)} DM livré(s)")


@app.command("inbox-poll")
def inbox_poll() -> None:
    """C4 — Relève IMAP des boîtes d'envoi : OK → oui, STOP → opt-out, sinon réponse à qualifier."""
    _setup()
    from app.outreach.inbox import poll_inboxes
    from app.outreach.mailboxes import load_mailboxes_file, sync_mailboxes
    from app.telegram.client import get_telegram

    settings = get_settings()
    entries = load_mailboxes_file(settings.mailboxes_file)
    with session_scope() as session:
        sync_mailboxes(session, entries)
        typer.echo(poll_inboxes(session, entries, get_telegram(), settings.telegram_chat_id))


@app.command()
def funnel() -> None:
    """C4 — Entonnoir de prospection (texte identique à /stats Telegram)."""
    _setup()
    from sqlalchemy import select

    from app.models import Mailbox
    from app.outreach.mailboxes import mailbox_health
    from app.outreach.stats import format_funnel
    from app.outreach.stats import funnel as _funnel

    with session_scope() as session:
        boxes = [mailbox_health(session, mb) for mb in session.scalars(select(Mailbox))]
        typer.echo(format_funnel(_funnel(session), boxes))


IntervalOpt = typer.Option(3600, "--interval", help="Secondes entre deux passages.")


@app.command()
def scheduler(interval: int = IntervalOpt) -> None:
    """Boucle horaire : relève IMAP, prospection, approbation automatique des brouillons."""
    from app.scheduler import main

    main(interval)


@app.command()
def jobs() -> None:
    """État des jobs de scraping par ville."""
    _setup()
    from sqlalchemy import func, select

    from app.models import ScrapeJob

    with session_scope() as session:
        rows = session.execute(
            select(ScrapeJob.city, ScrapeJob.status, func.count())
            .group_by(ScrapeJob.city, ScrapeJob.status)
            .order_by(ScrapeJob.city)
        ).all()
    for city, status, n in rows:
        typer.echo(f"{city:20s} {status:10s} {n}")


if __name__ == "__main__":
    app()
