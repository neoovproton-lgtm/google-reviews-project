"""C3 — Remplissage d'un formulaire de contact avec Playwright (heuristiques de champs)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from playwright.sync_api import Locator, Page

from app.config import Settings, get_settings
from app.scraping.browser import browser_session, goto_with_retry
from app.scraping.maps import dismiss_consent

log = logging.getLogger(__name__)

FIELD_PATTERNS = {
    "name": re.compile(r"nom|name|prenom|prénom|fullname|full_name", re.I),
    "email": re.compile(r"mail", re.I),
    "phone": re.compile(r"tel|phone|mobile", re.I),
    "subject": re.compile(r"sujet|subject|objet", re.I),
    "company": re.compile(r"societe|société|company|entreprise|restaurant|etablissement", re.I),
}
SUCCESS_RE = re.compile(r"merci|envoy[ée]|succ[eè]s|thank|bien re[çc]u|transmis", re.I)


@dataclass
class FormResult:
    ok: bool
    detail: str
    verified: bool = False


def _describe(el: Locator) -> str:
    parts = []
    for attr in ("name", "id", "placeholder", "aria-label", "type", "autocomplete"):
        try:
            v = el.get_attribute(attr)
        except Exception:  # noqa: BLE001
            v = None
        if v:
            parts.append(v)
    try:
        el_id = el.get_attribute("id")
        if el_id:
            label = el.page.locator(f'label[for="{el_id}"]').first
            if label.count():
                parts.append(label.inner_text())
    except Exception:  # noqa: BLE001
        pass
    return " ".join(parts)


def pick_form(page: Page) -> Locator | None:
    forms = page.locator("form")
    for i in range(forms.count()):
        f = forms.nth(i)
        if f.locator("textarea").count():
            return f
    return None


def fill_form(
    form: Locator, name: str, email: str, phone: str | None, subject: str, message: str
) -> list[str]:
    """Remplit les champs reconnus ; retourne la liste des champs remplis."""
    filled: list[str] = []
    inputs = form.locator(
        "input:not([type=hidden]):not([type=submit]):not([type=checkbox]):not([type=radio]):not([type=file])"
    )
    for i in range(inputs.count()):
        el = inputs.nth(i)
        desc = _describe(el)
        itype = (el.get_attribute("type") or "text").lower()
        value = None
        if itype == "email" or FIELD_PATTERNS["email"].search(desc):
            value, key = email, "email"
        elif itype == "tel" or FIELD_PATTERNS["phone"].search(desc):
            value, key = phone, "phone"
        elif FIELD_PATTERNS["subject"].search(desc):
            value, key = subject, "subject"
        elif FIELD_PATTERNS["company"].search(desc):
            value, key = "Répondu", "company"
        elif FIELD_PATTERNS["name"].search(desc) or itype == "text" and "name" not in filled:
            value, key = name, "name"
        else:
            continue
        if value and key not in filled:
            el.fill(value)
            filled.append(key)
    textarea = form.locator("textarea").first
    if textarea.count():
        textarea.fill(message)
        filled.append("message")
    # Cases à cocher obligatoires (consentement) : on coche
    boxes = form.locator("input[type=checkbox][required]")
    for i in range(boxes.count()):
        boxes.nth(i).check()
        filled.append("consent")
    return filled


def submit_form(page: Page, form: Locator) -> FormResult:
    before_url = page.url
    button = form.locator("button[type=submit], input[type=submit], button:not([type])").first
    if not button.count():
        return FormResult(False, "pas de bouton d'envoi")
    button.click()
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:  # noqa: BLE001
        pass
    text = page.inner_text("body")[:5000] if page.locator("body").count() else ""
    if SUCCESS_RE.search(text) or page.url != before_url:
        return FormResult(True, "envoyé", verified=bool(SUCCESS_RE.search(text)))
    if form.count() and form.locator("textarea").count():
        try:
            if form.locator("textarea").first.input_value().strip():
                return FormResult(False, "le formulaire n'a pas été vidé après envoi")
        except Exception:  # noqa: BLE001
            pass
    return FormResult(True, "soumis, sans confirmation visible", verified=False)


def submit_contact_form(
    url: str,
    name: str,
    email: str,
    phone: str | None,
    subject: str,
    message: str,
    settings: Settings | None = None,
) -> FormResult:
    settings = settings or get_settings()
    with browser_session(settings) as (context, pacer):
        page = context.new_page()
        try:
            goto_with_retry(page, url, attempts=2, pacer=pacer)
            dismiss_consent(page)
            form = pick_form(page)
            if form is None:
                return FormResult(False, "aucun formulaire avec message trouvé")
            filled = fill_form(form, name, email, phone, subject, message)
            if "message" not in filled:
                return FormResult(False, "champ message introuvable")
            pacer.wait(factor=0.5)
            result = submit_form(page, form)
            result.detail += f" (champs : {', '.join(filled)})"
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("formulaire %s : %s", url, exc)
            return FormResult(False, f"{type(exc).__name__}: {exc}"[:300])
        finally:
            page.close()
