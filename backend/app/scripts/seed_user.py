"""Create or update a local account, for development and demos.

    python -m app.scripts.seed_user USERNAME PASSWORD [--display-name NAME] [--adopt-local-data]

The password is only used to compute a hash; it is never printed or stored.
Run from the backend directory with the virtualenv active. Refuses to run
when APP_ENV=production.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError

from app.api.schemas import SignupRequest
from app.config import get_settings
from app.services import auth as accounts
from app.services.database import create_database


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update a Jot account.")
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("--display-name", default=None)
    parser.add_argument(
        "--adopt-local-data",
        action="store_true",
        help="Give this account the classes that were saved before accounts existed.",
    )
    return parser.parse_args(argv)


async def run(argv: list[str]) -> int:
    args = _parse(argv)
    settings = get_settings()
    if settings.app_env == "production":
        print("Refusing to seed an account when APP_ENV=production.", file=sys.stderr)
        return 2

    try:
        details = SignupRequest(
            username=args.username, password=args.password, display_name=args.display_name
        )
    except ValidationError as error:
        for problem in error.errors():
            print(f"{problem['loc'][0]}: {problem['msg']}", file=sys.stderr)
        return 2

    database = await create_database(settings)
    try:
        existing = await database.fetchrow(
            "SELECT id FROM users WHERE username = ?1", details.username
        )
        if existing:
            user_id = existing["id"]
            await database.execute(
                "UPDATE users SET password_hash = ?1, display_name = ?2 WHERE id = ?3",
                await accounts.hash_password(details.password),
                details.display_name or details.username,
                user_id,
            )
            action = "Updated"
        else:
            user = await accounts.create_user(
                database,
                username=details.username,
                password=details.password,
                display_name=details.display_name,
            )
            user_id = user["id"]
            action = "Created"

        adopted = 0
        if args.adopt_local_data:
            result = await database.execute(
                "UPDATE course_classes SET owner_id = ?1 WHERE owner_id = ?2",
                user_id,
                settings.local_owner_id,
            )
            adopted = int(result.split()[-1])
    finally:
        await database.close()

    print(f"{action} account '{details.username}'.")
    if args.adopt_local_data:
        print(f"Moved {adopted} existing class(es) to this account.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(sys.argv[1:])))
