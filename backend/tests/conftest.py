import os

# Set before the app imports settings, so tests never read a real .env or spend real credit.
os.environ["JWT_SECRET"] = "test-only-jwt-signing-key-0123456789abcdef"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["DATABASE_URL"] = "sqlite://"
SIGNUP_CODE = "test-signup-code"
os.environ["SIGNUP_CODE"] = SIGNUP_CODE

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

from app.config import settings  # noqa: E402

# Blank counts as unset (env_ignore_empty), so the blank variable above would let a real
# .env key through to the settings. The variable stays blank for the Anthropic SDK.
settings.anthropic_api_key = None
settings.signup_code = SIGNUP_CODE

from app.db import get_session, make_engine, session_scope  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "correct-horse-battery"


@pytest.fixture
def db():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(db):
    app.dependency_overrides[get_session] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.fixture
def engine(client, tmp_path):
    """A file database and one session per request, as in production.

    The shared `db` fixture can't prove what persisted: its single connection sees its own
    uncommitted rows, and a test has to roll it back by hand. Here each request runs alone.
    """
    file_engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(file_engine)

    def session_per_request():
        yield from session_scope(file_engine)

    app.dependency_overrides[get_session] = session_per_request
    yield file_engine
    file_engine.dispose()


@pytest.fixture
def auth_headers(client):
    def make(email: str = "ada@example.com") -> dict[str, str]:
        credentials = {"email": email, "password": PASSWORD, "signup_code": SIGNUP_CODE}
        client.post("/auth/register", json=credentials)
        response = client.post("/auth/token", data={"username": email, "password": PASSWORD})
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return make
