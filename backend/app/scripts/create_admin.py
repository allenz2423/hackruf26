"""Create the first administrator without exposing a public admin-setup route.

Run from the repository root with the backend virtualenv:
    PYTHONPATH=backend backend/.venv/bin/python -m app.scripts.create_admin USERNAME

The password is prompted twice, never accepted as a command-line argument, logged,
or stored in plaintext. The command refuses to add another administrator.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from pydantic import ValidationError

from app.api.errors import ApiError
from app.api.schemas import SignupRequest
from app.config import get_settings
from app.services import auth as accounts
from app.services.database import create_database


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the first Jot administrator.")
    parser.add_argument("username")
    parser.add_argument("--display-name", default=None)
    return parser.parse_args(argv)


async def run(argv: list[str]) -> int:
    args = _parse(argv)
    password = getpass.getpass("New admin password (8 to 128 characters): ")
    confirmation = getpass.getpass("Confirm admin password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2

    try:
        details = SignupRequest(
            username=args.username,
            password=password,
            display_name=args.display_name,
        )
    except ValidationError as error:
        for problem in error.errors():
            field = problem["loc"][0] if problem.get("loc") else "request"
            print(f"{field}: {problem['msg']}", file=sys.stderr)
        return 2

    password_hash = await accounts.hash_password(details.password)
    database = await create_database(get_settings())
    try:
        async with database.acquire() as connection:
            async with connection.transaction():
                if await connection.fetchrow("SELECT 1 FROM users WHERE is_admin = 1 LIMIT 1"):
                    print(
                        "An administrator already exists; refusing to add another.",
                        file=sys.stderr,
                    )
                    return 2
                if await connection.fetchrow(
                    "SELECT 1 FROM signup_requests "
                    "WHERE username = ?1 AND status = 'pending'",
                    details.username,
                ):
                    print(
                        "A pending signup request uses that username; choose another.",
                        file=sys.stderr,
                    )
                    return 2
                await accounts.create_user_with_hash(
                    connection,
                    username=details.username,
                    password_hash=password_hash,
                    display_name=details.display_name,
                    is_admin=True,
                )
    except ApiError as error:
        print(error.message, file=sys.stderr)
        return 2
    finally:
        await database.close()

    print(f"Created administrator account '{details.username}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(sys.argv[1:])))
