"""Boucle de tâches récurrentes (service `scheduler` de docker-compose).

Toutes les heures : prospection (fenêtre d'envoi respectée), relève IMAP, approbation des
brouillons dont le veto est écoulé. Chaque tâche est isolée : une erreur n'arrête pas la boucle.
"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.db import init_db, session_scope

log = logging.getLogger(__name__)


def tick() -> dict:
    settings = get_settings()
    results: dict = {}
    from app.outreach.inbox import poll_inboxes
    from app.outreach.mailboxes import load_mailboxes_file, sync_mailboxes
    from app.outreach.sequence import run_outreach
    from app.replies.service import auto_approve_due
    from app.telegram.client import get_telegram

    try:
        entries = load_mailboxes_file(settings.mailboxes_file)
        with session_scope() as s:
            sync_mailboxes(s, entries)
            results["inbox"] = poll_inboxes(s, entries, get_telegram(), settings.telegram_chat_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("relève IMAP")
        results["inbox"] = f"erreur : {exc}"
    try:
        r = run_outreach()
        results["outreach"] = {
            "enrolled": r.enrolled,
            "sent": r.sent,
            "manual": r.prepared_manual,
            "window": not r.skipped_window,
            "errors": r.errors,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("prospection")
        results["outreach"] = f"erreur : {exc}"
    try:
        with session_scope() as s:
            results["auto_approve"] = [r.id for r in auto_approve_due(s)]
    except Exception as exc:  # noqa: BLE001
        log.exception("auto-approve")
        results["auto_approve"] = f"erreur : {exc}"
    results["service"] = service_tick(settings)
    return results


def service_tick(settings=None) -> dict:
    """Phase D : boîtes gestionnaire/service, avis des clients, rappels, rapports du lundi."""
    from app.service.inboxes import poll_manager_inbox, poll_service_inbox
    from app.service.loop import run_service_cycle
    from app.service.onboarding import send_reminders
    from app.service.report import send_weekly_reports
    from app.service.survey import send_end_of_trial_surveys
    from app.telegram.client import get_telegram

    settings = settings or get_settings()
    out: dict = {}
    for name, fn in (("manager_inbox", poll_manager_inbox), ("service_inbox", poll_service_inbox)):
        try:
            with session_scope() as s:
                out[name] = fn(s, settings=settings, telegram=get_telegram())
        except Exception as exc:  # noqa: BLE001
            log.exception(name)
            out[name] = f"erreur : {exc}"
    try:
        with session_scope() as s:
            out["reviews"] = run_service_cycle(s, telegram=get_telegram(), settings=settings)
    except Exception as exc:  # noqa: BLE001
        log.exception("service cycle")
        out["reviews"] = f"erreur : {exc}"
    try:
        with session_scope() as s:
            out["reminders"] = [e.id for e in send_reminders(s, settings=settings)]
            out["reports"] = [e.id for e in send_weekly_reports(s, settings=settings)]
            out["surveys"] = [e.id for e in send_end_of_trial_surveys(s, settings=settings)]
    except Exception as exc:  # noqa: BLE001
        log.exception("rappels / rapports")
        out["reminders"] = f"erreur : {exc}"
    return out


def main(interval_s: int = 3600) -> None:  # pragma: no cover - boucle infinie
    logging.basicConfig(
        level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    init_db()
    while True:
        log.info("tick : %s", tick())
        time.sleep(interval_s)


if __name__ == "__main__":  # pragma: no cover
    main()
