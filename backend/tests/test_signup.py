import pytest
from sqlmodel import select

from app import auth
from app.config import settings
from app.models import User
from tests.conftest import PASSWORD, SIGNUP_CODE

EMAIL = "ada@example.com"
REJECTED = {"detail": "Signup is not available."}


def register(client, **changes):
    body = {"email": EMAIL, "password": PASSWORD, "signup_code": SIGNUP_CODE, **changes}
    return client.post("/auth/register", json={k: v for k, v in body.items() if v is not ...})


def no_users(db) -> bool:
    return db.exec(select(User)).first() is None


def test_correct_code_registers(client):
    assert register(client).status_code == 201


@pytest.mark.parametrize("code", ["wrong-code", "", " ", SIGNUP_CODE + " ", "pässword", "中"])
def test_wrong_code_returns_403_with_the_fixed_message(client, db, code):
    response = register(client, signup_code=code)
    assert response.status_code == 403
    assert response.json() == REJECTED
    assert no_users(db)


def test_non_ascii_configured_code_compares_as_utf8(client, monkeypatch):
    monkeypatch.setattr(settings, "signup_code", "código-de-prueba")
    assert register(client, signup_code="código-de-prueba").status_code == 201


@pytest.mark.parametrize("configured", [None, ""])
@pytest.mark.parametrize("code", ["", SIGNUP_CODE])
def test_signup_is_closed_when_no_code_is_configured(client, db, monkeypatch, configured, code):
    monkeypatch.setattr(settings, "signup_code", configured)
    response = register(client, signup_code=code)
    assert response.status_code == 403
    assert response.json() == REJECTED
    assert no_users(db)


def test_missing_code_field_returns_422(client, db):
    response = register(client, signup_code=...)
    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["detail"]] == [["body", "signup_code"]]
    assert no_users(db)


@pytest.mark.parametrize("code", [None, 12345, ["x"]])
def test_malformed_code_returns_422_not_a_server_error(client, db, code):
    response = register(client, signup_code=code)
    assert response.status_code == 422
    assert no_users(db)


def test_lone_surrogate_code_is_a_wrong_code_not_a_server_error(client, db):
    # Sent raw: the test client itself can't encode a lone surrogate.
    body = '{"email":"ada@example.com","password":"correct-horse-battery","signup_code":"\\ud800"}'
    headers = {"Content-Type": "application/json"}
    response = client.post("/auth/register", content=body, headers=headers)
    assert response.status_code == 403
    assert response.json() == REJECTED
    assert no_users(db)


def test_code_is_checked_before_the_password_is_hashed(client, monkeypatch):
    def fail_if_called(_password):
        raise AssertionError("hashed the password before checking the signup code")

    monkeypatch.setattr(auth, "hash_password", fail_if_called)
    assert register(client, signup_code="wrong-code").status_code == 403


def test_wrong_code_does_not_reveal_whether_an_email_exists(client):
    assert register(client).status_code == 201
    assert register(client, signup_code="wrong-code").status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"signup_code": "submitted-code"},
        {"signup_code": "submitted-code", "password": "too-short"},
        {"signup_code": "submitted-code", "email": "not-an-email"},
        {"signup_code": ["submitted-code"]},
    ],
)
def test_responses_never_echo_either_code(client, changes):
    response = register(client, **changes)
    assert response.status_code in (403, 422)
    assert "submitted-code" not in response.text
    assert SIGNUP_CODE not in response.text


def test_bad_type_message_on_the_code_field_is_the_fixed_one(client):
    response = register(client, signup_code=["submitted-code"])
    assert [error["msg"] for error in response.json()["detail"]] == ["Invalid value."]


def test_login_does_not_need_the_code(client, monkeypatch):
    assert register(client).status_code == 201
    monkeypatch.setattr(settings, "signup_code", None)
    login = client.post("/auth/token", data={"username": EMAIL, "password": PASSWORD})
    assert login.status_code == 200
