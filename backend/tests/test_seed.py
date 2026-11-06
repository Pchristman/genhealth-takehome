import pytest
from sqlmodel import Session, SQLModel, select

from app.db import make_engine
from app.models import User
from app.security import verify_password
from scripts import seed


@pytest.fixture
def seed_engine(monkeypatch):
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "init_db", lambda: None)
    return engine


def stored_users(engine) -> list[User]:
    with Session(engine) as db:
        return list(db.exec(select(User)))


def set_seed_password(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("SEED_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("SEED_PASSWORD", value)


@pytest.mark.parametrize("value", [None, ""])
def test_generated_password_is_printed(seed_engine, monkeypatch, capsys, value):
    set_seed_password(monkeypatch, value)
    seed.main()
    printed = capsys.readouterr().out.split("Password: ")[-1].strip()
    users = stored_users(seed_engine)
    assert len(users) == 2
    assert all(verify_password(printed, user.password_hash) for user in users)


def test_given_password_is_used_and_not_printed(seed_engine, monkeypatch, capsys):
    set_seed_password(monkeypatch, "a-long-enough-password")
    seed.main()
    assert "a-long-enough-password" not in capsys.readouterr().out
    users = stored_users(seed_engine)
    assert all(verify_password("a-long-enough-password", user.password_hash) for user in users)


def test_short_password_exits_nonzero_without_creating_users(seed_engine, monkeypatch, capsys):
    set_seed_password(monkeypatch, "elevenchars")
    with pytest.raises(SystemExit) as caught:
        seed.main()
    assert caught.value.code not in (0, None)
    assert "elevenchars" not in str(caught.value.code)
    assert "elevenchars" not in capsys.readouterr().out
    assert stored_users(seed_engine) == []
