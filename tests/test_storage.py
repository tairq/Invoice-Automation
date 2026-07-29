"""Tests for storage backends."""

from __future__ import annotations

from app.core.storage import LocalStorage


class TestLocalStorageURL:
    def test_get_url_returns_uri(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.save("test.txt", b"hello")
        url = storage.get_url("test.txt")
        assert url is not None
        assert url.startswith("file:///")
        assert "test.txt" in url

    def test_get_url_nonexistent(self, tmp_path):
        storage = LocalStorage(tmp_path)
        url = storage.get_url("nonexistent.txt")
        assert url is None

    def test_get_url_custom_expiry(self, tmp_path):
        """expires_in param is accepted but may be ignored by local backend."""
        storage = LocalStorage(tmp_path)
        storage.save("test.txt", b"hello")
        url = storage.get_url("test.txt", expires_in=7200)
        assert url is not None
