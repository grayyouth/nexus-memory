"""
Nexus — Universal memory system for AI agents.

Core modules:
    nexus_core   — Main Nexus class (add_chunk, search, archive_session, etc.)
    ingestion    — Ingestion Pipeline (raw files → indexed chunks)
    ocr          — OCR extraction from images (pytesseract + easyocr)
    summarizer   — SessionSummarizer (auto-generated summaries)
    session_hook — SessionHook (one-call session save)
    semantic     — SemanticSearch (vector similarity search)
    watchkeeper  — Watchkeeper (automatic raw/ directory monitoring)
    config       — Configuration management (nexus_config.json)
"""

__version__ = "0.9.5"

from core.nexus_core import Nexus
from core.ingestion import IngestionPipeline
from core.ocr import extract_text_from_image, ocr_scan_directory, get_ocr_status
from core.summarizer import SessionSummarizer
from core.session_hook import SessionHook
from core.semantic import SemanticSearch
from core.watchkeeper import Watchkeeper
from core.config import Config, config

__all__ = [
    "Nexus",
    "IngestionPipeline",
    "extract_text_from_image",
    "ocr_scan_directory",
    "get_ocr_status",
    "SessionSummarizer",
    "SessionHook",
    "SemanticSearch",
    "Watchkeeper",
    "Config",
    "config",
]
