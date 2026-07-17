"""
Create or update a login user directly against the database.

Why this exists: docs/MIGRATION.md documents a `load_demo_data.py` seed path, but
no such script (or any other account-creation path - there is no /auth/register
endpoint) ships in this repo. Without it there is no way to log in to a fresh
database. This is the minimal thing that unblocks that: it hashes a password with
the same `bcrypt` helper the API uses and upserts a row via the same
`UserRepository` the API's login path reads from, so there is exactly one
definition of "how a user is stored."

Usage (from platform/backend, with DMRV_DB_* / DMRV_JWT_SECRET set as in .env):
    python -m scripts.create_admin --username admin --password 'a-strong-password'
    python -m scripts.create_admin --username viewer1 --password '...' --role Viewer

Or against the running compose stack:
    docker compose exec backend python -m scripts.create_admin --username admin --password '...'
"""
from __future__ import annotations

import argparse
import sys

from app.core.config import get_settings
from app.core.db import Database
from app.core.security import hash_password
from app.domain.enums import Role
from app.repositories.users import UserRepository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--role", default=Role.ADMINISTRATOR.value, choices=[r.value for r in Role]
    )
    args = parser.parse_args()

    if len(args.password) < 8:
        print("Refusing a password shorter than 8 characters.", file=sys.stderr)
        raise SystemExit(1)

    settings = get_settings()
    db = Database(settings)
    db.connect()
    try:
        with db.transaction() as cur:
            user = UserRepository(cur).upsert(
                username=args.username,
                password_hash=hash_password(args.password),
                role=args.role,
            )
        print(f"OK: user_id={user['user_id']} username={user['username']} role={user['role']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
