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
    return results


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
