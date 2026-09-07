"""Choix du canal prioritaire : un seul canal par prospect."""

from __future__ import annotations

WRITTEN_CHANNELS = ("email", "formulaire", "instagram", "facebook", "sms")


def choose_channel(
    email: str | None,
    contact_form_url: str | None,
    instagram: str | None,
    facebook: str | None,
    mobile_phone: str | None,
    phone: str | None,
) -> str | None:
    """Ordre : email > formulaire > instagram > facebook > sms (mobile) > telephone (fixe)."""
    if email:
        return "email"
    if contact_form_url:
        return "formulaire"
    if instagram:
        return "instagram"
    if facebook:
        return "facebook"
    if mobile_phone:
        return "sms"
    if phone:
        return "telephone"
    return None
