"""
Tests for core/ocr.py - OCR extraction from images.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ocr import (
    extract_text_from_image,
    ocr_scan_directory,
    get_ocr_status,
    _has_pytesseract,
    _has_easyocr,
    _best_engine,
)


class TestOCRStatus:
    """Test OCR engine detection."""

    def test_get_ocr_status(self):
        status = get_ocr_status()
        assert "pytesseract" in status
        assert "easyocr" in status
        assert "best_available" in status
        assert "supported_formats" in status
        assert ".png" in status["supported_formats"]
        assert ".jpg" in status["supported_formats"]

    def test_supported_formats(self):
        status = get_ocr_status()
        expected = [".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"]
        for fmt in expected:
            assert fmt in status["supported_formats"]

    def test_best_engine_pytesseract(self):
        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                assert _best_engine() == "pytesseract"

    def test_best_engine_easyocr(self):
        with patch("core.ocr._has_pytesseract", return_value=False):
            with patch("core.ocr._has_easyocr", return_value=True):
                assert _best_engine() == "easyocr"

    def test_best_engine_none(self):
        with patch("core.ocr._has_pytesseract", return_value=False):
            with patch("core.ocr._has_easyocr", return_value=False):
                assert _best_engine() is None


class TestExtractTextFromImage:
    """Test extract_text_from_image function."""

    def test_nonexistent_file(self, tmp_path):
        result = extract_text_from_image(tmp_path / "nonexistent.png")
        assert result is None

    def test_no_engine_available(self, tmp_path, monkeypatch):
        """When no OCR engine is available, returns None."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=False):
            with patch("core.ocr._has_easyocr", return_value=False):
                result = extract_text_from_image(img)
                assert result is None

    def test_explicit_unavailable_engine(self, tmp_path):
        """When explicit engine is not available, returns None."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=False):
            with patch("core.ocr._has_easyocr", return_value=False):
                result = extract_text_from_image(img, engine="pytesseract")
                assert result is None

    def test_pytesseract_returns_text(self, tmp_path):
        """When pytesseract returns text, it is passed through."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                with patch("core.ocr._extract_pytesseract", return_value="Hello world"):
                    result = extract_text_from_image(img)
                    assert result == "Hello world"

    def test_pytesseract_returns_none(self, tmp_path):
        """When pytesseract returns None, falls back to easyocr."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=True):
                with patch("core.ocr._extract_pytesseract", return_value=None):
                    with patch("core.ocr._extract_easyocr", return_value="Easy text"):
                        result = extract_text_from_image(img)
                        assert result == "Easy text"

    def test_explicit_engine_pytesseract(self, tmp_path):
        """When explicit engine is pytesseract, uses it."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._extract_pytesseract", return_value="Explicit text"):
                result = extract_text_from_image(img, engine="pytesseract")
                assert result == "Explicit text"

    def test_explicit_engine_easyocr(self, tmp_path):
        """When explicit engine is easyocr, uses it."""
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        with patch("core.ocr._has_easyocr", return_value=True):
            with patch("core.ocr._extract_easyocr", return_value="EasyOCR text"):
                result = extract_text_from_image(img, engine="easyocr")
                assert result == "EasyOCR text"


class TestOCRScanDirectory:
    """Test ocr_scan_directory function."""

    def test_nonexistent_directory(self, tmp_path):
        result = ocr_scan_directory(tmp_path / "nonexistent")
        assert result["status"] == "error"

    def test_no_images(self, tmp_path):
        """Directory with no image files returns empty."""
        (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")

        with patch("core.ocr._has_pytesseract", return_value=False):
            with patch("core.ocr._has_easyocr", return_value=False):
                result = ocr_scan_directory(tmp_path)
                assert result["status"] == "ok"
                assert result["scanned"] == 0
                assert "No image files" in result["message"]

    def test_with_images(self, tmp_path):
        """Directory with images returns scan report."""
        (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "scan.jpg").write_bytes(b"\xff\xd8\xff")
        (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                with patch("core.ocr._extract_pytesseract", return_value="Extracted"):
                    result = ocr_scan_directory(tmp_path)
                    assert result["status"] == "ok"
                    assert result["scanned"] == 2
                    assert result["success"] == 2
                    assert result["failed"] == 0

    def test_partial_failure(self, tmp_path):
        """Some images fail OCR, others succeed."""
        (tmp_path / "good.png").write_bytes(b"\x89PNG\r\n")
        (tmp_path / "bad.jpg").write_bytes(b"\xff\xd8\xff")

        def mock_extract(path, languages=None):
            if path.name == "good.png":
                return "Good text"
            return None

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                with patch("core.ocr._extract_pytesseract", side_effect=mock_extract):
                    result = ocr_scan_directory(tmp_path)
                    assert result["scanned"] == 2
                    assert result["success"] == 1
                    assert result["failed"] == 1

    def test_results_include_preview(self, tmp_path):
        """Each result includes a preview of extracted text."""
        (tmp_path / "test.png").write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                with patch("core.ocr._extract_pytesseract", return_value="A" * 300):
                    result = ocr_scan_directory(tmp_path)
                    assert len(result["results"]) == 1
                    assert result["results"][0]["status"] == "success"
                    assert result["results"][0]["text_length"] == 300
                    assert len(result["results"][0]["preview"]) == 200


class TestOCRIntegration:
    """Integration-style tests with mocked pytesseract."""

    def test_full_pipeline_with_mocked_pytesseract(self, tmp_path):
        """Simulate full OCR pipeline with mocked pytesseract."""
        img = tmp_path / "document.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                with patch(
                    "core.ocr._extract_pytesseract",
                    return_value="This is a test document with important information.",
                ):
                    result = extract_text_from_image(img)
                    assert result == "This is a test document with important information."

    def test_multilanguage_ocr(self, tmp_path):
        """OCR with custom languages."""
        img = tmp_path / "bilingual.png"
        img.write_bytes(b"\x89PNG\r\n")

        with patch("core.ocr._has_pytesseract", return_value=True):
            with patch("core.ocr._has_easyocr", return_value=False):
                with patch("core.ocr._extract_pytesseract", return_value="RU+EN text"):
                    result = extract_text_from_image(img, languages=["rus", "eng"])
                    assert result == "RU+EN text"
