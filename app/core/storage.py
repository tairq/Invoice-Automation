"""File storage abstraction — local filesystem or S3-compatible."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO, Optional

from app.config import settings


class BaseStorage:
    """Abstract base for file storage backends."""

    def save(self, file_path: str, content: bytes | BinaryIO) -> str:
        """Save file and return the storage path."""
        raise NotImplementedError

    def read(self, file_path: str) -> bytes:
        """Read file contents."""
        raise NotImplementedError

    def delete(self, file_path: str) -> None:
        """Delete a file."""
        raise NotImplementedError

    def exists(self, file_path: str) -> bool:
        """Check if a file exists."""
        raise NotImplementedError


class LocalStorage(BaseStorage):
    """Local filesystem storage."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, file_path: str) -> Path:
        full = self.base_path / file_path
        full.parent.mkdir(parents=True, exist_ok=True)
        return full

    def save(self, file_path: str, content: bytes | BinaryIO) -> str:
        dest = self._resolve(file_path)
        if isinstance(content, bytes):
            dest.write_bytes(content)
        else:
            with open(dest, "wb") as f:
                shutil.copyfileobj(content, f)
        return str(dest)

    def read(self, file_path: str) -> bytes:
        return self._resolve(file_path).read_bytes()

    def delete(self, file_path: str) -> None:
        self._resolve(file_path).unlink(missing_ok=True)

    def exists(self, file_path: str) -> bool:
        return self._resolve(file_path).exists()


class S3Storage(BaseStorage):
    """S3-compatible storage."""

    def __init__(self) -> None:
        import boto3  # noqa: auto-import

        self.client = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        self.bucket = settings.aws_s3_bucket

    def save(self, file_path: str, content: bytes | BinaryIO) -> str:
        if isinstance(content, bytes):
            self.client.put_object(Bucket=self.bucket, Key=file_path, Body=content)
        else:
            self.client.upload_fileobj(content, self.bucket, file_path)
        return file_path

    def read(self, file_path: str) -> bytes:
        resp = self.client.get_object(Bucket=self.bucket, Key=file_path)
        return resp["Body"].read()

    def delete(self, file_path: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=file_path)

    def exists(self, file_path: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=file_path)
            return True
        except Exception:
            return False


def get_storage() -> BaseStorage:
    """Factory: return the configured storage backend."""
    if settings.storage_backend == "s3":
        return S3Storage()
    return LocalStorage(settings.storage_abs_path)


storage = get_storage()
