import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_jwt_secret_fails_startup(monkeypatch):
    monkeypatch.delenv("JWT_SECRET")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_short_jwt_secret_fails_without_echoing_it(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "short-secret-value")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert "short-secret-value" not in str(caught.value)
