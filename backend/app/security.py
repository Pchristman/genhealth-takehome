import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import jwt

from app.config import settings

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
ALGORITHM = "HS256"


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"{salt.hex()}${_derive(password, salt).hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        return hmac.compare_digest(_derive(password, bytes.fromhex(salt_hex)).hex(), digest_hex)
    except (ValueError, TypeError):  # a malformed stored hash, not a server error
        return False


# Verified against when the email is unknown, so timing doesn't reveal which emails exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def create_token(user_id: str) -> str:
    now = datetime.now(UTC)
    claims = {"sub": user_id, "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def read_token(token: str) -> str | None:
    """Return the user id, or None for any invalid token."""
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.InvalidTokenError:
        return None
    return claims["sub"]
