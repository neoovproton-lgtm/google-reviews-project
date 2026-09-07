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
