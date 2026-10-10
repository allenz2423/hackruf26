"""Private local file storage with short-lived signed download URLs."""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import UploadFile

from app.config import Settings


class UploadTooLarge(ValueError):
    """The incoming file exceeded the configured byte limit."""


class LocalPrivateStorage:
    """Store original files outside the static web root and sign read URLs."""

    def __init__(self, settings: Settings) -> None:
        if settings.file_url_signing_secret is None:
            raise ValueError("FILE_URL_SIGNING_SECRET is required for private file storage.")
        self.root = settings.storage_dir_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._signing_key = settings.file_url_signing_secret.get_secret_value().encode("utf-8")

    async def save_upload(self, upload: UploadFile, max_bytes: int) -> tuple[str, int]:
        """Stream one upload to a server-generated key and enforce its size."""
        suffix = Path(upload.filename or "upload").suffix.lower()[:16]
        storage_key = f"{uuid4().hex}{suffix}"
        path = self.path_for(storage_key)
        size = 0
        try:
            with path.open("wb") as output:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadTooLarge
                    output.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        if size == 0:
            path.unlink(missing_ok=True)
            raise ValueError("The uploaded file is empty.")
        return storage_key, size

    def path_for(self, storage_key: str) -> Path:
        """Resolve a database storage key while rejecting traversal."""
        candidate = (self.root / storage_key).resolve()
        if candidate == self.root or self.root not in candidate.parents:
            raise ValueError("Invalid storage key.")
        return candidate

    def delete(self, storage_key: str | None) -> None:
        if storage_key:
            self.path_for(storage_key).unlink(missing_ok=True)

    def signed_url(self, base_url: str, upload_id: UUID, *, lifetime_seconds: int = 300) -> tuple[str, int]:
        expires = int(time.time()) + lifetime_seconds
        signature = self._signature(upload_id, expires)
        url = (
            f"{base_url.rstrip('/')}/api/uploads/{upload_id}/file"
            f"?expires={expires}&signature={quote(signature)}"
        )
        return url, expires

    def verify_url(self, upload_id: UUID, expires: int, signature: str) -> bool:
        if expires < int(time.time()) or expires > int(time.time()) + 600:
            return False
        return hmac.compare_digest(self._signature(upload_id, expires), signature)

    def _signature(self, upload_id: UUID, expires: int) -> str:
        payload = f"{upload_id}:{expires}".encode("ascii")
        return hmac.new(self._signing_key, payload, hashlib.sha256).hexdigest()
