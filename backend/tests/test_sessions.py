"""The real get_session, on a file database, when a COMMIT fails."""

import sqlite3

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select

from app import db as app_db
from app.db import get_session, make_engine
from app.main import app
from app.models import Order, User
from tests.conftest import PASSWORD, SIGNUP_CODE
from tests.test_orders import ORDER

GHOST = "ghost@example.com"


@pytest.fixture
def real_session(client, tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    engine = make_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(app_db, "engine", engine)
    del app.dependency_overrides[get_session]
    yield engine, path
    engine.dispose()


def fail_next_commit(monkeypatch, engine) -> None:
    """A commit failure that isn't an IntegrityError, so nothing in the route handles it."""
    real_commit = engine.dialect.do_commit

    def fail_once(connection):
        engine.dialect.do_commit = real_commit
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(engine.dialect, "do_commit", fail_once)


def register(client, email):
    body = {"email": email, "password": PASSWORD, "signup_code": SIGNUP_CODE}
    return client.post("/auth/register", json=body)


def emails(engine) -> list[str]:
    # Through the pool, so a transaction left open on a pooled connection would show here.
    with Session(engine) as session:
        return sorted(user.email for user in session.exec(select(User)))


def write_lock_is_free(path) -> bool:
    connection = sqlite3.connect(path, timeout=1)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()


def test_failed_register_commit_leaves_no_ghost_user(client, real_session, monkeypatch):
    engine, path = real_session
    fail_next_commit(monkeypatch, engine)

    assert register(client, GHOST).status_code == 500

    assert write_lock_is_free(path)
    assert emails(engine) == []
    # The next commit on that pooled connection must not carry the ghost along with it.
    assert register(client, "next@example.com").status_code == 201
    assert emails(engine) == ["next@example.com"]
    assert register(client, GHOST).status_code == 201


def test_failed_order_commit_leaves_nothing_for_the_next_request(
    client, real_session, auth_headers, monkeypatch
):
    engine, path = real_session
    headers = auth_headers()
    fail_next_commit(monkeypatch, engine)

    assert client.post("/v1/orders", json=ORDER, headers=headers).status_code == 500

    assert write_lock_is_free(path)
    assert register(client, "next@example.com").status_code == 201
    with Session(engine) as session:
        assert session.exec(select(Order)).first() is None


def test_file_sqlite_pool_has_room_for_every_worker_thread(tmp_path):
    # Every sync route holds a connection and up to 40 run at once. The default pool stops at 15.
    pool = make_engine(f"sqlite:///{tmp_path / 'pool.db'}").pool
    assert pool.size() == 50
    assert pool._max_overflow == 0


def test_in_memory_sqlite_keeps_its_single_shared_connection():
    assert isinstance(make_engine("sqlite://").pool, StaticPool)
