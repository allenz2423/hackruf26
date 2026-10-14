"""Encrypted, administrator-managed OpenAI-compatible provider settings."""

from __future__ import annotations

import base64
import logging
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import Settings
from app.services.database import SQLiteDatabase
from app.services.openai_compatible import (
    ProviderConfig,
    ProviderConfigManager,
    validate_base_url,
)

logger = logging.getLogger("screen_tutor.ai_provider_settings")
_AAD = b"jot-ai-provider-api-key-v1"
_NONCE_BYTES = 12


def _cipher(settings: Settings) -> AESGCM:
    signing_secret = settings.file_url_signing_secret
    if signing_secret is None or len(signing_secret.get_secret_value().encode("utf-8")) < 32:
        raise RuntimeError("Provider settings encryption is unavailable.")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"jot-ai-provider-settings-v1",
        info=b"openai-compatible-api-key",
    ).derive(signing_secret.get_secret_value().encode("utf-8"))
    return AESGCM(key)


def _encrypt(settings: Settings, api_key: str) -> str:
    nonce = secrets.token_bytes(_NONCE_BYTES)
    encrypted = _cipher(settings).encrypt(nonce, api_key.encode("utf-8"), _AAD)
    return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def _decrypt(settings: Settings, value: str) -> str:
    try:
        encrypted = base64.urlsafe_b64decode(value.encode("ascii"))
        if len(encrypted) <= _NONCE_BYTES:
            raise ValueError("Invalid encrypted provider key.")
        return (
            _cipher(settings)
            .decrypt(encrypted[:_NONCE_BYTES], encrypted[_NONCE_BYTES:], _AAD)
            .decode("utf-8")
        )
    except Exception:
        raise ValueError("The saved provider key cannot be decrypted.") from None


def _environment_config(settings: Settings) -> ProviderConfig | None:
    base_url = settings.openai_compatible_base_url
    model = settings.openai_compatible_model
    secret = settings.openai_compatible_api_key
    if base_url is None or model is None or secret is None:
        return None
    return ProviderConfig(
        validate_base_url(base_url, production=settings.app_env == "production"),
        model.strip(),
        secret.get_secret_value(),
    )


async def load_provider_config(
    database: SQLiteDatabase, settings: Settings
) -> ProviderConfigManager:
    row = await database.fetchrow(
        "SELECT base_url,model,encrypted_api_key FROM ai_provider_settings WHERE id=1"
    )
    if row is None:
        config = _environment_config(settings)
        return ProviderConfigManager(
            config, source="environment" if config is not None else "unconfigured"
        )
    try:
        base_url = validate_base_url(
            str(row["base_url"]), production=settings.app_env == "production"
        )
        model = str(row["model"]).strip()
        encrypted_key = row["encrypted_api_key"]
        api_key = _decrypt(settings, encrypted_key) if encrypted_key else None
    except ValueError:
        logger.error("ai_provider_settings_load_failed reason=invalid_or_unreadable_settings")
        config = _environment_config(settings)
        return ProviderConfigManager(
            config, source="environment" if config is not None else "unconfigured"
        )
    return ProviderConfigManager(ProviderConfig(base_url, model, api_key), source="admin")


async def save_provider_config(
    database: SQLiteDatabase,
    settings: Settings,
    manager: ProviderConfigManager,
    *,
    base_url: str,
    model: str,
    api_key: str | None,
) -> ProviderConfig:
    validated_url = validate_base_url(base_url, production=settings.app_env == "production")
    normalized_model = model.strip()
    if (
        not normalized_model
        or len(normalized_model) > 200
        or any(character.isspace() for character in normalized_model)
    ):
        raise ValueError("Enter a non-empty model identifier without whitespace.")
    if api_key is not None:
        api_key = api_key.strip()
        if not api_key or len(api_key) > 4_096:
            raise ValueError("The API key must contain between 1 and 4096 characters.")

    encrypted_key = _encrypt(settings, api_key) if api_key else None
    await database.execute(
        "INSERT INTO ai_provider_settings (id,base_url,model,encrypted_api_key) "
        "VALUES (1,?1,?2,?3) ON CONFLICT(id) DO UPDATE SET "
        "base_url=excluded.base_url,model=excluded.model,"
        "encrypted_api_key=excluded.encrypted_api_key,"
        "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
        validated_url,
        normalized_model,
        encrypted_key,
    )
    config = ProviderConfig(validated_url, normalized_model, api_key)
    manager.set(config)
    return config


async def clear_provider_config(
    database: SQLiteDatabase,
    settings: Settings,
    manager: ProviderConfigManager,
) -> None:
    """Remove the administrator override and restore the environment config, if any."""
    config = _environment_config(settings)
    await database.execute("DELETE FROM ai_provider_settings WHERE id=1")
    source = "environment" if config is not None else "unconfigured"
    manager.set(config, source=source)
