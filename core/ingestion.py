"""
Nexus Ingestion Pipeline.

Scans input_docs/raw/, extracts text from supported formats, normalizes it
to a single Markdown shape, splits into semantic chunks, and indexes chunks
into the Nexus library via Nexus.add_chunk(). Processed files are moved to
input_docs/processed/.

Supported formats: .md, .markdown, .txt, .text, .json, .jsonl, .csv, .html, .htm, .pdf,
                   .png, .jpg, .jpeg, .bmp, .tiff, .webp (via OCR)
Unsupported formats (.gif, ...) are left in raw/ and reported in the log.

Usage (CLI):
    python -m core.ingestion

Usage (Python):
    from core.nexus_core import Nexus
    from core.ingestion import IngestionPipeline
    report = IngestionPipeline(Nexus()).run()
"""

import csv
import json
import logging
import re
import shutil
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core.nexus_core import Nexus, INPUT_RAW_DIR, INPUT_PROCESSED_DIR
from core.config import config

logger = logging.getLogger(__name__)

# Target chunk size in characters (friendly to small-context local LLMs).
# Overridable via nexus_config.json → ingestion.max_chunk_chars
MAX_CHUNK_CHARS = config.max_chunk_chars

# Map of supported extensions -> kind
SUPPORTED_EXTS: Dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".text": "text",
    ".json": "json",
    ".jsonl": "jsonl",
    ".csv": "csv",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".bmp": "image",
    ".tiff": "image",
    ".tif": "image",
    ".webp": "image",
}


# ─── Utility: sanitize filename (from GigaEyes) ──────────────────────────────

def sanitize_filename(name: str) -> str:
    """Remove characters invalid in filenames.

    Adapted from GigaEyes ScreenshotStorage._sanitize_filename.
    """
    invalid_chars = '<>:"/\\|?* '
    for char in invalid_chars:
        name = name.replace(char, "_")
    # Collapse multiple underscores
    while "__" in name:
        name = name.replace("__", "_")
    return name[:80].strip("_")


# ─── Structured result container (from GigaEyes AnalysisResult) ──────────────

class IngestionResult:
    """Container for ingestion results with JSON serialization.

    Adapted from GigaEyes AnalysisResult. Provides structured output with
    metadata, stages, errors, and timing — plus JSON save/load.
    """

    def __init__(self) -> None:
        self.metadata: Dict[str, Any] = {}
        self.stages: Dict[str, Any] = {}
        self.errors: List[Dict[str, str]] = []
        self.timing: Dict[str, float] = {}
        self._start_time: Optional[float] = None

    def set_stage(self, stage_name: str, data: Any) -> None:
        """Set result for a stage."""
        self.stages[stage_name] = data

    def add_error(self, stage: str, message: str) -> None:
        """Record an error from a stage."""
        self.errors.append({"stage": stage, "message": message})

    def start_timer(self) -> None:
        """Start timing."""
        self._start_time = time.time()

    def stop_timer(self) -> None:
        """Stop timing and record total."""
        if self._start_time is not None:
            self.timing["total_seconds"] = round(time.time() - self._start_time, 3)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "pipeline_version": "0.6.0",
            "timestamp": datetime.now().isoformat(),
            "metadata": self.metadata,
            "stages": self.stages,
            "errors": self.errors,
            "timing": self.timing,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: Union[str, Path]) -> Path:
        """Save to JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        logger.info("Ingestion result saved: %s", path)
        return path

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IngestionResult":
        """Load from dictionary."""
        result = cls()
        result.metadata = data.get("metadata", {})
        result.stages = data.get("stages", {})
        result.errors = data.get("errors", [])
        result.timing = data.get("timing", {})
        return result

    @classmethod
    def from_json(cls, json_str: str) -> "IngestionResult":
        """Load from JSON string."""
        return cls.from_dict(json.loads(json_str))

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "IngestionResult":
        """Load from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())


# ─── HTML text extractor ─────────────────────────────────────────────────────


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML -> plain text extractor (headings marked with #, no external deps)."""

    BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: List[str] = []
        self._skip = 0  # depth of <style>/<script> to ignore

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "noscript"):
            self._skip += 1
        if tag in self.BLOCK_TAGS and not self._skip:
            self._parts.append("\n")
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit() and not self._skip:
            self._parts.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "noscript"):
            if self._skip:
                self._skip -= 1
        if tag in self.BLOCK_TAGS and not self._skip:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # collapse >2 newlines and strip trailing spaces per line
        lines = [ln.rstrip() for ln in raw.splitlines()]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class IngestionPipeline:
    """Main ingestion pipeline: raw files -> normalized md -> indexed chunks."""

    def __init__(
        self,
        nexus: Optional[Nexus] = None,
        raw_dir: Optional[Path] = None,
        processed_dir: Optional[Path] = None,
    ) -> None:
        self.nexus = nexus or Nexus()
        self.raw_dir = Path(raw_dir) if raw_dir else self.nexus.input_raw_dir
        self.processed_dir = Path(processed_dir) if processed_dir else self.nexus.input_processed_dir
        self.log_dir = self.nexus.base_dir / "_logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "ingestion.jsonl"

    # --- logging ---

    def _log(self, level: str, msg: str, **ctx) -> None:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "msg": msg,
            **ctx,
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # --- file discovery ---

    def _iter_raw_files(self) -> List[Path]:
        if not self.raw_dir.exists():
            return []
        return sorted(p for p in self.raw_dir.rglob("*") if p.is_file())

    def _kind(self, path: Path) -> Optional[str]:
        return SUPPORTED_EXTS.get(path.suffix.lower())

    # --- extraction ---

    def _extract_text(self, path: Path, kind: str) -> str:
        """Read raw bytes and return plain text, or empty string if unreadable."""
        if kind == "pdf":
            return self._extract_pdf(path)

        if kind == "image":
            return self._extract_image(path)

        try:
            data = path.read_bytes()
        except OSError as e:
            self._log("error", f"cannot read {path.name}", path=str(path), detail=str(e))
            return ""

        # Normalize encoding with a UTF-8-first fallback chain.
        for enc in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
            try:
                return data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return data.decode("utf-8", errors="replace")

    def _html_to_text(self, raw: str) -> str:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(raw)
        except Exception as e:  # pragma: no cover - very defensive
            self._log("warning", f"html parse issue: {e}")
        return parser.text()

    def _extract_pdf(self, path: Path) -> str:
        """Extract text from a PDF file using pypdf or PyMuPDF.

        Tries pypdf first (lighter, pure Python), falls back to PyMuPDF (fitz)
        for better compatibility with complex PDFs.
        """
        # Try pypdf first
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(str(path))
            pages = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages.append(text.strip())
            return "\n\n".join(pages) if pages else ""
        except ImportError:
            pass
        except Exception as e:
            self._log("warning", f"pypdf failed for {path.name}, trying PyMuPDF: {e}")

        # Fallback to PyMuPDF (fitz)
        try:
            import fitz  # type: ignore  # PyMuPDF
            doc = fitz.open(str(path))
            pages = []
            for page in doc:
                text = page.get_text()
                if text.strip():
                    pages.append(text.strip())
            doc.close()
            return "\n\n".join(pages)
        except ImportError:
            pass
        except Exception as e:
            self._log("error", f"PyMuPDF failed for {path.name}: {e}")

        self._log("error", f"pdf extraction failed for {path.name} (install pypdf or PyMuPDF)")
        return ""

    def _extract_image(self, path: Path) -> str:
        """Extract text from an image file using OCR.

        Tries pytesseract first (fast, free), falls back to easyocr
        (higher quality, heavier). If neither is available, returns empty string.
        """
        from core.ocr import extract_text_from_image

        text = extract_text_from_image(path)
        if text:
            return text
        self._log("warning", f"OCR returned empty for {path.name} (no engine or no text found)")
        return ""

    # --- normalization to markdown ---

    def _normalize(self, raw: str, kind: str, path: Path) -> str:
        """Convert raw content of a given kind into a single Markdown document."""
        if kind == "markdown":
            return raw.strip()

        if kind == "text":
            return raw.strip()

        if kind == "pdf":
            # PDF text is already plain text, just normalize spacing
            lines = [ln.rstrip() for ln in raw.splitlines()]
            text = "\n".join(lines)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

        if kind == "image":
            # OCR text is plain text, normalize spacing
            lines = [ln.rstrip() for ln in raw.splitlines()]
            text = "\n".join(lines)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

        if kind == "json":
            try:
                obj = json.loads(raw)
                return json.dumps(obj, indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                return raw.strip()

        if kind == "jsonl":
            rows = [ln for ln in raw.splitlines() if ln.strip()]
            if not rows:
                return ""
            return "\n".join(rows)  # each line is already a JSON object

        if kind == "csv":
            return self._csv_to_markdown(raw)

        if kind == "html":
            return self._html_to_text(raw)

        return raw.strip()

    @staticmethod
    def _csv_to_markdown(raw: str) -> str:
        rows: List[List[str]] = []
        try:
            for row in csv.reader(raw.splitlines()):
                rows.append([c.strip() for c in row])
        except Exception:
            return raw.strip()
        if not rows:
            return ""
        header = rows[0]
        out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
        for row in rows[1:]:
            row = (row + [""] * len(header))[: len(header)]
            out.append("| " + " | ".join(row) + " |")
        return "\n".join(out)

    # --- front matter (YAML-like metadata in .md files) ---

    @staticmethod
    def _parse_front_matter(text: str) -> Tuple[Dict[str, object], str]:
        """Extract a simple `--- key: value ---` front matter block from markdown.

        Returns (metadata, body_without_front_matter).
        """
        if not text.startswith("---"):
            return {}, text
        lines = text.splitlines()
        if len(lines) < 2:
            return {}, text
        end = None
        for i in range(1, min(len(lines), 50)):  # front matter must be near the top
            if lines[i].strip() == "---":
                end = i
                break
        if end is None:
            return {}, text

        meta: Dict[str, object] = {}
        for ln in lines[1:end]:
            if ":" not in ln:
                continue
            key, _, value = ln.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            if value.startswith("[") and value.endswith("]"):
                items = [v.strip().strip("\"'#") for v in value[1:-1].split(",") if v.strip()]
                meta[key] = items
            else:
                meta[key] = value.strip("\"'# ")
        body = "\n".join(lines[end + 1:]).strip()
        return meta, body

    # --- chunking ---

    def _chunk_markdown(self, md_text: str) -> List[str]:
        """Split markdown by headings first, then by size if a section is too big."""
        lines = md_text.splitlines()
        sections: List[Tuple[str, List[str]]] = []  # (heading, body lines)
        cur_heading, cur_body = "", []
        for ln in lines:
            if re.match(r"^#{1,6}\s", ln):  # heading line
                if cur_body or cur_heading:
                    sections.append((cur_heading, cur_body))
                cur_heading, cur_body = ln, []
            else:
                cur_body.append(ln)
        if cur_body or cur_heading:
            sections.append((cur_heading, cur_body))

        chunks: List[str] = []
        for heading, body in sections:
            all_lines = ([heading] + body) if heading else body
            section_text = "\n".join(all_lines).strip()
            if not section_text:
                continue
            if len(section_text) <= MAX_CHUNK_CHARS:
                chunks.append(section_text)
                continue
            # split the oversized section by lines, keeping heading in the first chunk
            buf, cnt = [], 0
            first = True
            for ln in all_lines:
                size = len(ln) + 1
                if not first and buf and cnt + size > MAX_CHUNK_CHARS:
                    chunks.append("\n".join(buf).strip())
                    buf, cnt = [], 0
                buf.append(ln)
                cnt += size
                first = False
            if buf:
                chunks.append("\n".join(buf).strip())
        return [c for c in chunks if c]

    def _chunk_plain(self, text: str) -> List[str]:
        """Split plain text into chunks by paragraphs/size."""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not paragraphs:
            return []
        chunks: List[str] = []
        buf, cnt = [], 0
        for p in paragraphs:
            size = len(p) + 2
            if buf and cnt + size > MAX_CHUNK_CHARS:
                chunks.append("\n\n".join(buf).strip())
                buf, cnt = [], 0
            buf.append(p)
            cnt += size
        if buf:
            chunks.append("\n\n".join(buf).strip())
        return chunks

    def _chunk(self, text: str, kind: str) -> List[str]:
        if kind in ("markdown", "pdf", "image"):
            return self._chunk_markdown(text)
        return self._chunk_plain(text)

    # --- processing ---

    def _run_stage(
        self,
        result: IngestionResult,
        stage_name: str,
        stage_func,
        *args,
        **kwargs,
    ) -> None:
        """Run a single ingestion stage with timing and error handling."""
        start = time.time()
        try:
            data = stage_func(*args, **kwargs)
            result.set_stage(stage_name, data)
            elapsed = round(time.time() - start, 3)
            result.timing[f"{stage_name}_seconds"] = elapsed
            logger.info("Stage '%s' completed in %.3fs", stage_name, elapsed)
        except Exception as e:
            error_msg = f"Stage '{stage_name}' failed: {e}"
            logger.error(error_msg)
            result.add_error(stage_name, str(e))
            result.timing[f"{stage_name}_seconds"] = round(time.time() - start, 3)

    def process_file(self, path: Path) -> Dict[str, object]:
        """Extract, normalize, chunk, index and archive a single file.

        Returns a report dict:
          {path, kind, status, chunks, chunk_ids, skipped_reason?}
        """
        kind = self._kind(path)
        report: Dict[str, object] = {
            "path": str(path.relative_to(self.nexus.base_dir)),
            "kind": kind or "unknown",
        }
        if kind is None:
            report.update(
                status="skipped",
                reason=f"unsupported format {path.suffix} (supported: {', '.join(sorted(SUPPORTED_EXTS))})",
            )
            self._log("warning", "skipped unsupported file", path=str(path), suffix=path.suffix)
            return report

        raw = self._extract_text(path, kind)
        if not raw.strip():
            report.update(status="error", reason="empty content")
            self._log("error", "empty content", path=str(path))
            return report

        md_text = self._normalize(raw, kind, path)

        # For markdown files, respect front-matter meta (tags / project / source).
        meta: Dict[str, object] = {}
        if kind == "markdown":
            meta, md_text = self._parse_front_matter(md_text)

        chunks = self._chunk(md_text, kind)
        if not chunks:
            report.update(status="error", reason="no chunks produced")
            self._log("error", "no chunks", path=str(path))
            return report

        tags: List[str] = [t for t in (meta.get("tags") or [])]
        if tags:
            tags = [t if t.startswith("#") else f"#{t}" for t in tags]
        # Always add a source-kind tag so data can be filtered by origin.
        if f"#source:{kind}" not in tags:
            tags.append(f"#source:{kind}")
        if "#doc" not in tags:
            tags.append("#doc")

        project_id = str(meta["project"]) if meta.get("project") else None
        source = str(meta.get("source")) if meta.get("source") else str(path.relative_to(self.nexus.base_dir))

        chunk_ids: List[str] = []
        for chunk in chunks:
            cid = self.nexus.add_chunk(
                content=chunk,
                tags=tags,
                project_id=project_id,
                source=source,
                agent_id=None,
            )
            chunk_ids.append(cid)

        self._archive_file(path)
        report.update(status="ingested", chunks=len(chunk_ids), chunk_ids=chunk_ids, tags=tags, project=project_id)
        self._log(
            "info",
            f"ingested {path.name}: {len(chunk_ids)} chunks -> {project_id or 'general'}",
            path=str(path),
            chunks=len(chunk_ids),
            project=project_id,
        )
        return report

    def _archive_file(self, path: Path) -> None:
        """Move a processed file into input_docs/processed/ preserving relative structure."""
        rel = path.relative_to(self.raw_dir)
        dest = self.processed_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():  # avoid overwrite: add timestamp suffix
            dest = dest.with_name(f"{dest.stem}_{datetime.now():%Y%m%d_%H%M%S}{dest.suffix}")
        shutil.move(str(path), str(dest))

    def run(self, output: Optional[Union[str, Path]] = None) -> Dict[str, object]:
        """Scan raw_dir, process every supported file, return a summary report.

        Args:
            output: Optional path to save structured JSON result.
        """
        result = IngestionResult()
        result.start_timer()
        result.metadata["raw_dir"] = str(self.raw_dir)
        result.metadata["processed_dir"] = str(self.processed_dir)

        files = self._iter_raw_files()
        summary: Dict[str, object] = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "scanned": len(files),
            "ingested": 0,
            "skipped": 0,
            "errors": 0,
            "files": [],
        }

        for f in files:
            rep = self.process_file(f)
            summary["files"].append(rep)
            status = rep.get("status")
            if status == "ingested":
                summary["ingested"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            elif status == "error":
                summary["errors"] += 1

        result.stop_timer()
        result.metadata["summary"] = summary

        if output:
            result.save(output)

        summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
        return summary


# --- CLI entry point ---

if __name__ == "__main__":
    pipeline = IngestionPipeline()
    report = pipeline.run()
    print(json.dumps(report, indent=2, ensure_ascii=False))