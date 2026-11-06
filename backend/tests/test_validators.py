from datetime import date

import pytest

from app.extraction_types import FlagCode
from app.validators import check_dob, is_placeholder, is_valid_npi, parse_dob

TODAY = date(2026, 11, 6)

ORDER = {
    "patient_first_name": "Jane",
    "patient_last_name": "Doe",
    "patient_dob": "1961-02-17",
    "provider_name": "Dana Smith MD",
    "provider_npi": "1234567893",
}
BAD_NPI = "9182734556"
FUTURE_DOB = "2999-01-01"
ANCIENT_DOB = "1900-12-05"
PRINTED_PLACEHOLDERS = [
    "TBD",
    "see above",
    "same as above",
    "Same",
    "Pending",
    "refused",
    "declined",
    "not on file",
    "not listed",
    "unavailable",
    "see attached",
    "illegible",
    "blank",
    "not given",
    "no name",
    "unknown patient",
    "patient",
    "N-A",
    "N\\A",
    "(N/A)",
    "N/A,",
    "Unknown,",
    "[unknown]",
    "--none--",
    "N\u200b/A",
    "N\u2044A",
    "\uff2e\uff0f\uff21",
    "xxxxx",
    "X",
]
REAL_NAMES = ["Marie", "Curie", "Noah", "Nguyen", "O'Brien", "Li"]


def test_npi_accepts_valid_checksum():
    assert is_valid_npi("1234567893")


@pytest.mark.parametrize("printed", ["1234 567 893", "1234-567-893", " 1234567893 "])
def test_npi_ignores_spaces_and_hyphens(printed):
    assert is_valid_npi(printed)


@pytest.mark.parametrize("npi", ["1234567893", "1245319599", "2000000002"])
def test_npi_fixtures_stay_valid(npi):
    assert is_valid_npi(npi)


@pytest.mark.parametrize("npi", ["0000000006", "3000000000", "9999999995"])
def test_npi_must_start_with_1_or_2_even_when_luhn_passes(npi):
    assert not is_valid_npi(npi)


def test_npi_rejects_sample_value():
    assert not is_valid_npi(BAD_NPI)


@pytest.mark.parametrize(
    "value",
    ["", "123456789", "12345678930", "123456789X", "abcdefghij", "١٢٣٤٥٦٧٨٩٣", "1٢٣٤٥٦٧٨٩٣"],
)
def test_npi_rejects_wrong_length_and_letters(value):
    assert not is_valid_npi(value)


def test_parse_dob_is_month_first():
    assert parse_dob("12/05/1900", TODAY) == date(1900, 12, 5)


def test_parse_dob_requires_today():
    with pytest.raises(TypeError):
        parse_dob("12/05/60")


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("02/17/1961", date(1961, 2, 17)),
        ("2/7/1961", date(1961, 2, 7)),
        ("02-17-1961", date(1961, 2, 17)),
        ("02/17/61", date(1961, 2, 17)),
        ("1961-02-17", date(1961, 2, 17)),
        ("February 17, 1961", date(1961, 2, 17)),
        (" 02/17/1961 ", date(1961, 2, 17)),
    ],
)
def test_parse_dob_formats(printed, expected):
    assert parse_dob(printed, TODAY) == expected


def test_parse_dob_two_digit_year_in_the_future_moves_back_a_century():
    assert parse_dob("02/17/50", TODAY) == date(1950, 2, 17)
    assert parse_dob("11/07/26", TODAY) == date(1926, 11, 7)


def test_parse_dob_two_digit_year_on_today_stays():
    assert parse_dob("11/06/26", TODAY) == TODAY


def test_parse_dob_uses_the_injected_today_not_the_clock():
    assert parse_dob("02/17/50", date(2060, 1, 1)) == date(2050, 2, 17)


@pytest.mark.parametrize("printed", ["", "soon", "13/05/1990", "02/30/1961", "1961"])
def test_parse_dob_unparseable_is_none(printed):
    assert parse_dob(printed, TODAY) is None


def test_dob_future_and_over_120_years_flagged():
    assert check_dob(date(2026, 11, 7), TODAY) == FlagCode.implausible_dob
    assert check_dob(date(1906, 11, 5), TODAY) == FlagCode.implausible_dob
    assert check_dob(date(1900, 12, 5), TODAY) == FlagCode.implausible_dob


def test_dob_boundaries_pass():
    assert check_dob(date(1906, 11, 6), TODAY) is None
    assert check_dob(TODAY, TODAY) is None
    assert check_dob(date(1961, 2, 17), TODAY) is None


def test_dob_check_survives_a_leap_day_today():
    leap_day = date(2020, 2, 29)  # 120 years earlier, 1900, has no Feb 29
    assert check_dob(date(1900, 3, 1), leap_day) is None
    assert check_dob(date(1900, 2, 28), leap_day) == FlagCode.implausible_dob


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "N/A", "n/a", "N / A", "n.a.", "NA", "unknown", "UNKNOWN", "none", "-", "--"],
)
def test_is_placeholder_true(value):
    assert is_placeholder(value)


@pytest.mark.parametrize("value", PRINTED_PLACEHOLDERS)
def test_is_placeholder_catches_printed_stand_ins(value):
    assert is_placeholder(value)


@pytest.mark.parametrize("value", ["J", "J.", "\uff2e\uff21"])
def test_is_placeholder_single_character_and_nfkc(value):
    assert is_placeholder(value)


@pytest.mark.parametrize(
    "value", ["N_A", "(see above)", "[not on file]", "see\u200babove", "-- unknown patient --"]
)
def test_is_placeholder_reads_through_punctuation(value):
    assert is_placeholder(value)


@pytest.mark.parametrize("value", ["Jane", "Nate", "Noneman", "1234567893", "12/05/1900"])
def test_is_placeholder_false(value):
    assert not is_placeholder(value)


@pytest.mark.parametrize("value", REAL_NAMES)
def test_is_placeholder_leaves_real_names_alone(value):
    assert not is_placeholder(value)


def test_manual_order_bad_npi_returns_422(client, auth_headers):
    response = client.post(
        "/v1/orders", json={**ORDER, "provider_npi": BAD_NPI}, headers=auth_headers()
    )
    assert response.status_code == 422
    assert BAD_NPI not in response.text


NAME_FIELDS = ["patient_first_name", "patient_last_name", "provider_name"]


@pytest.mark.parametrize("name", NAME_FIELDS)
@pytest.mark.parametrize("value", ["TBD", "see attached", "N\u200b/A"])
def test_manual_order_placeholder_name_returns_422(client, auth_headers, name, value):
    response = client.post("/v1/orders", json={**ORDER, name: value}, headers=auth_headers())
    assert response.status_code == 422
    assert value not in response.text


@pytest.mark.parametrize("name", NAME_FIELDS)
def test_patch_rejects_placeholder_name_without_echoing(client, auth_headers, name):
    headers = auth_headers()
    order_id = client.post("/v1/orders", json=ORDER, headers=headers).json()["id"]

    response = client.patch(f"/v1/orders/{order_id}", json={name: "TBD"}, headers=headers)

    assert response.status_code == 422
    assert "TBD" not in response.text
    assert client.get(f"/v1/orders/{order_id}", headers=headers).json()[name] == ORDER[name]


@pytest.mark.parametrize("name", NAME_FIELDS)
def test_manual_order_accepts_real_names(client, auth_headers, name):
    response = client.post("/v1/orders", json={**ORDER, name: "Li"}, headers=auth_headers())
    assert response.status_code == 201
    assert response.json()[name] == "Li"


def test_manual_order_keeps_npi_as_entered(client, auth_headers):
    response = client.post(
        "/v1/orders", json={**ORDER, "provider_npi": "1234 567 893"}, headers=auth_headers()
    )
    assert response.status_code == 201
    assert response.json()["provider_npi"] == "1234 567 893"


@pytest.mark.parametrize("dob", [FUTURE_DOB, ANCIENT_DOB])
def test_manual_order_implausible_dob_returns_422(client, auth_headers, dob):
    response = client.post("/v1/orders", json={**ORDER, "patient_dob": dob}, headers=auth_headers())
    assert response.status_code == 422
    assert dob not in response.text


@pytest.mark.parametrize("dob", [0, 1700000000, 1.5, True, "0", "1700000000", ["1961-02-17"]])
def test_manual_order_rejects_dob_that_is_not_a_date_string(client, auth_headers, dob):
    response = client.post("/v1/orders", json={**ORDER, "patient_dob": dob}, headers=auth_headers())
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_npi", BAD_NPI),
        ("patient_dob", FUTURE_DOB),
        ("patient_dob", ANCIENT_DOB),
        ("patient_dob", 0),
    ],
)
def test_patch_rejects_bad_npi_and_dob_without_echoing(client, auth_headers, field, value):
    headers = auth_headers()
    order_id = client.post("/v1/orders", json=ORDER, headers=headers).json()["id"]

    response = client.patch(f"/v1/orders/{order_id}", json={field: value}, headers=headers)

    assert response.status_code == 422
    assert str(value) not in response.text
    assert client.get(f"/v1/orders/{order_id}", headers=headers).json()[field] == ORDER[field]


def test_patch_accepts_good_npi_and_dob(client, auth_headers):
    headers = auth_headers()
    order_id = client.post("/v1/orders", json=ORDER, headers=headers).json()["id"]

    response = client.patch(
        f"/v1/orders/{order_id}",
        json={"provider_npi": "1234-567-893", "patient_dob": "1970-01-02"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["provider_npi"] == "1234-567-893"
    assert response.json()["patient_dob"] == "1970-01-02"
