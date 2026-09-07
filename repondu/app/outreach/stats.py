"""C4 — Entonnoir de prospection : envois, ouvertures, réponses, objections, oui, taux."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import MessageStatus, Outreach, OutreachMessage, OutreachStatus

CHANNELS = ("email", "formulaire", "instagram", "facebook", "sms")


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def funnel(session: Session) -> dict:
    by_status = {
        (ch, st): int(n)
        for ch, st, n in session.execute(
            select(Outreach.channel, Outreach.status, func.count()).group_by(
                Outreach.channel, Outreach.status
            )
        )
    }
    sent_by_step = {
        (ch, step): int(n)
        for ch, step, n in session.execute(
            select(OutreachMessage.channel, OutreachMessage.step, func.count())
            .where(OutreachMessage.status.in_(MessageStatus.SENT_LIKE))
            .group_by(OutreachMessage.channel, OutreachMessage.step)
        )
    }
    delivered = {
        ch: int(n)
        for ch, n in session.execute(
            select(OutreachMessage.channel, func.count())
            .where(OutreachMessage.delivered_at.is_not(None))
            .group_by(OutreachMessage.channel)
        )
    }
    opened = {
        ch: int(n)
        for ch, n in session.execute(
            select(OutreachMessage.channel, func.count())
            .where(OutreachMessage.opened_at.is_not(None))
            .group_by(OutreachMessage.channel)
        )
    }
    pending_manual = {
        ch: int(n)
        for ch, n in session.execute(
            select(OutreachMessage.channel, func.count())
            .where(OutreachMessage.status == MessageStatus.MANUAL_PENDING)
            .group_by(OutreachMessage.channel)
        )
    }

    def block(chs: tuple[str, ...]) -> dict:
        def st(status: str) -> int:
            return sum(by_status.get((c, status), 0) for c in chs)

        def step(n: int) -> int:
            return sum(sent_by_step.get((c, n), 0) for c in chs)

        enrolled = sum(v for (c, _), v in by_status.items() if c in chs)
        s1, s2, s3 = step(1), step(2), step(3)
        replied, yes, objection = (
            st(OutreachStatus.REPLIED),
            st(OutreachStatus.YES),
            st(OutreachStatus.OBJECTION),
        )
        answered = replied + yes + objection
        return {
            "enrolled": enrolled,
            "active": st(OutreachStatus.ACTIVE),
            "sent_step1": s1,
            "sent_step2": s2,
            "sent_step3": s3,
            "delivered": sum(delivered.get(c, 0) for c in chs),
            "opened": sum(opened.get(c, 0) for c in chs),
            "pending_manual": sum(pending_manual.get(c, 0) for c in chs),
            "replied": replied,
            "objection": objection,
            "yes": yes,
            "opted_out": st(OutreachStatus.OPTED_OUT),
            "bounced": st(OutreachStatus.BOUNCED),
            "done_no_reply": st(OutreachStatus.DONE),
            "rates": {
                "delivered_of_sent": _rate(sum(delivered.get(c, 0) for c in chs), s1 + s2 + s3),
                "opened_of_sent": _rate(sum(opened.get(c, 0) for c in chs), s1 + s2 + s3),
                "reply_of_contacted": _rate(answered, s1),
                "yes_of_contacted": _rate(yes, s1),
                "yes_of_replies": _rate(yes, answered),
                "step2_of_step1": _rate(s2, s1),
                "step3_of_step1": _rate(s3, s1),
            },
        }

    return {"total": block(CHANNELS), "by_channel": {c: block((c,)) for c in CHANNELS}}


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:.0f} %"


def format_funnel(data: dict, mailboxes: list[dict] | None = None) -> str:
    t = data["total"]
    r = t["rates"]
    lines = [
        "📊 Entonnoir de prospection",
        f"Enrôlés {t['enrolled']} · actifs {t['active']}",
        f"Contactés J0 {t['sent_step1']} · J+3 {t['sent_step2']} · J+8 {t['sent_step3']}",
        f"Délivrés {t['delivered']} ({_pct(r['delivered_of_sent'])}) · ouverts {t['opened']} "
        f"({_pct(r['opened_of_sent'])})",
        f"Réponses {t['replied'] + t['yes'] + t['objection']} ({_pct(r['reply_of_contacted'])} "
        f"des contactés) · objections {t['objection']} · OUI {t['yes']} "
        f"({_pct(r['yes_of_contacted'])})",
        f"Opt-out {t['opted_out']} · bounces {t['bounced']} · sans réponse {t['done_no_reply']} · "
        f"DM à envoyer {t['pending_manual']}",
    ]
    for ch, b in data["by_channel"].items():
        if b["enrolled"]:
            lines.append(
                f"  {ch} : {b['sent_step1']} contactés, {b['replied'] + b['yes'] + b['objection']} "
                f"réponses, {b['yes']} oui"
            )
    if mailboxes:
        lines.append("📮 Boîtes")
        for mb in mailboxes:
            state = "ok" if mb["active"] else f"COUPÉE ({mb['paused_reason']})"
            lines.append(
                f"  {mb['address']} : {mb['sent_today']}/{mb['quota_today']} aujourd'hui, "
                f"bounce {_pct(mb['bounce_rate'])}, {state}"
            )
    return "\n".join(lines)
