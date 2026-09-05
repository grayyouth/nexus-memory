"""
Nexus Web — скачивание веб-страниц по URL для индексации в Nexus.

Скачивает HTML-страницы, извлекает основной контент, сохраняет в
`input_docs/raw/` для автоматической обработки IngestionPipeline.

Использование:
    from core.web import fetch_web_page, save_to_raw

    # Скачать и сохранить
    result = save_to_raw("https://example.com/docs", project_id="MyProject")

    # Скачать и получить HTML
    html = fetch_web_page("https://example.com/docs")

    # Сканировать сайт (все ссылки до указанной глубины)
    from core.web import crawl_site
    report = crawl_site("https://example.com/docs", max_depth=2, max_pages=20)
"""

import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# --- Configuration ---

DEFAULT_TIMEOUT = 30          # seconds
DEFAULT_USER_AGENT = (
    "NexusMemory/0.9.3 (+https://github.com/nexus-memory) "
    "AI-Agent-Web-Scraper"
)
MAX_PAGE_SIZE = 5 * 1024 * 1024  # 5 MB


# --- HTML content extractor ---

class _ContentExtractor(HTMLParser):
    """Extract main content from HTML, stripping scripts, styles, nav, etc."""

    SKIP_TAGS = {"script", "style", "noscript", "iframe", "svg", "head"}
    REMOVE_TAGS = {"nav", "footer", "header", "aside", "form", "button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip_depth = 0  # depth of <script>/<style> blocks
        self._remove_depth = 0  # depth of <nav>/<footer> blocks
        self._in_main = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.REMOVE_TAGS:
            self._remove_depth += 1
            self._parts.append("\n---\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag in self.REMOVE_TAGS and self._remove_depth > 0:
            self._remove_depth -= 1
            self._parts.append("\n---\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and self._remove_depth == 0 and data.strip():
            self._parts.append(data)

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            self._skip_depth -= 1

    def content(self) -> str:
        text = "".join(self._parts)
        # Clean up whitespace
        lines = [ln.rstrip() for ln in text.splitlines()]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _extract_main_content(html: str) -> str:
    """Extract readable content from HTML, removing scripts, styles, nav, etc."""
    parser = _ContentExtractor()
    try:
        parser.feed(html)
    except Exception as e:
        logger.warning("HTML content extraction failed: %s", e)
        return html[:10000]  # fallback: return raw HTML truncated
    return parser.content()


def _extract_title(html: str) -> str:
    """Extract page title from HTML."""
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return "Untitled"


def _extract_favicon_url(html: str, base_url: str) -> Optional[str]:
    """Extract favicon URL from HTML."""
    match = re.search(r'href=["\']([^"\']*favicon[^"\']*)["\']', html, re.IGNORECASE)
    if match:
        return urljoin(base_url, match.group(1))
    return None


# --- HTTP fetching ---

def _fetch_html(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
    max_size: int = MAX_PAGE_SIZE,
) -> Optional[str]:
    """Fetch HTML content from a URL using requests or urllib.

    Tries `requests` first (better error handling), falls back to
    `urllib.request` (stdlib, no dependencies).
    """
    # Try requests first
    try:
        import requests  # type: ignore
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
            allow_redirects=True,
        )
        resp.raise_for_status()
        if len(resp.content) > max_size:
            logger.warning("Page too large (%d bytes): %s", len(resp.content), url)
            return None
        return resp.text
    except ImportError:
        pass
    except Exception as e:
        logger.warning("requests failed for %s: %s", url, e)

    # Fallback to urllib
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if len(data) > max_size:
                logger.warning("Page too large (%d bytes): %s", len(data), url)
                return None
            # Try to decode
            for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
                try:
                    return data.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return data.decode("utf-8", errors="replace")
    except Exception as e:
        logger.error("urllib failed for %s: %s", url, e)
        return None


# --- URL to filename ---

def _url_to_filename(url: str, raw_dir: Path) -> Path:
    """Convert a URL to a safe filename in raw_dir."""
    parsed = urlparse(url)
    # Use domain + path as filename
    domain = parsed.netloc.replace(".", "_").replace("www_", "")
    path = parsed.path.strip("/") or "index"
    # Sanitize path components
    path = re.sub(r"[^a-zA-Z0-9_\-./]", "_", path)
    path = path.rstrip("/")
    filename = f"{domain}{path or '/index'}.html"
    # Sanitize filename
    filename = re.sub(r"[<>:\"/\\|?* ]", "_", filename)
    while "__" in filename:
        filename = filename.replace("__", "_")
    filename = filename[:150]

    # Ensure unique name by checking and incrementing
    target = raw_dir / filename
    counter = 1
    original = target
    while target.exists():
        name, ext = original.stem.rsplit("_", 1)
        # Check if the last part is a counter number
        try:
            int(name.rsplit("_", 1)[-1])
            # Already has counter, increment
            base = original.stem.rsplit("_", 1)[0]
        except (ValueError, IndexError):
            base = original.stem
        target = raw_dir / f"{base}_{counter}.{original.suffix}"
        counter += 1
    return target


# --- Public API ---

def fetch_web_page(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    extract_content: bool = True,
) -> Optional[Dict[str, Any]]:
    """Fetch a web page and return its content.

    Args:
        url: URL to fetch.
        timeout: Request timeout in seconds.
        extract_content: If True, extract main content (strip scripts/styles).

    Returns:
        Dict with url, title, content (or html), status, and timestamp.
        None if fetch failed.
    """
    logger.info("Fetching: %s", url)
    html = _fetch_html(url, timeout=timeout)
    if not html:
        return None

    title = _extract_title(html)
    content = _extract_main_content(html) if extract_content else html

    return {
        "url": url,
        "title": title,
        "content": content,
        "html_length": len(html),
        "content_length": len(content),
        "timestamp": datetime.now().isoformat(),
    }


def save_to_raw(
    url: str,
    raw_dir: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch a web page and save it to input_docs/raw/ for ingestion.

    Args:
        url: URL to fetch.
        raw_dir: Directory to save to (default: nexus input_docs/raw/).
        timeout: Request timeout in seconds.
        project_id: Optional project ID for metadata.

    Returns:
        Report dict with url, saved_path, and metadata.
        None if fetch or save failed.
    """
    from core.nexus_core import Nexus

    nm = Nexus()
    target_dir = raw_dir or nm.input_raw_dir
    target_dir.mkdir(parents=True, exist_ok=True)

    result = fetch_web_page(url, timeout=timeout)
    if not result:
        logger.error("Failed to fetch: %s", url)
        return None

    # Add front-matter metadata
    meta_lines = [
        "---",
        f"source: {url}",
        f"title: {result['title']}",
        f"saved_at: {result['timestamp']}",
    ]
    if project_id:
        meta_lines.append(f"project: {project_id}")
    meta_lines.append("---")
    meta_lines.append("")

    content = "\n".join(meta_lines) + result["content"]

    target = _url_to_filename(url, target_dir)
    target.write_text(content, encoding="utf-8")
    logger.info("Saved: %s -> %s", url, target)

    # Compute relative path for reporting
    try:
        saved_path = str(target.relative_to(nm.base_dir))
    except ValueError:
        saved_path = str(target)

    return {
        "url": url,
        "title": result["title"],
        "saved_path": saved_path,
        "content_length": len(result["content"]),
        "status": "saved",
    }


def web_scan_directory(
    directory: str | Path,
    engine: Optional[str] = None,
    languages: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Alias for ocr_scan_directory (kept for backward compat)."""
    from core.ocr import ocr_scan_directory
    return ocr_scan_directory(directory, engine=engine, languages=languages)


def get_web_status() -> Dict[str, Any]:
    """Return information about web fetching capabilities."""
    has_requests = False
    try:
        import requests  # noqa: F401
        has_requests = True
    except ImportError:
        pass

    return {
        "requests_available": has_requests,
        "urllib_available": True,  # stdlib
        "timeout": DEFAULT_TIMEOUT,
        "user_agent": DEFAULT_USER_AGENT,
        "max_page_size_mb": MAX_PAGE_SIZE / (1024 * 1024),
    }
