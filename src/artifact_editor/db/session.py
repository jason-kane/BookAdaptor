"""Lazy, process-local engines and short-lived transactional sessions."""
from contextlib import contextmanager
import os
from threading import RLock

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

_engines = {}
_pid = os.getpid()
_lock = RLock()


def _after_fork():
    global _lock, _pid
    _lock = RLock()
    for engine in _engines.values():
        engine.dispose(close=False)
    _engines.clear()
    _pid = os.getpid()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


def _postgres_url(value):
    url = make_url(value)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    if url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg driver")
    return url


def database_url():
    configured = os.environ.get("DATABASE_URL")
    if configured:
        return _postgres_url(configured)
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("PGUSER", "bookadaptor"),
        password=os.environ.get("PGPASSWORD", "bookadaptor"),
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "bookadaptor"),
    )


def get_engine(url=None):
    global _pid
    selected = _postgres_url(url) if url is not None else database_url()
    with _lock:
        if _pid != os.getpid():
            # Forked CPU workers must not reuse their parent's DB connections.
            for engine in _engines.values():
                engine.dispose(close=False)
            _engines.clear()
            _pid = os.getpid()
        if selected not in _engines:
            _engines[selected] = create_engine(selected, pool_pre_ping=True, pool_size=2,
                                               max_overflow=3, connect_args={"connect_timeout": 5})
        return _engines[selected]


@contextmanager
def session_scope(*, engine=None):
    """Commit on success, roll back on failure, and always release the connection."""
    with Session(engine or get_engine(), expire_on_commit=False) as session:
        with session.begin():
            yield session
