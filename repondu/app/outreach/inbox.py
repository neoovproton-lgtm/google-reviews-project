"""C4 — Détection des réponses par IMAP sur les boîtes d'envoi : OK → oui, STOP → opt-out."""

from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailEvent, Mailbox, Outreach, OutreachStatus, utcnow
from app.outreach.sequence import normalize_contact, opt_out, record_outcome
from app.replies.checks import normalize
from app.telegram.client import Telegram

log = logging.getLogger(__name__)

QUOTE_START_RE = re.compile(
    r"^(le .{3,80} a écrit ?:|on .{3,80} wrote:|-----\s*original message|de ?:.*envoy[ée] ?:|"
    r"________________________________)",
    re.I,
)
YES_RE = re.compile(
    r"^(ok|oui|d'accord|ca marche|go|parfait|banco|allons-y|top|je suis partant|"
    r"partant|volontiers|avec plaisir|pourquoi pas|c'est ok)\b"
)
STOP_RE = re.compile(
    r"\b(stop|unsubscribe)\b|\bdesinscri|\bdesabonn|plus de message|ne plus (etre|me) contact|"
    r"retirez[- ]moi|supprimez[- ]moi"
)
_ADDR_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class InboundMail:
    uid: str
    from_address: str
    subject: str
    body: str
    message_id: str | None
    in_reply_to: str | None
    date: datetime


def _body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True) or b""
                html = payload.decode(part.get_content_charset() or "utf-8", "replace")
                return re.sub(r"<[^>]+>", " ", html)
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")


def decode_header_value(value) -> str:
    """En-tête décodé (RFC 2047), robuste aux octets UTF-8 bruts non encodés."""
    text = str(value or "")
    try:
        text = text.encode("ascii", "surrogateescape").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    try:
        return str(email.header.make_header(email.header.decode_header(text)))
    except Exception:  # noqa: BLE001
        return text


def _extract_address(header_value) -> str:
    """Adresse dans un en-tête From, robuste aux accents et aux encodages exotiques."""
    text = decode_header_value(header_value)
    _, addr = email.utils.parseaddr(text)
    if not addr or "@" not in addr:
        m = _ADDR_RE.search(text)
        addr = m.group(0) if m else ""
    return addr.lower()


def parse_rfc822(raw: bytes, uid: str = "") -> InboundMail:
    msg = email.message_from_bytes(raw)
    addr = _extract_address(msg.get("From", ""))
    date = None
    if msg.get("Date"):
        try:
            date = email.utils.parsedate_to_datetime(msg["Date"])
            date = date.astimezone(UTC).replace(tzinfo=None) if date.tzinfo else date
        except (TypeError, ValueError):
            date = None
    subject = decode_header_value(msg.get("Subject", ""))
    return InboundMail(
        uid=uid,
        from_address=addr.lower(),
        subject=subject,
        body=_body_text(msg),
        message_id=(msg.get("Message-ID") or "").strip() or None,
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        date=date or utcnow(),
    )


def strip_quotes(body: str) -> str:
    """Garde le texte écrit par l'expéditeur : coupe aux citations et aux lignes '>'."""
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if QUOTE_START_RE.match(stripped):
            break
        lines.append(stripped)
    return "\n".join(ln for ln in lines if ln).strip()


def classify_reply(body: str) -> str:
    """`yes` | `opted_out` | `replied`"""
    own = normalize(strip_quotes(body))
    if not own:
        return "replied"
    if STOP_RE.search(own):
        return "opted_out"
    first = own.splitlines()[0] if own.splitlines() else own
    first = re.sub(r"^[\s\"'«(]+", "", first)
    if YES_RE.match(first) and len(first) <= 80:
        return "yes"
    return "replied"


def match_outreach(session: Session, mail: InboundMail) -> Outreach | None:
    contact = normalize_contact(mail.from_address)
    stmt = select(Outreach).where(Outreach.channel == "email").order_by(Outreach.id.desc())
    for o in session.scalars(stmt):
        if o.contact and normalize_contact(o.contact) == contact:
            return o
    return None


def process_inbound(
    session: Session,
    mail: InboundMail,
    telegram: Telegram | None,
    chat_id: str | None,
    mailbox: Mailbox | None = None,
    now: datetime | None = None,
) -> str:
    """Retourne `yes` | `opted_out` | `replied` | `unknown` | `duplicate`."""
    now = now or utcnow()
    if mail.message_id and session.scalar(
        select(EmailEvent.id).where(
            EmailEvent.external_id == mail.message_id, EmailEvent.source == "imap"
        )
    ):
        return "duplicate"
    o = match_outreach(session, mail)
    if o is None:
        session.add(
            EmailEvent(
                mailbox_id=mailbox.id if mailbox else None,
                type="inbound_unknown",
                source="imap",
                external_id=mail.message_id,
                payload={"from": mail.from_address, "subject": mail.subject},
                occurred_at=now,
            )
        )
        return "unknown"
    kind = classify_reply(mail.body)
    snippet = strip_quotes(mail.body)[:400]
    if kind == "opted_out":
        opt_out(session, mail.from_address, source="imap", now=now)
        if o.status == OutreachStatus.ACTIVE:
            o.status = OutreachStatus.OPTED_OUT
    else:
        record_outcome(session, o, kind, note=snippet, now=now, source="imap")
    session.add(
        EmailEvent(
            mailbox_id=mailbox.id if mailbox else None,
            outreach_id=o.id,
            type=f"inbound_{kind}",
            source="imap",
            external_id=mail.message_id,
            payload={"from": mail.from_address, "subject": mail.subject},
            occurred_at=now,
        )
    )
    if telegram and chat_id:
        label = {"yes": "🎉 OUI", "opted_out": "🚫 STOP", "replied": "💬 Réponse"}[kind]
        text = (
            f"{label} — {o.prospect.name} ({mail.from_address})\nSéquence #{o.id}\n\n"
            f"{snippet or '(vide)'}"
        )
        if kind == "replied":
            text += f"\n\nQualifier : /oui {o.id} · /objection {o.id} <note> · /non {o.id}"
        telegram.send_message(chat_id, text)
    return kind


def _event(mailbox, outreach_id, kind, mail, now) -> EmailEvent:
    return EmailEvent(
        mailbox_id=mailbox.id if mailbox else None,
        outreach_id=outreach_id,
        type=kind,
        source="imap",
        external_id=mail.message_id,
        payload={"from": mail.from_address, "subject": mail.subject},
        occurred_at=now,
    )


# --- IMAP -----------------------------------------------------------------------------------


Fetcher = Callable[[dict], list[tuple[str, bytes]]]


def fetch_unseen_imap(cfg: dict) -> list[tuple[str, bytes]]:
    """Messages non lus de la boîte (host, port, user, password). Marque lus après lecture."""
    conn = imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("port", 993)))
    try:
        conn.login(cfg["user"], cfg["password"])
        conn.select(cfg.get("folder", "INBOX"))
        status, data = conn.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return []
        out = []
        for uid in data[0].split():
            status, msg_data = conn.fetch(uid, "(BODY.PEEK[])")
            if status == "OK" and msg_data and isinstance(msg_data[0], tuple):
                out.append((uid.decode(), msg_data[0][1]))
                conn.store(uid, "+FLAGS", "\\Seen")
        return out
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def poll_inboxes(
    session: Session,
    entries: list[dict],
    telegram: Telegram | None,
    chat_id: str | None,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
) -> dict:
    """Relève chaque boîte de `mailboxes.json` ayant une section `imap`."""
    fetcher = fetcher or fetch_unseen_imap
    now = now or utcnow()
    counts = {
        "boxes": 0,
        "messages": 0,
        "yes": 0,
        "replied": 0,
        "opted_out": 0,
        "unknown": 0,
        "duplicate": 0,
        "errors": 0,
    }
    for entry in entries:
        cfg = entry.get("imap")
        if not cfg:
            continue
        counts["boxes"] += 1
        mailbox = session.scalar(select(Mailbox).where(Mailbox.address == entry["address"].lower()))
        try:
            raw_messages = fetcher(cfg)
        except Exception as exc:  # noqa: BLE001
            log.warning("IMAP %s : %s", entry.get("address"), exc)
            counts["errors"] += 1
            continue
        for uid, raw in raw_messages:
            counts["messages"] += 1
            kind = process_inbound(session, parse_rfc822(raw, uid), telegram, chat_id, mailbox, now)
            counts[kind] = counts.get(kind, 0) + 1
        if mailbox is not None:
            mailbox.last_inbox_check_at = now
    session.flush()
    return counts
