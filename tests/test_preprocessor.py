"""Tests for the preprocessing service."""
from __future__ import annotations

from app.services.preprocessor import convert_to_images, pdf_to_images


class TestConvertToImages:
    def test_jpg_passthrough(self):
        result = convert_to_images(b"fake jpg bytes", "jpg")
        assert len(result) == 1
        assert result[0] == b"fake jpg bytes"

    def test_png_passthrough(self):
        result = convert_to_images(b"fake png bytes", "png")
        assert len(result) == 1
        assert result[0] == b"fake png bytes"

    def test_pdf_no_crash(self):
        # This is a fake PDF — should not crash but may return empty
        result = convert_to_images(b"not a real pdf", "pdf")
        # Either returns images or empty list — both acceptable
        assert isinstance(result, list)
