"""OCR engine wrapper — Tesseract fallback for image-based invoices."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.config import settings


def run_ocr(image_path: str | Path, languages: Optional[str] = None) -> str:
    """Run OCR on an image file and return extracted text.

    Uses Tesseract. This is a fallback for when the LLM vision
    pipeline cannot process an image (e.g. extremely poor quality).
    """
    try:
        import pytesseract

        if languages:
            lang = languages
        else:
            lang = settings.ocr_languages

        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

        text = pytesseract.image_to_string(
            str(image_path),
            lang=lang,
            config="--oem 3 --psm 6",
        )
        return text.strip()
    except Exception as exc:
        return f"[OCR Error: {exc}]"


def enhance_image(image_path: str | Path) -> str:
    """Apply image enhancement (deskew, denoise, contrast) for better OCR.

    Returns path to the enhanced image.
    """
    try:
        import cv2
        import numpy as np

        img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)

        # Denoise
        img = cv2.fastNlMeansDenoising(img, h=30)

        # Threshold
        _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Deskew
        coords = np.column_stack(np.where(img > 0))
        if len(coords) > 0:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = 90 + angle
            h, w = img.shape
            center = (w // 2, h // 2)
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(
                img,
                matrix,
                (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )

        enhanced_path = Path(str(image_path).replace(".", "_enhanced."))
        cv2.imwrite(str(enhanced_path), img)
        return str(enhanced_path)
    except ImportError:
        return str(image_path)
    except Exception:
        return str(image_path)
