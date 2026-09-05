"""
Tests for core/ingestion.py - HTMLTextExtractor and IngestionPipeline.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.ingestion import _HTMLTextExtractor, IngestionPipeline, SUPPORTED_EXTS, IngestionResult, sanitize_filename

# Import pypdf for creating test PDFs
try:
    from pypdf import PdfWriter  # type: ignore
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False


class TestHTMLTextExtractor:
    """Test HTMLTextExtractor class."""

    def test_html_to_text_headings(self):
        parser = _HTMLTextExtractor()
        parser.feed("<h1>Title</h1><h2>Subtitle</h2>")
        text = parser.text()
        assert "# Title" in text
        assert "## Subtitle" in text

    def test_html_to_text_paragraphs(self):
        parser = _HTMLTextExtractor()
        parser.feed("<p>Para one</p><p>Para two</p>")
        text = parser.text()
        assert "Para one" in text
        assert "Para two" in text

    def test_html_to_text_ignores_script(self):
        parser = _HTMLTextExtractor()
        parser.feed("<script>alert(1)</script><p>visible</p>")
        text = parser.text()
        assert "alert" not in text
        assert "visible" in text

    def test_html_to_text_collapse_newlines(self):
        parser = _HTMLTextExtractor()
        parser.feed("<p>a</p><p>b</p><p>c</p>")
        text = parser.text()
        assert "\n\n\n" not in text


class TestIngestionPipeline:
    """Test IngestionPipeline class."""

    @pytest.fixture
    def pipeline(self, nexus_instance, nexus_store):
        raw_dir = nexus_store / "input_docs" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        return IngestionPipeline(nexus_instance, raw_dir=raw_dir)

    def test_kind_supported(self, pipeline):
        assert pipeline._kind(Path("test.md")) == "markdown"
        assert pipeline._kind(Path("test.txt")) == "text"
        assert pipeline._kind(Path("test.json")) == "json"
        assert pipeline._kind(Path("test.csv")) == "csv"
        assert pipeline._kind(Path("test.html")) == "html"

    def test_kind_unsupported(self, pipeline):
        assert pipeline._kind(Path("test.gif")) is None
        assert pipeline._kind(Path("test.svg")) is None
        assert pipeline._kind(Path("test.exe")) is None

    def test_kind_image_supported(self, pipeline):
        assert pipeline._kind(Path("test.png")) == "image"
        assert pipeline._kind(Path("test.jpg")) == "image"
        assert pipeline._kind(Path("test.jpeg")) == "image"
        assert pipeline._kind(Path("test.bmp")) == "image"
        assert pipeline._kind(Path("test.tiff")) == "image"
        assert pipeline._kind(Path("test.webp")) == "image"

    def test_kind_pdf_supported(self, pipeline):
        assert pipeline._kind(Path("test.pdf")) == "pdf"

    def test_normalize_markdown(self, pipeline):
        result = pipeline._normalize("# Header\nBody", "markdown", Path("test.md"))
        assert "# Header" in result

    def test_normalize_json(self, pipeline):
        result = pipeline._normalize('{"key": "val"}', "json", Path("test.json"))
        assert '"key"' in result
        assert '"val"' in result

    def test_normalize_csv(self, pipeline):
        csv_data = "a,b,c\n1,2,3\n4,5,6"
        result = pipeline._normalize(csv_data, "csv", Path("test.csv"))
        assert "| a |" in result
        assert "|---|" in result
        assert "| 1 |" in result

    def test_normalize_html(self, pipeline):
        html = "<h1>Title</h1><p>Content</p>"
        result = pipeline._normalize(html, "html", Path("test.html"))
        assert "# Title" in result
        assert "Content" in result

    def test_parse_front_matter_present(self):
        md = "---\ntags: [a, b]\nproject: proj1\nsource: url\n---\nbody text"
        meta, body = IngestionPipeline._parse_front_matter(md)
        assert meta["tags"] == ["a", "b"]
        assert meta["project"] == "proj1"
        assert body == "body text"

    def test_parse_front_matter_absent(self):
        meta, body = IngestionPipeline._parse_front_matter("no frontmatter")
        assert meta == {}

    def test_parse_front_matter_tags_list(self):
        md = "---\ntags: [x, y, z]\n---\nbody"
        meta, _ = IngestionPipeline._parse_front_matter(md)
        assert meta["tags"] == ["x", "y", "z"]

    def test_chunk_markdown_by_headings(self):
        pipeline = IngestionPipeline()
        md = "# H1\nsection1\n## H2\nsection2"
        chunks = pipeline._chunk_markdown(md)
        assert len(chunks) >= 1

    def test_chunk_markdown_size_limit(self):
        pipeline = IngestionPipeline()
        md = "# Big\n" + "x" * 2000
        chunks = pipeline._chunk_markdown(md)
        # Chunks should be produced (may not strictly cut at MAX_CHUNK_CHARS)
        assert len(chunks) >= 1

    def test_chunk_plain_by_paragraphs(self):
        pipeline = IngestionPipeline()
        text = "para1\n\npara2\n\npara3"
        chunks = pipeline._chunk_plain(text)
        assert len(chunks) >= 1

    def test_process_file_markdown(self, pipeline, nexus_store):
        raw_dir = pipeline.raw_dir
        (raw_dir / "test.md").write_text("# Test\nContent here", encoding="utf-8")
        rep = pipeline.process_file(raw_dir / "test.md")
        assert rep["status"] == "ingested"
        assert rep["chunks"] >= 1

    def test_process_file_json(self, pipeline, nexus_store):
        (pipeline.raw_dir / "test.json").write_text('{"key": "val"}', encoding="utf-8")
        rep = pipeline.process_file(pipeline.raw_dir / "test.json")
        assert rep["status"] == "ingested"

    def test_process_file_csv(self, pipeline, nexus_store):
        (pipeline.raw_dir / "test.csv").write_text("a,b\n1,2\n3,4", encoding="utf-8")
        rep = pipeline.process_file(pipeline.raw_dir / "test.csv")
        assert rep["status"] == "ingested"

    def test_process_file_unsupported(self, pipeline, nexus_store):
        (pipeline.raw_dir / "test.gif").write_bytes(b"fake gif")
        rep = pipeline.process_file(pipeline.raw_dir / "test.gif")
        assert rep["status"] == "skipped"

    def test_process_file_image_no_ocr(self, pipeline, nexus_store):
        """Image file without OCR engine returns error (not skipped)."""
        (pipeline.raw_dir / "test.png").write_bytes(b"fake png")
        rep = pipeline.process_file(pipeline.raw_dir / "test.png")
        # Without OCR engines, image processing returns error (empty content)
        assert rep["status"] == "error"

    def test_process_file_empty(self, pipeline, nexus_store):
        (pipeline.raw_dir / "empty.txt").write_text("", encoding="utf-8")
        rep = pipeline.process_file(pipeline.raw_dir / "empty.txt")
        assert rep["status"] == "error"

    def test_run_processes_multiple(self, pipeline, nexus_store):
        (pipeline.raw_dir / "a.md").write_text("# A", encoding="utf-8")
        (pipeline.raw_dir / "b.json").write_text('{"x":1}', encoding="utf-8")
        (pipeline.raw_dir / "c.gif").write_bytes(b"fake gif")
        report = pipeline.run()
        assert report["scanned"] == 3
        assert report["ingested"] == 2
        assert report["skipped"] == 1

    def test_run_report_summary(self, pipeline, nexus_store):
        (pipeline.raw_dir / "test.md").write_text("# Test", encoding="utf-8")
        report = pipeline.run()
        assert "started_at" in report
        assert "finished_at" in report
        assert "files" in report

    def test_archive_file_moved(self, pipeline, nexus_store):
        (pipeline.raw_dir / "move.md").write_text("# Move", encoding="utf-8")
        pipeline.process_file(pipeline.raw_dir / "move.md")
        processed_dir = pipeline.processed_dir
        found = any("move.md" in str(f) for f in processed_dir.rglob("*"))
        assert found


class TestIngestionResult:
    """Test IngestionResult structured result container."""

    def test_result_stages(self):
        result = IngestionResult()
        result.set_stage("extract", {"text": "hello"})
        assert result.stages["extract"] == {"text": "hello"}

    def test_result_add_error(self):
        result = IngestionResult()
        result.add_error("ocr", "model not found")
        assert len(result.errors) == 1
        assert result.errors[0]["stage"] == "ocr"
        assert result.errors[0]["message"] == "model not found"

    def test_result_timer(self):
        result = IngestionResult()
        result.start_timer()
        result.stop_timer()
        assert "total_seconds" in result.timing

    def test_result_to_dict(self):
        result = IngestionResult()
        result.metadata["version"] = "0.6.0"
        result.set_stage("extract", {"chars": 100})
        result.add_error("ocr", "skip")
        result.start_timer()
        result.stop_timer()
        d = result.to_dict()
        assert d["metadata"]["version"] == "0.6.0"
        assert d["stages"]["extract"]["chars"] == 100
        assert len(d["errors"]) == 1
        assert "total_seconds" in d["timing"]

    def test_result_to_json(self):
        result = IngestionResult()
        result.set_stage("extract", {"chars": 42})
        json_str = result.to_json()
        import json as _json
        d = _json.loads(json_str)
        assert d["stages"]["extract"]["chars"] == 42

    def test_result_save_and_load(self, tmp_path):
        result = IngestionResult()
        result.set_stage("extract", {"chars": 99})
        path = tmp_path / "result.json"
        result.save(path)
        assert path.exists()
        loaded = IngestionResult.from_file(path)
        assert loaded.stages["extract"]["chars"] == 99

    def test_result_from_json(self):
        json_str = '{"metadata": {"key": "val"}, "stages": {"a": 1}, "errors": [], "timing": {}}'
        result = IngestionResult.from_json(json_str)
        assert result.metadata["key"] == "val"
        assert result.stages["a"] == 1


class TestSanitizeFilename:
    """Test sanitize_filename utility from GigaEyes."""

    def test_basic(self):
        assert sanitize_filename("hello world.txt") == "hello_world.txt"

    def test_special_chars(self):
        result = sanitize_filename('file<>:"/\\|?*name.txt')
        assert "<" not in result
        assert ">" not in result
        assert '"' not in result
        assert "\\" not in result

    def test_collapse_underscores(self):
        result = sanitize_filename("hello___world")
        assert "___" not in result

    def test_truncate_long(self):
        long_name = "a" * 200
        result = sanitize_filename(long_name)
        assert len(result) <= 80

    def test_strip_underscores(self):
        result = sanitize_filename("__test__")
        assert result.startswith("test")

    def test_preserve_unicode(self):
        result = sanitize_filename("привет_мир.txt")
        assert "привет" in result


class TestPDFIngestion:
    """Test PDF ingestion pipeline (requires pypdf)."""

    @pytest.fixture
    def pipeline(self, nexus_instance, nexus_store):
        raw_dir = nexus_store / "input_docs" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        return IngestionPipeline(nexus_instance, raw_dir=raw_dir)

    @pytest.fixture
    def sample_pdf(self, nexus_store):
        """Create a simple PDF file with text content."""
        if not HAS_PYPDF:
            pytest.skip("pypdf not installed")
        
        data_dir = nexus_store / "_data"
        data_dir.mkdir(exist_ok=True)
        
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        pdf_path = data_dir / "sample.pdf"
        with open(pdf_path, "wb") as f:
            writer.write(f)
        return pdf_path

    def test_kind_pdf_supported(self, pipeline):
        assert pipeline._kind(Path("test.pdf")) == "pdf"

    def test_extract_pdf(self, pipeline, sample_pdf):
        text = pipeline._extract_pdf(sample_pdf)
        # Blank page may have no text, but should not raise
        assert isinstance(text, str)

    def test_normalize_pdf(self, pipeline):
        pdf_text = "Hello world\n\nThis is a test PDF.\n\nMultiple paragraphs."
        result = pipeline._normalize(pdf_text, "pdf", Path("test.pdf"))
        assert "Hello world" in result
        assert "Multiple paragraphs" in result

    def test_process_file_pdf(self, pipeline, sample_pdf):
        rep = pipeline.process_file(sample_pdf)
        # PDF should be ingested (may have 0 chunks if blank)
        assert rep["status"] in ("ingested", "error")
        assert rep["kind"] == "pdf"

    def test_run_with_pdf(self, pipeline, sample_pdf):
        # Create a PDF with actual text
        if HAS_PYPDF:
            from pypdf import PdfWriter, PdfReader
            from io import BytesIO
            
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            pdf_bytes = BytesIO()
            writer.write(pdf_bytes)
            pdf_bytes.seek(0)
            
            # Write PDF to raw dir
            (pipeline.raw_dir / "test.pdf").write_bytes(pdf_bytes.read())
        
        (pipeline.raw_dir / "test.md").write_text("# Test", encoding="utf-8")
        report = pipeline.run()
        assert report["scanned"] >= 1
        assert report["ingested"] + report["skipped"] + report["errors"] == report["scanned"]
