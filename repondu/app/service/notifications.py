"""D2 — Notifications « nouvel avis » de Google reçues sur le compte gestionnaire (IMAP)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Establishment
from app.outreach.inbox import InboundMail
from app.replies.checks import normalize

log = logging.getLogger(__name__)

GOOGLE_SENDER_RE = re.compile(r"@google\.com$|@googlemail\.com$", re.I)
SUBJECT_RE = re.compile(
    r"(nouvel avis|new review|a laissé un avis|left a review|vous a attribué|gave you)", re.I
)
RATING_RE = re.compile(r"(\d)\s*(?:étoiles?|stars?|/\s*5|★)", re.I)
STARS_RE = re.compile(r"★{1,5}")
NAME_PATTERNS = (
    re.compile(
        r"(?:nouvel avis|new review)\s+(?:sur|pour|for|on)\s+[«\"]?([^»\"\n]+?)[»\"]?\s*$", re.I
    ),
    re.compile(r"avis\s+(?:sur|pour)\s+[«\"]?([^»\"\n]+?)[»\"]?\s*(?:[:\-–]|$)", re.I),
)
AUTHOR_RE = re.compile(
    r"^([A-ZÀ-Ý][\w'\- .]{1,60}?)\s+(?:a laissé|a attribué|vous a|left|gave)", re.I | re.M
)


@dataclass
class ReviewNotification:
    establishment_name: str | None
    author: str | None
    rating: int | None
    snippet: str | None
    message_id: str | None


def is_google_review_notification(mail: InboundMail) -> bool:
    return bool(GOOGLE_SENDER_RE.search(mail.from_address)) and bool(
        SUBJECT_RE.search(mail.subject or "") or SUBJECT_RE.search(mail.body[:500])
    )


def parse_notification(mail: InboundMail) -> ReviewNotification | None:
    if not is_google_review_notification(mail):
        return None
    text = re.sub(r"\s+", " ", mail.body).strip()
    name = None
    for pat in NAME_PATTERNS:
        m = pat.search(mail.subject or "") or pat.search(text[:400])
        if m:
            name = m.group(1).strip(" .:-")
            break
    rating = None
    m = RATING_RE.search(mail.subject or "") or RATING_RE.search(text[:600])
    if m:
        rating = int(m.group(1))
    else:
        m = STARS_RE.search(mail.subject or "") or STARS_RE.search(text[:600])
        if m:
            rating = len(m.group(0))
    author = None
    m = AUTHOR_RE.search(mail.body)
    if m:
        author = m.group(1).strip()
    snippet = None
    m = re.search(r"[«\"]([^»\"]{10,400})[»\"]", text)
    if m:
        snippet = m.group(1).strip()
    return ReviewNotification(name, author, rating, snippet, mail.message_id)


def match_establishment(session: Session, name: str | None) -> Establishment | None:
    if not name:
        return None
    target = normalize(name)
    best = None
    for est in session.scalars(select(Establishment).where(Establishment.active == 1)):
        n = normalize(est.name)
        if n == target:
            return est
        if (
            n
            and (n in target or target in n)
            and (best is None or len(n) > len(normalize(best.name)))
        ):
            best = est
    return best
