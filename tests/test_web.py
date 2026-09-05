"""
Tests for core/web.py - Web page fetching for Nexus.
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.web import (
    fetch_web_page,
    save_to_raw,
    get_web_status,
    _extract_main_content,
    _extract_title,
    _url_to_filename,
    _ContentExtractor,
)


class TestContentExtractor:
    """Test _ContentExtractor HTML content extraction."""

    def test_extract_basic_text(self):
        html = "<html><body><p>Hello world</p></body></html>"
        result = _extract_main_content(html)
        assert "Hello world" in result

    def test_extract_strips_script(self):
        html = "<html><body><script>alert(1)</script><p>visible</p></body></html>"
        result = _extract_main_content(html)
        assert "alert" not in result
        assert "visible" in result

    def test_extract_strips_style(self):
        html = '<html><body><style>.foo{color:red}</style><p>text</p></body></html>'
        result = _extract_main_content(html)
        assert "color:red" not in result
        assert "text" in result

    def test_extract_strips_nav(self):
        html = "<html><body><nav>Menu</nav><main>Content</main></body></html>"
        result = _extract_main_content(html)
        assert "Menu" not in result
        assert "Content" in result

    def test_extract_strips_footer(self):
        html = "<html><body><footer>Copyright 2024</footer><p>Article</p></body></html>"
        result = _extract_main_content(html)
        assert "Copyright" not in result
        assert "Article" in result

    def test_extract_strips_iframe(self):
        html = "<html><body><iframe src='evil.html'></iframe><p>Safe</p></body></html>"
        result = _extract_main_content(html)
        assert "evil" not in result
        assert "Safe" in result

    def test_extract_multiple_paragraphs(self):
        html = "<html><body><p>Para 1</p><p>Para 2</p><p>Para 3</p></body></html>"
        result = _extract_main_content(html)
        assert "Para 1" in result
        assert "Para 2" in result
        assert "Para 3" in result

    def test_extract_collapse_newlines(self):
        html = "<html><body><p>a</p><p>b</p><p>c</p><p>d</p></body></html>"
        result = _extract_main_content(html)
        assert "\n\n\n\n" not in result


class TestTitleExtraction:
    """Test _extract_title function."""

    def test_extract_title(self):
        html = "<html><head><title>My Page Title</title></head><body></body></html>"
        assert _extract_title(html) == "My Page Title"

    def test_extract_title_with_whitespace(self):
        html = "<html><head><title>  Title with spaces  </title></head><body></body></html>"
        assert _extract_title(html) == "Title with spaces"

    def test_extract_title_missing(self):
        html = "<html><body>No title</body></html>"
        assert _extract_title(html) == "Untitled"


class TestUrlToFilename:
    """Test _url_to_filename function."""

    def test_simple_url(self, tmp_path):
        result = _url_to_filename("https://example.com/docs", tmp_path)
        assert result.name.endswith(".html")
        assert "example" in result.name.lower()

    def test_url_with_path(self, tmp_path):
        result = _url_to_filename("https://example.com/docs/api/reference", tmp_path)
        assert result.name.endswith(".html")

    def test_url_uniqueness(self, tmp_path):
        # Create the first file, then second should get counter
        r1 = _url_to_filename("https://example.com/page", tmp_path)
        # Simulate the file existing
        r1.touch()
        r2 = _url_to_filename("https://example.com/page", tmp_path)
        assert r1 != r2

    def test_special_chars_sanitized(self, tmp_path):
        result = _url_to_filename("https://example.com/path?query=1&foo=bar", tmp_path)
        assert "?" not in result.name
        assert "&" not in result.name


class TestFetchWebPage:
    """Test fetch_web_page function with mocked HTTP."""

    def _make_mock_response(self, html: str):
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.status_code = 200
        mock_response.content = html.encode()
        mock_response.raise_for_status = MagicMock()
        return mock_response

    def test_fetch_with_mocked_requests(self, tmp_path):
        """Test fetching with mocked requests library."""
        url = "https://example.com/page"
        html = "<html><head><title>Test Page</title></head><body><p>Hello</p></body></html>"
        
        mock_resp = self._make_mock_response(html)
        
        with patch("core.web._fetch_html", return_value=html):
            result = fetch_web_page(url, timeout=5)
            
            assert result is not None
            assert result["title"] == "Test Page"
            assert "Hello" in result["content"]
            assert result["url"] == url

    def test_fetch_404(self, tmp_path):
        """Test 404 response."""
        url = "https://example.com/notfound"
        
        with patch("core.web._fetch_html", return_value=None):
            result = fetch_web_page(url, timeout=5)
            assert result is None

    def test_fetch_extract_content_false(self, tmp_path):
        """Test raw HTML return."""
        url = "https://example.com/page"
        html = "<html><body><p>Test</p></body></html>"
        
        with patch("core.web._fetch_html", return_value=html):
            result = fetch_web_page(url, timeout=5, extract_content=False)
            
            assert result is not None
            assert result["content"] == html


class TestSaveToRaw:
    """Test save_to_raw function."""

    def test_save_with_mocked_fetch(self, tmp_path):
        """Test saving a web page to raw directory."""
        url = "https://example.com/page"
        html = "<html><head><title>Test</title></head><body><p>Content</p></body></html>"
        
        raw_dir = tmp_path / "raw"
        
        with patch("core.web._fetch_html", return_value=html):
            result = save_to_raw(url, raw_dir=raw_dir, timeout=5)
            
            assert result is not None
            assert result["status"] == "saved"
            assert "Test" in result["title"]
            assert result["saved_path"] is not None
            
            # File should exist in raw_dir
            saved_file = tmp_path / result["saved_path"]
            assert saved_file.exists()

    def test_save_failed_fetch(self, tmp_path):
        """Test save with failed fetch."""
        url = "https://example.com/404"
        
        with patch("core.web._fetch_html", return_value=None):
            result = save_to_raw(url, raw_dir=tmp_path / "raw", timeout=5)
            assert result is None


class TestWebStatus:
    """Test get_web_status function."""

    def test_web_status(self):
        status = get_web_status()
        assert "requests_available" in status
        assert "urllib_available" in status
        assert status["urllib_available"] is True
        assert "timeout" in status
        assert "max_page_size_mb" in status


class TestContentExtractionEdgeCases:
    """Test edge cases for content extraction."""

    def test_empty_html(self):
        result = _extract_main_content("")
        assert result == ""

    def test_html_with_only_script(self):
        html = "<html><body><script>alert('evil')</script></body></html>"
        result = _extract_main_content(html)
        assert "evil" not in result

    def test_html_with_nested_tags(self):
        html = "<html><body><div><p><strong>Bold</strong> text</p></div></body></html>"
        result = _extract_main_content(html)
        assert "Bold" in result
        assert "text" in result

    def test_html_with_entities(self):
        html = "<html><body><p>Copyright &copy; 2024</p></body></html>"
        result = _extract_main_content(html)
        assert "Copyright" in result

    def test_html_with_form(self):
        html = "<html><body><form>Login</form><p>Article</p></body></html>"
        result = _extract_main_content(html)
        assert "Login" not in result
        assert "Article" in result

    def test_html_with_button(self):
        html = "<html><body><button>Submit</button><p>Content</p></body></html>"
        result = _extract_main_content(html)
        assert "Submit" not in result
        assert "Content" in result
