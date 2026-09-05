# -*- coding: utf-8 -*-
"""
Nexus Source Trust — оценка доверия к источнику (backlog ROADMAP «Оценка
доверия к источнику»).

Каждому чанку присваивается вес доверия 0..1 в зависимости от источника:
официальная документация ценнее случайного блога.

Правила (настраиваются через nexus_config.json -> section "trust"):
- URL из trusted_domains (домен или поддомен), домены-префиксы docs./
  developer./learn./api. и зоны .gov/.edu -> trusted_trust (по умолчанию 1.0);
- прочий URL -> unknown_url_trust (0.4);
- локальный файл/документ (.md/.txt/.rst/.adoc, упоминание docs в пути,
  путь внутри nexus_store) -> local_doc_trust (0.8);
- пустой или неопределимый источник -> default_trust (0.5).

Использование:
    from core.trust import compute_trust
    trust = compute_trust("https://docs.python.org/3/library/os.html")  # 1.0
"""

from typing import List, Optional
from urllib.parse import urlparse

from core.config import config

# Fallback list when config has no trusted_domains (docs/developer prefix
# rules and gov/edu zones are hardcoded below and always apply).
DEFAULT_TRUSTED_DOMAINS: List[str] = [
    "wikipedia.org", "python.org", "mozilla.org", "microsoft.com",
    "arxiv.org", "github.com", "oracle.com", "redhat.com",
]

# Domain labels that mark official documentation hosts (docs.python.org,
# developer.mozilla.org, learn.microsoft.com, api.example.com, ...).
_TRUSTED_PREFIX_LABELS = ("docs", "developer", "developers", "learn", "api", "help")
# TLDs that are inherently authoritative.
_TRUSTED_SUFFIX_TLDS = ("gov", "edu", "mil")


def _domain_of(source: str) -> str:
    """Extract lowercase host from a URL-ish source string ('' if none)."""
    s = source.strip()
    if "://" not in s:
        s = "http://" + s
    try:
        netloc = urlparse(s).netloc
    except ValueError:
        return ""
    host = netloc.split("@")[-1].split(":")[0].strip(".").lower()
    return host


def _is_trusted_domain(host: str, patterns: List[str]) -> bool:
    """True if host equals / is a subdomain of a pattern, has a trusted
    prefix label (docs.python.org) or an authoritative TLD (.gov/.edu)."""
    d = host.lower().lstrip(".")
    if not d:
        return False
    tld = d.rsplit(".", 1)[-1] if "." in d else ""
    if tld in _TRUSTED_SUFFIX_TLDS:
        return True
    first_label = d.split(".", 1)[0]
    if first_label in _TRUSTED_PREFIX_LABELS:
        return True
    for pat in patterns:
        p = str(pat).lower().strip().lstrip(".")
        if not p:
            continue
        if d == p or d.endswith("." + p):
            return True
    return False


def compute_trust(source: Optional[str]) -> float:
    """
    Compute a trust weight (0..1) for a chunk source.

    Args:
        source: URL, file path or any provenance string (may be None).

    Returns:
        Float in [0, 1]; higher = more trustworthy source.
    """
    section = config.trust
    default = float(section.get("default_trust", 0.5))
    if not source or not str(source).strip():
        return default

    s = str(source).strip()
    is_url = "://" in s or s.lower().startswith("www.")

    if is_url:
        host = _domain_of(s)
        if not host:
            return default
        patterns = section.get("trusted_domains", DEFAULT_TRUSTED_DOMAINS)
        if _is_trusted_domain(host, patterns):
            return float(section.get("trusted_trust", 1.0))
        return float(section.get("unknown_url_trust", 0.4))

    # Local file / document source
    low = s.lower()
    if (
        low.endswith((".md", ".txt", ".rst", ".adoc"))
        or "docs" in low
        or low.startswith("nexus_store")
        or low.startswith("sessions")
    ):
        return float(section.get("local_doc_trust", 0.8))
    return default


def clamp_trust(value) -> float:
    """Clamp a trust value into [0.0, 1.0]."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(config.trust.get("default_trust", 0.5))
    return max(0.0, min(1.0, v))

