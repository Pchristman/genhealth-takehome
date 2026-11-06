import pytest

from app.security import hash_password, verify_password


@pytest.mark.parametrize("stored", ["nodollar", "zz$zz", "aa$é"])
def test_malformed_stored_hash_does_not_verify(stored):
    assert verify_password("any-password-here", stored) is False


def test_well_formed_hash_still_verifies():
    stored = hash_password("any-password-here")
    assert verify_password("any-password-here", stored) is True
    assert verify_password("another-password", stored) is False
