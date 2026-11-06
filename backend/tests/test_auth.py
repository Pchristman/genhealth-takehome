from datetime import UTC, datetime, timedelta

import jwt
from sqlmodel import select

from app.config import settings
from app.models import User
from app.security import create_token
from tests.conftest import PASSWORD

EMAIL = "ada@example.com"


def register(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/register", json={"email": email, "password": password})


def login(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/token", data={"username": email, "password": password})


def test_register_then_token_then_me(client):
    assert register(client).status_code == 201
    token = login(client).json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert "password_hash" not in response.json()


def test_password_is_never_stored_plain(client, db):
    register(client)
    user = db.exec(select(User)).one()
    assert PASSWORD not in user.password_hash


def test_duplicate_email_returns_409(client):
    register(client)
    assert register(client).status_code == 409


def test_email_is_case_insensitive(client):
    assert register(client, "Ada@Example.com").status_code == 201
    assert register(client, "ada@example.com").status_code == 409
    assert login(client, "ADA@example.com").status_code == 200


def test_short_password_returns_422(client):
    response = register(client, password="too-short-1")
    assert response.status_code == 422
    assert "too-short-1" not in response.text


def test_bad_password_returns_401(client):
    register(client)
    assert login(client, password="wrong-password-here").status_code == 401


def test_unknown_email_returns_401(client):
    assert login(client, "nobody@example.com").status_code == 401


def test_expired_token_returns_401(client):
    register(client)
    expired = jwt.encode(
        {"sub": "anyone", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401


def test_token_with_wrong_signature_returns_401(client):
    forged = jwt.encode({"sub": "anyone"}, "another-secret-" + "y" * 32, algorithm="HS256")
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_token_for_missing_user_returns_401(client):
    token = create_token("user-that-does-not-exist")
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_me_without_token_returns_401(client):
    assert client.get("/auth/me").status_code == 401


def test_health_is_public(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_every_response_has_a_request_id(client):
    assert client.get("/health").headers["X-Request-ID"]
