import hmac
import unicodedata
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import User
from app.security import DUMMY_HASH, create_token, hash_password, read_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)

Db = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    signup_code: str


class UserOut(BaseModel):
    id: str
    email: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


def normalize_email(email: str) -> str:
    return unicodedata.normalize("NFC", email.strip()).lower()


def _require_signup_code(submitted: str) -> None:
    expected = settings.signup_code
    # Unset means closed, so an empty submission can never match an empty setting.
    if not expected or not hmac.compare_digest(
        # surrogatepass: JSON can carry a lone surrogate, which is a wrong code, not a server error.
        submitted.encode("utf-8", "surrogatepass"),
        expected.encode("utf-8"),
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Signup is not available.")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def current_principal(token: Annotated[str | None, Depends(bearer)], db: Db) -> Principal:
    user_id = read_token(token) if token else None
    # The user lookup matters: a valid token can outlive its account.
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise _unauthorized("Bearer token is missing or invalid.")
    return Principal(user_id=user.id, email=user.email)


Auth = Annotated[Principal, Depends(current_principal)]


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, db: Db) -> User:
    _require_signup_code(body.signup_code)  # first: a stranger shouldn't cost a scrypt hash
    user = User(email=normalize_email(body.email), password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered.") from None
    db.refresh(user)
    return user


@router.post("/token", response_model=TokenOut)
def token(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: Db) -> TokenOut:
    user = db.exec(select(User).where(User.email == normalize_email(form.username))).first()
    valid = verify_password(form.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not valid:
        raise _unauthorized("Incorrect email or password.")
    return TokenOut(access_token=create_token(user.id))


@router.get("/me", response_model=UserOut)
def me(who: Auth) -> UserOut:
    return UserOut(id=who.user_id, email=who.email)
