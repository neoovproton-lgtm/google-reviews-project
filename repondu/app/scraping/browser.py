"""Session Playwright : proxy, rythme lent, retries. Aucune connaissance du DOM Maps."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


def proxy_settings(proxy_url: str | None) -> dict[str, str] | None:
    """'http://user:pass@host:port' → dict attendu par Playwright."""
    if not proxy_url:
        return None
    parts = urlsplit(proxy_url)
    server = f"{parts.scheme or 'http'}://{parts.hostname}"
    if parts.port:
        server += f":{parts.port}"
    out = {"server": server}
    if parts.username:
        out["username"] = parts.username
    if parts.password:
        out["password"] = parts.password
    return out


class Pacer:
    """Attente aléatoire entre deux actions, pour rester sous les radars."""

    def __init__(self, min_s: float, max_s: float):
        self.min_s, self.max_s = min_s, max(min_s, max_s)

    def wait(self, factor: float = 1.0) -> None:
        d = random.uniform(self.min_s, self.max_s) * factor
        if d > 0:
            time.sleep(d)


@contextmanager
def browser_session(settings: Settings | None = None) -> Iterator[tuple[BrowserContext, Pacer]]:
    settings = settings or get_settings()
    with sync_playwright() as pw:
        launch_kwargs: dict = {"headless": settings.headless}
        if settings.chromium_executable:
            launch_kwargs["executable_path"] = settings.chromium_executable
        proxy = proxy_settings(settings.proxy_url)
        if proxy:
            launch_kwargs["proxy"] = proxy
        browser: Browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            locale=settings.browser_locale,
            viewport={"width": 1366, "height": 900},
            user_agent=settings.enrich_user_agent,
        )
        context.set_default_timeout(30_000)
        try:
            yield context, Pacer(settings.scrape_min_delay_s, settings.scrape_max_delay_s)
        finally:
            context.close()
            browser.close()


def goto_with_retry(page: Page, url: str, attempts: int = 3, pacer: Pacer | None = None) -> None:
    last: Exception | None = None
    for i in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return
        except Exception as exc:  # noqa: BLE001 - on veut réessayer sur tout
            last = exc
            log.warning("goto %s échoué (%d/%d): %s", url, i + 1, attempts, exc)
            if pacer:
                pacer.wait(factor=2**i)
    assert last is not None
    raise last
