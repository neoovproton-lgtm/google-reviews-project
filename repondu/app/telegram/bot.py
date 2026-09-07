"""Logique du bot : commandes, formulaire d'onboarding (5 questions), boutons de validation."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Establishment, Reply, TelegramSession
from app.replies.service import decide
from app.telegram.client import Telegram

log = logging.getLogger(__name__)

QUESTIONS: list[tuple[str, str]] = [
    ("name", "1/5 — Quel est le nom du restaurant ?"),
    ("cuisine_type", "2/5 — Quel type de cuisine ? (ex : bistrot français, pizzeria, japonais)"),
    ("manager_first_name", "3/5 — Quel est le prénom de la personne qui signe les réponses ?"),
    (
        "tone_signature",
        "4/5 — Quel ton souhaitez-vous, et quelle signature ? "
        "Format : ton ; signature (ex : « chaleureux et direct ; Marc, Chez Marcel »)",
    ),
    (
        "never_say",
        "5/5 — Y a-t-il des sujets à ne jamais évoquer dans une réponse ? "
        "(séparés par des points-virgules, ou « aucun »)",
    ),
]

HELP = (
    "Commandes :\n"
    "/onboard — créer le profil d'un établissement (5 questions)\n"
    "/profils — lister les établissements de cette conversation\n"
    "/annuler — abandonner le formulaire en cours\n"
    "/dm — recevoir le lot du jour de DM Instagram/Facebook à envoyer\n"
    "/envoye <id> — marquer un DM comme envoyé\n"
    "Les réponses proposées arrivent ici avec les boutons Approuver / Refuser."
)


def _chat_allowed(chat_id: str) -> bool:
    allowed = get_settings().telegram_chat_id
    return not allowed or str(allowed) == str(chat_id)


def handle_update(update: dict, session: Session, telegram: Telegram) -> None:
    """Traite une mise à jour Telegram (message ou callback). Ne lève jamais vers le webhook."""
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"], session, telegram)
        elif "message" in update:
            _handle_message(update["message"], session, telegram)
    except Exception:  # noqa: BLE001
        log.exception("update Telegram en erreur : %s", update)


def _handle_message(message: dict, session: Session, telegram: Telegram) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    text = (message.get("text") or "").strip()
    if not chat_id or not text:
        return
    if not _chat_allowed(chat_id):
        telegram.send_message(chat_id, "Conversation non autorisée.")
        return
    state = session.get(TelegramSession, chat_id)
    command = text.split()[0].lower().split("@")[0]
    if command in ("/start", "/aide", "/help"):
        telegram.send_message(chat_id, "Bienvenue sur Répondu.\n\n" + HELP)
    elif command == "/onboard":
        _start_onboarding(chat_id, session, telegram, state)
    elif command == "/annuler":
        if state and state.state:
            state.state = None
            state.answers = {}
            telegram.send_message(chat_id, "Formulaire annulé.")
        else:
            telegram.send_message(chat_id, "Rien à annuler.")
    elif command == "/profils":
        _list_profiles(chat_id, session, telegram)
    elif command == "/dm":
        _deliver_dms(chat_id, session, telegram)
    elif command == "/envoye":
        _mark_dm_sent(chat_id, text, session, telegram)
    elif state and state.state:
        _answer_step(chat_id, text, session, telegram, state)
    else:
        telegram.send_message(chat_id, HELP)


def _start_onboarding(
    chat_id: str, session: Session, telegram: Telegram, state: TelegramSession | None
) -> None:
    if state is None:
        state = TelegramSession(chat_id=chat_id)
        session.add(state)
    state.state = QUESTIONS[0][0]
    state.answers = {}
    telegram.send_message(chat_id, "Création d'un profil établissement.\n\n" + QUESTIONS[0][1])


def _answer_step(
    chat_id: str, text: str, session: Session, telegram: Telegram, state: TelegramSession
) -> None:
    keys = [k for k, _ in QUESTIONS]
    idx = keys.index(state.state) if state.state in keys else -1
    if idx < 0:
        state.state = None
        return
    answers = dict(state.answers or {})
    answers[state.state] = text
    state.answers = answers
    if idx + 1 < len(QUESTIONS):
        state.state = QUESTIONS[idx + 1][0]
        telegram.send_message(chat_id, QUESTIONS[idx + 1][1])
        return
    est = create_establishment_from_answers(session, answers, chat_id)
    state.state = None
    state.answers = {}
    telegram.send_message(chat_id, "Profil enregistré ✅\n\n" + describe(est))


def create_establishment_from_answers(session: Session, a: dict, chat_id: str) -> Establishment:
    tone, signature = _split_tone_signature(a.get("tone_signature", ""))
    manager = a.get("manager_first_name", "").strip() or None
    name = a.get("name", "").strip() or "Établissement"
    never = a.get("never_say", "").strip()
    if never.lower() in ("aucun", "aucune", "non", "rien", "-", ""):
        never = None
    est = Establishment(
        name=name,
        cuisine_type=a.get("cuisine_type", "").strip() or None,
        manager_first_name=manager,
        tone=tone or "chaleureux et professionnel",
        signature=signature or (f"{manager}, {name}" if manager else name),
        never_say=never,
        telegram_chat_id=chat_id,
    )
    session.add(est)
    session.flush()
    return est


def _split_tone_signature(text: str) -> tuple[str | None, str | None]:
    if ";" in text:
        tone, signature = text.split(";", 1)
        return tone.strip() or None, signature.strip() or None
    return text.strip() or None, None


def describe(est: Establishment) -> str:
    return (
        f"#{est.id} {est.name}\n"
        f"Cuisine : {est.cuisine_type or '—'}\n"
        f"Ton : {est.tone}\n"
        f"Signature : {est.signature or '—'}\n"
        f"Gérant : {est.manager_first_name or '—'}\n"
        f"À ne jamais dire : {est.never_say or '—'}"
    )


def _list_profiles(chat_id: str, session: Session, telegram: Telegram) -> None:
    ests = list(
        session.scalars(select(Establishment).where(Establishment.telegram_chat_id == chat_id))
    )
    if not ests:
        telegram.send_message(chat_id, "Aucun profil. Lancez /onboard pour en créer un.")
        return
    telegram.send_message(chat_id, "\n\n".join(describe(e) for e in ests))


def _handle_callback(callback: dict, session: Session, telegram: Telegram) -> None:
    callback_id = str(callback.get("id", ""))
    chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
    data = callback.get("data") or ""
    if not _chat_allowed(chat_id):
        telegram.answer_callback(callback_id, "Non autorisé")
        return
    action, _, raw_id = data.partition(":")
    if action not in ("approve", "reject") or not raw_id.isdigit():
        telegram.answer_callback(callback_id)
        return
    reply = session.get(Reply, int(raw_id))
    if reply is None:
        telegram.answer_callback(callback_id, "Brouillon introuvable")
        return
    who = callback.get("from", {}).get("first_name") or "telegram"
    try:
        decide(session, reply, action, by=f"telegram:{who}")
    except ValueError as exc:
        telegram.answer_callback(callback_id, str(exc))
        return
    label = "approuvée ✅" if action == "approve" else "refusée ❌"
    telegram.answer_callback(callback_id, f"Réponse {label}")
    telegram.send_message(chat_id, f"Réponse #{reply.id} {label}.")


def _deliver_dms(chat_id: str, session: Session, telegram: Telegram) -> None:
    from app.outreach.channels import deliver_dm_batch

    batch = deliver_dm_batch(session, telegram, chat_id, get_settings().outreach_dm_batch)
    if not batch:
        telegram.send_message(chat_id, "Aucun DM en attente.")
    else:
        telegram.send_message(
            chat_id, f"{len(batch)} DM livré(s). Marquez chacun avec /envoye <id>."
        )


def _mark_dm_sent(chat_id: str, text: str, session: Session, telegram: Telegram) -> None:
    from app.outreach.channels import mark_manual_sent

    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        telegram.send_message(chat_id, "Usage : /envoye <id>")
        return
    msg = mark_manual_sent(session, int(parts[1]))
    if msg is None:
        telegram.send_message(chat_id, f"DM #{parts[1]} introuvable ou déjà traité.")
    else:
        telegram.send_message(chat_id, f"DM #{msg.id} marqué envoyé ✅")
