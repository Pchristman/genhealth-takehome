from typing import Annotated

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

from app.main import validation_error

SENSITIVE = "Jane Doe 1980-01-02"


def custom_error(value: str) -> str:
    raise PydanticCustomError("patient_check", "rejected {value}", {"value": value})


def failed_assertion(value: str) -> str:
    raise AssertionError(f"rejected {value}")


def value_error(value: str) -> str:
    raise ValueError(f"rejected {value}")


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=5)
    custom: Annotated[str, AfterValidator(custom_error)] = "unset"
    asserted: Annotated[str, AfterValidator(failed_assertion)] = "unset"
    plain: Annotated[str, AfterValidator(value_error)] = "unset"
    count: int = 0


@pytest.fixture
def scratch():
    # A separate app, so the throwaway route never reaches the real one.
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_error)

    @app.post("/check")
    def check(body: Body) -> None:
        return None

    return TestClient(app)


def errors_for(scratch, payload: dict) -> list[dict]:
    response = scratch.post("/check", json={"name": "abc", **payload})
    assert response.status_code == 422
    return response.json()["detail"]


@pytest.mark.parametrize("field", ["custom", "asserted", "plain"])
def test_message_quoting_the_input_is_replaced(scratch, field):
    response = scratch.post("/check", json={"name": "abc", field: SENSITIVE})
    assert response.status_code == 422
    assert SENSITIVE not in response.text
    assert [error["msg"] for error in response.json()["detail"]] == ["Invalid value."]


def test_unlisted_builtin_type_gets_the_fixed_message(scratch):
    [error] = errors_for(scratch, {"count": "not-a-number"})
    assert error["type"] == "int_parsing"
    assert error["msg"] == "Invalid value."


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"name": "ab"}, "string_too_short"),
        ({"name": "abcdef"}, "string_too_long"),
        ({"surprise": 1}, "extra_forbidden"),
    ],
)
def test_allowlisted_types_keep_their_message(scratch, payload, expected_type):
    [error] = errors_for(scratch, payload)
    assert error["type"] == expected_type
    assert error["msg"] != "Invalid value."


def test_missing_field_keeps_its_message(scratch):
    [error] = scratch.post("/check", json={}).json()["detail"]
    assert error["type"] == "missing"
    assert error["msg"] != "Invalid value."


def test_invalid_json_keeps_its_message(scratch):
    response = scratch.post("/check", content="{", headers={"Content-Type": "application/json"})
    [error] = response.json()["detail"]
    assert error["type"] == "json_invalid"
    assert error["msg"] != "Invalid value."
