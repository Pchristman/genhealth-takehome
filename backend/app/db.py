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
        engine = create_engine(url, connect_args=args)
    event.listen(engine, "connect", _configure_sqlite)
    return engine


engine = make_engine(settings.database_url)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
