"""Verify Supabase Auth access tokens without trusting client-supplied user IDs."""

from __future__ import annotations

from uuid import UUID

import jwt
from jwt import PyJWKClient

from app.config import Settings


class SupabaseTokenInvalid(ValueError):
    """The bearer token is invalid, expired, or does not identify a user."""


class SupabaseVerifierUnavailable(RuntimeError):
    """Supabase signing keys could not be fetched or are not configured."""


class SupabaseJWTVerifier:
    """Validate Supabase JWT signatures and the claims used for app ownership."""

    def __init__(self, settings: Settings) -> None:
        issuer = settings.supabase_issuer
        if issuer is None:
            raise ValueError("SUPABASE_URL is required to construct the JWT verifier.")
        self._issuer = issuer
        self._legacy_secret = settings.supabase_jwt_secret.get_secret_value() if settings.supabase_jwt_secret else None
        self._jwks = PyJWKClient(
            f"{issuer}/.well-known/jwks.json",
            cache_keys=True,
            timeout=settings.gemini_timeout_seconds,
        )

    def verify(self, token: str) -> UUID:
        """Return the verified JWT subject UUID or raise a sanitized auth error."""
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm == "HS256":
                if not self._legacy_secret:
                    raise SupabaseTokenInvalid("Legacy Supabase JWT signing is not configured.")
                signing_key = self._legacy_secret
            elif algorithm in {"ES256", "RS256"}:
                signing_key = self._jwks.get_signing_key_from_jwt(token).key
            else:
                raise SupabaseTokenInvalid("Unsupported JWT signing algorithm.")

            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[algorithm],
                audience="authenticated",
                issuer=self._issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
            try:
                return UUID(claims["sub"])
            except (KeyError, ValueError) as exc:
                raise SupabaseTokenInvalid("The Supabase token subject is invalid.") from exc
        except SupabaseTokenInvalid:
            raise
        except jwt.InvalidTokenError as exc:
            raise SupabaseTokenInvalid("Invalid Supabase access token.") from exc
        except Exception as exc:  # key-fetch errors must not be confused with valid identity
            raise SupabaseVerifierUnavailable("Supabase signing keys are unavailable.") from exc
