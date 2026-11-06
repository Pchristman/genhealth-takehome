"""Create two demo users. Run from backend/: uv run python -m scripts.seed

The password comes from SEED_PASSWORD, or is generated and printed once.
"""

import os
import secrets

from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import User
from app.security import hash_password

EMAILS = ["demo-a@example.com", "demo-b@example.com"]


def main() -> None:
    init_db()
    password = os.environ.get("SEED_PASSWORD") or secrets.token_urlsafe(16)
    created = []
    with Session(engine) as db:
        for email in EMAILS:
            if db.exec(select(User).where(User.email == email)).first() is None:
                db.add(User(email=email, password_hash=hash_password(password)))
                created.append(email)
        db.commit()
    if created:
        print(f"Created {', '.join(created)}")
        if "SEED_PASSWORD" not in os.environ:
            print(f"Password: {password}")
    else:
        print("Demo users already exist.")


if __name__ == "__main__":
    main()
