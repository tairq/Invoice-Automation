"""Preprocessing service — converts invoices to images for LLM vision."""

from __future__ import annotations

import io
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def pdf_to_images(pdf_bytes: bytes, dpi: int = 200) -> list[bytes]:
    """Convert PDF pages to PNG images.

    Uses multiple strategies:
    1. pdfplumber + Pillow (best quality, standard PDFs)
    2. pypdf + Pillow (fallback for simpler PDFs)
    """
    images: list[bytes] = []

    # Strategy 1: pdfplumber
    try:
        import pdfplumber
        from PIL import Image

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=dpi)
                img_bytes = io.BytesIO()
                img.save(img_bytes, format="PNG")
                images.append(img_bytes.getvalue())

        os.unlink(tmp_path)

        if images:
            return images
    except Exception as exc:
        logger.warning("pdfplumber conversion failed: %s. Trying pypdf...", exc)

    # Strategy 2: pypdf fallback
    try:
        from PIL import Image
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            # pypdf doesn't render images directly for complex PDFs
            # This works for simple/image-based PDFs
            for img_key in page.images:
                img_data = page.images[img_key].data
                img = Image.open(io.BytesIO(img_data))
                img_bytes = io.BytesIO()
                img.save(img_bytes, format="PNG")
                images.append(img_bytes.getvalue())
    except Exception as exc:
        logger.warning("pypdf conversion failed: %s", exc)

    return images


def convert_to_images(file_bytes: bytes, file_type: str) -> list[bytes]:
    """Convert an invoice file to one or more PNG images for LLM vision.

    Supports PDF, JPEG, PNG, TIFF.
    """
    from PIL import Image

    if file_type == "pdf":
        return pdf_to_images(file_bytes)

    # Single image formats
    if file_type in ("jpg", "jpeg", "png"):
        return [file_bytes]

    # TIFF (may be multi-page)
    if file_type == "tiff":
        try:
            img = Image.open(io.BytesIO(file_bytes))
            images = []
            for page in range(getattr(img, "n_frames", 1)):
                img.seek(page)
                frame_bytes = io.BytesIO()
                img.save(frame_bytes, format="PNG")
                images.append(frame_bytes.getvalue())
            return images
        except Exception:
            return [file_bytes]

    return [file_bytes]


def enhance_images(image_bytes_list: list[bytes]) -> list[bytes]:
    """Apply image enhancement for better LLM vision processing."""
    try:
        import cv2
        import numpy as np

        enhanced = []
        for img_bytes in image_bytes_list:
            arr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

            # Denoise
            img = cv2.fastNlMeansDenoisingColored(img, h=10, hColor=10)

            # Sharpen
            kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
            img = cv2.filter2D(img, -1, kernel)

            _, buffer = cv2.imencode(".png", img)
            enhanced.append(buffer.tobytes())

        return enhanced
    except ImportError:
        return image_bytes_list
    except Exception as exc:
        logger.warning("Image enhancement failed: %s", exc)
        return image_bytes_list
