import pytest
from pydantic import ValidationError

from app.config import Settings, settings


def test_missing_jwt_secret_fails_startup(monkeypatch):
    monkeypatch.delenv("JWT_SECRET")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_short_jwt_secret_fails_without_echoing_it(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "short-secret-value")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert "short-secret-value" not in str(caught.value)


def test_blank_jwt_secret_fails_as_missing(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert [error["type"] for error in caught.value.errors()] == ["missing"]


def test_blank_jwt_secret_in_env_file_fails_as_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("JWT_SECRET")
    env_file = tmp_path / ".env"
    env_file.write_text("JWT_SECRET=\n")
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=env_file)
    assert [error["type"] for error in caught.value.errors()] == ["missing"]


def test_blank_setting_falls_back_to_its_default(monkeypatch):
    monkeypatch.setenv("JWT_TTL_MINUTES", "")
    assert Settings(_env_file=None).jwt_ttl_minutes == 60


def test_tests_never_see_an_api_key():
    # A bool, so a failure can't print the key from a developer's real .env.
    key_is_set = bool(settings.anthropic_api_key)
    assert not key_is_set


def test_log_level_is_normalized_to_upper(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings(_env_file=None).log_level == "DEBUG"


@pytest.mark.parametrize("ttl", ["0", "-5"])
def test_non_positive_jwt_ttl_fails(monkeypatch, ttl):
    monkeypatch.setenv("JWT_TTL_MINUTES", ttl)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("secret", [" " * 32, "a" * 32, "abcd" * 10])
def test_worthless_jwt_secret_fails_without_echoing_it(monkeypatch, secret):
    monkeypatch.setenv("JWT_SECRET", secret)
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)
    assert secret not in str(caught.value)
