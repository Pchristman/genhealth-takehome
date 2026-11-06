from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings


def _configure_sqlite(connection, _record) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def make_engine(url: str) -> Engine:
    args = {"check_same_thread": False}
    if url == "sqlite://":  # in-memory: one shared connection, or each session sees an empty DB
        engine = create_engine(url, connect_args=args, poolclass=StaticPool)
    else:
        # A sync route holds a connection throughout, and up to 40 of them run at once.
        engine = create_engine(url, connect_args=args, pool_size=50, max_overflow=0)
    event.listen(engine, "connect", _configure_sqlite)
    return engine


engine = make_engine(settings.database_url)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def session_scope(bind: Engine) -> Iterator[Session]:
    """One session for one request. Closing it is not enough after a failed COMMIT.

    SQLAlchemy would return the connection to the pool with the transaction still open, and the
    next request to get that connection would inherit, and could commit, the failed rows.
    """
    with Session(bind) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    yield from session_scope(engine)
