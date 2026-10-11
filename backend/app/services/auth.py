"""Accounts, password hashing, and cookie-backed login sessions.

Passwords are stored as salted scrypt hashes. Session tokens are random values
sent to the browser in an HttpOnly cookie; only their SHA-256 hash is stored,
so a copy of the database cannot be replayed as a login.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.api.errors import ApiError
from app.services.database import SQLiteDatabase

SESSION_COOKIE = "jot_session"

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}$")

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_KEY_BYTES = 32
_SCRYPT_MAX_MEMORY = 64 * 1024 * 1024
_TIMING_SALT = b"jot-timing-equalizer"


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_SCRYPT_KEY_BYTES,
        maxmem=_SCRYPT_MAX_MEMORY,
    )


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


async def hash_password(password: str) -> str:
    """Return a self-describing salted scrypt hash; runs off the event loop."""
    salt = secrets.token_bytes(16)
    digest = await asyncio.to_thread(_scrypt, password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


async def verify_password(password: str, stored_hash: str) -> bool:
    """Compare a password with a stored hash in constant time."""
    try:
        scheme, n, r, p, salt, expected = stored_hash.split("$")
        if scheme != "scrypt":
            return False
        actual = await asyncio.to_thread(
            _scrypt, password, base64.b64decode(salt), int(n), int(r), int(p)
        )
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


async def spend_password_time(password: str) -> None:
    """Burn a comparable amount of time when no account matched the login."""
    await asyncio.to_thread(_scrypt, password, _TIMING_SALT, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _stored_avatar(value: Any) -> dict[str, str] | None:
    """A saved avatar, or None if there is none or the stored value is not valid."""
    from app.api.schemas import Avatar

    if not value:
        return None
    try:
        return Avatar.model_validate_json(value).model_dump()
    except ValueError:
        return None


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    """The account fields that are safe to send to the browser."""
    return {
        "id": str(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row.get("email"),
        "is_demo": bool(row.get("is_demo")),
        "avatar": _stored_avatar(row.get("avatar")),
    }


async def create_user(
    database: SQLiteDatabase,
    *,
    username: str,
    password: str,
    email: str | None = None,
    display_name: str | None = None,
    user_id: UUID | None = None,
    is_demo: bool = False,
) -> dict[str, Any]:
    """Create an account or raise a 409 naming the field that is already taken."""
    if await database.fetchrow("SELECT 1 FROM users WHERE username = ?1", username):
        raise ApiError(409, "username_taken", "That username is already taken.")
    if email and await database.fetchrow("SELECT 1 FROM users WHERE email = ?1", email):
        raise ApiError(409, "email_taken", "An account with that email already exists.")

    new_id = user_id or uuid4()
    password_hash = await hash_password(password)
    await database.execute(
        "INSERT INTO users (id, username, email, display_name, password_hash, is_demo) "
        "VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        new_id,
        username,
        email or None,
        (display_name or username).strip() or username,
        password_hash,
        1 if is_demo else 0,
    )
    row = await database.fetchrow(
        "SELECT id, username, email, display_name, is_demo, avatar FROM users WHERE id = ?1", new_id
    )
    assert row is not None
    return row


async def authenticate(
    database: SQLiteDatabase, identifier: str, password: str
) -> dict[str, Any] | None:
    """Return the matching account for a username-or-email login, else None."""
    column = "email" if "@" in identifier else "username"
    row = await database.fetchrow(
        f"SELECT id, username, email, display_name, is_demo, avatar, password_hash FROM users WHERE {column} = ?1",
        identifier,
    )
    if row is None:
        await spend_password_time(password)
        return None
    if not await verify_password(password, row["password_hash"]):
        return None
    return {key: row[key] for key in ("id", "username", "email", "display_name", "is_demo", "avatar")}


async def create_session(
    database: SQLiteDatabase, user_id: UUID | str, ttl_days: int
) -> tuple[str, datetime]:
    """Start a login session; return the raw cookie token and its expiry."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=ttl_days)
    await database.execute(
        "INSERT INTO user_sessions (token_hash, user_id, expires_at) VALUES (?1, ?2, ?3)",
        token_hash(token),
        str(user_id),
        _iso(expires_at),
    )
    await database.execute("DELETE FROM user_sessions WHERE expires_at <= ?1", _iso(now))
    return token, expires_at


async def user_for_token(database: SQLiteDatabase, token: str) -> dict[str, Any] | None:
    """Resolve a session cookie to its account, ignoring expired sessions."""
    if not token or len(token) > 128:
        return None
    return await database.fetchrow(
        "SELECT u.id, u.username, u.email, u.display_name, u.is_demo, u.avatar "
        "FROM user_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ?1 AND s.expires_at > ?2",
        token_hash(token),
        _iso(datetime.now(UTC)),
    )


async def delete_session(database: SQLiteDatabase, token: str) -> None:
    await database.execute("DELETE FROM user_sessions WHERE token_hash = ?1", token_hash(token))


async def update_profile(
    database: SQLiteDatabase, user_id: UUID | str, changes: dict[str, Any]
) -> dict[str, Any]:
    """Change username, email, and/or avatar; `None` removes an email or avatar. 409 if taken."""
    current = await database.fetchrow(
        "SELECT id, username, email, display_name, is_demo, avatar FROM users WHERE id = ?1", str(user_id)
    )
    if current is None:
        raise ApiError(401, "authentication_required", "Please log in again.")

    username = changes.get("username", current["username"])
    email = changes["email"] if "email" in changes else current["email"]

    if username.lower() != current["username"].lower() and await database.fetchrow(
        "SELECT 1 FROM users WHERE username = ?1 AND id <> ?2", username, str(user_id)
    ):
        raise ApiError(409, "username_taken", "That username is already taken.")
    if email and await database.fetchrow(
        "SELECT 1 FROM users WHERE email = ?1 AND id <> ?2", email, str(user_id)
    ):
        raise ApiError(409, "email_taken", "An account with that email already exists.")

    # A display name that was just the old username keeps following the username.
    display_name = username if current["display_name"] == current["username"] else current["display_name"]
    if "avatar" in changes:
        avatar = json.dumps(changes["avatar"], separators=(",", ":")) if changes["avatar"] else None
    else:
        avatar = current["avatar"]
    try:
        await database.execute(
            "UPDATE users SET username = ?1, email = ?2, display_name = ?3, avatar = ?4 WHERE id = ?5",
            username,
            email or None,
            display_name,
            avatar,
            str(user_id),
        )
    except sqlite3.IntegrityError:
        raise ApiError(409, "username_taken", "That username or email is already in use.") from None

    row = await database.fetchrow(
        "SELECT id, username, email, display_name, is_demo, avatar FROM users WHERE id = ?1", str(user_id)
    )
    assert row is not None
    return row


async def change_password(
    database: SQLiteDatabase,
    user_id: UUID | str,
    current_password: str,
    new_password: str,
    *,
    keep_session_token: str | None,
) -> None:
    """Replace the password after checking the old one; end every other login session."""
    row = await database.fetchrow("SELECT password_hash FROM users WHERE id = ?1", str(user_id))
    if row is None or not await verify_password(current_password, row["password_hash"]):
        raise ApiError(400, "incorrect_password", "Your current password is incorrect.")
    if hmac.compare_digest(current_password.encode(), new_password.encode()):
        raise ApiError(
            400, "password_unchanged", "Choose a new password that is different from your current one."
        )

    new_hash = await hash_password(new_password)
    keep_hash = token_hash(keep_session_token) if keep_session_token else ""
    async with database.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "UPDATE users SET password_hash = ?1 WHERE id = ?2", new_hash, str(user_id)
            )
            await connection.execute(
                "DELETE FROM user_sessions WHERE user_id = ?1 AND token_hash <> ?2",
                str(user_id),
                keep_hash,
            )
