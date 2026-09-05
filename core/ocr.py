"""
Nexus OCR — извлечение текста из изображений (.png, .jpg, .jpeg, .bmp, .tiff).

Использует два бэкенда (по убыванию приоритета):
1. **pytesseract** — обёртка над Tesseract OCR (бесплатно, быстро, требует
   установленный tesseract.exe на системе).
2. **easyocr** — нейросетевой OCR (качественнее, работает из коробки, но медленнее
   и тяжелее).

Если ни один бэкенд не установлен — изображения пропускаются с предупреждением
в логе (без ошибок).

Использование:
    from core.ocr import extract_text_from_image

    # Автоматический выбор бэкенда
    text = extract_text_from_image("screenshot.png")

    # Явный бэкенд
    text = extract_text_from_image("scan.jpg", engine="easyocr")

    # Многоязычный OCR (RU + EN)
    text = extract_text_from_image("doc.png", languages=["rus", "eng"])
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Configuration ---

# Default languages for OCR (Tesseract lang codes)
DEFAULT_LANGUAGES = ["eng", "rus"]

# Confidence threshold (0-100 for pytesseract, 0-1 for easyocr)
PYTESSERACT_THRESHOLD = 40
EASYOCR_THRESHOLD = 0.3


# --- Backend detection ---

def _has_pytesseract() -> bool:
    """Check if pytesseract is available."""
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


def _has_easyocr() -> bool:
    """Check if easyocr is available."""
    try:
        import easyocr  # noqa: F401
        return True
    except ImportError:
        return False


def _best_engine() -> Optional[str]:
    """Return the best available OCR engine."""
    if _has_pytesseract():
        return "pytesseract"
    if _has_easyocr():
        return "easyocr"
    return None


# --- pytesseract backend ---

def _extract_pytesseract(image_path: Path, languages: List[str]) -> Optional[str]:
    """Extract text using pytesseract (Tesseract OCR).

    Requires: pip install pytesseract + system tesseract installed.
    """
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return None

    try:
        text = pytesseract.image_to_string(
            str(image_path),
            lang="+".join(languages),
            config=f"--psm 6 --oem 3 -c tessedit_char_whitelist=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ ",
        )
        return text.strip() or None
    except Exception as e:
        logger.warning("pytesseract failed for %s: %s", image_path.name, e)
        return None


# --- easyocr backend ---

def _extract_easyocr(image_path: Path, languages: List[str]) -> Optional[str]:
    """Extract text using easyocr (neural network based).

    Requires: pip install easyocr
    """
    try:
        import easyocr  # type: ignore
    except ImportError:
        return None

    try:
        reader = easyocr.Reader(lang_list=languages, gpu=False)
        results = reader.readtext(str(image_path), detail=0)
        text = "\n".join(results)
        return text.strip() or None
    except Exception as e:
        logger.warning("easyocr failed for %s: %s", image_path.name, e)
        return None


# --- Public API ---

def extract_text_from_image(
    image_path: str | Path,
    engine: Optional[str] = None,
    languages: Optional[List[str]] = None,
) -> Optional[str]:
    """Extract text from an image file using OCR.

    Args:
        image_path: Path to the image file (.png, .jpg, .jpeg, .bmp, .tiff).
        engine: OCR engine to use ("pytesseract" or "easyocr").
                If None, automatically picks the best available.
        languages: Language codes for OCR (default: ["eng", "rus"]).

    Returns:
        Extracted text, or None if OCR failed / no engine available.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        logger.error("Image not found: %s", image_path)
        return None

    langs = languages or DEFAULT_LANGUAGES

    # Determine engine
    if engine:
        available = {"pytesseract": _has_pytesseract, "easyocr": _has_easyocr}
        if not available.get(engine, lambda: False)():
            logger.error("OCR engine '%s' not available", engine)
            return None
    else:
        engine = _best_engine()

    if not engine:
        logger.warning(
            "No OCR engine available. Install one of: pytesseract, easyocr. "
            "Images will be skipped."
        )
        return None

    # Extract with fallback to second engine if first returns empty
    engines_to_try = [engine]
    if engine == "pytesseract" and _has_easyocr():
        engines_to_try.append("easyocr")
    elif engine == "easyocr" and _has_pytesseract():
        engines_to_try.append("pytesseract")

    for eng in engines_to_try:
        if eng == "pytesseract":
            text = _extract_pytesseract(image_path, langs)
        elif eng == "easyocr":
            text = _extract_easyocr(image_path, langs)
        else:
            continue

        if text:
            return text

    return None


def ocr_scan_directory(
    dir_path: str | Path,
    engine: Optional[str] = None,
    languages: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Scan a directory for image files and extract text from each.

    Args:
        dir_path: Directory to scan.
        engine: OCR engine to use.
        languages: Language codes.

    Returns:
        Report dict with results per file.
    """
    dir_path = Path(dir_path)
    if not dir_path.exists():
        return {"status": "error", "message": f"Directory not found: {dir_path}"}

    image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
    files = sorted(p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() in image_extensions)

    if not files:
        return {
            "status": "ok",
            "scanned": 0,
            "success": 0,
            "failed": 0,
            "results": [],
            "message": "No image files found.",
        }

    results: List[Dict[str, Any]] = []
    success_count = 0
    failed_count = 0

    for f in files:
        text = extract_text_from_image(f, engine=engine, languages=languages)
        if text:
            results.append({
                "file": str(f.name),
                "status": "success",
                "text_length": len(text),
                "preview": text[:200],
            })
            success_count += 1
        else:
            results.append({
                "file": str(f.name),
                "status": "failed",
                "reason": "OCR returned empty or engine unavailable",
            })
            failed_count += 1

    return {
        "status": "ok",
        "scanned": len(files),
        "success": success_count,
        "failed": failed_count,
        "results": results,
    }


def get_ocr_status() -> Dict[str, Any]:
    """Return information about available OCR engines."""
    return {
        "pytesseract": _has_pytesseract(),
        "easyocr": _has_easyocr(),
        "best_available": _best_engine(),
        "default_languages": DEFAULT_LANGUAGES,
        "supported_formats": [".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"],
    }
