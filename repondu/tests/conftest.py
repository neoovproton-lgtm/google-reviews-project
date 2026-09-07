"""Fixtures communes : base SQLite temporaire, serveur HTTP local pour les fixtures HTML."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Chaque test a sa propre base et des réglages rapides. Chromium local si disponible."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCRAPE_MIN_DELAY_S", "0")
    monkeypatch.setenv("SCRAPE_MAX_DELAY_S", "0")
    monkeypatch.setenv("PROXY_URL", "")
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if os.path.exists("/opt/pw-browsers/chromium") and not os.environ.get("CHROMIUM_EXECUTABLE"):
        monkeypatch.setenv("CHROMIUM_EXECUTABLE", "/opt/pw-browsers/chromium")
    from app.config import reset_settings
    from app.db import reset_engine

    reset_settings()
    reset_engine()
    yield
    reset_engine()
    reset_settings()


class _FixtureHandler(SimpleHTTPRequestHandler):
    """Sert tests/fixtures/ ; les URLs Google Maps sont mappées sur des fichiers fixes."""

    ROUTES = {
        "/maps/search/": "maps_search.html",
        "/maps/place/": "maps_place.html",
    }

    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0]
        for prefix, filename in self.ROUTES.items():
            if clean.startswith(prefix):
                return str(FIXTURES / filename)
        return super().translate_path(path)

    def log_message(self, *_args) -> None:  # silence
        pass


@pytest.fixture(scope="session")
def fixture_server() -> Iterator[str]:
    handler = partial(_FixtureHandler, directory=str(FIXTURES))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def db():
    from app.db import init_db, session_scope

    init_db()
    return session_scope
