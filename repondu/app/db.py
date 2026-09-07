"""Engine SQLAlchemy, sessions et initialisation du schéma (SQLite)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        url = get_settings().database_url
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Pour les tests : oublie l'engine courant (après changement de DATABASE_URL)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    with session_scope() as session:
        yield session


def init_db() -> None:
    """Crée les tables manquantes et ajoute les colonnes manquantes (migration légère)."""
    from app import models  # noqa: F401 - enregistre les tables

    engine = get_engine()
    models.Base.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    """Ajoute par ALTER TABLE les colonnes déclarées dans les modèles mais absentes en base.

    Suffisant pour SQLite tant qu'on n'ajoute que des colonnes nullables ou avec défaut.
    """
    from app import models

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in models.Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                ddl += column.type.compile(engine.dialect)
                if column.default is not None and column.default.is_scalar:
                    value = column.default.arg
                    ddl += f" DEFAULT {value!r}" if isinstance(value, str) else f" DEFAULT {value}"
                conn.execute(text(ddl))
