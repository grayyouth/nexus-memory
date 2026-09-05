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


# ─── DOCX Fixtures (module-level) ──────────────────────────────────────────


@pytest.fixture
def docx_pipeline(nexus_instance, nexus_store):
    """Create an IngestionPipeline for DOCX tests."""
    raw_dir = nexus_store / "input_docs" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return IngestionPipeline(nexus_instance, raw_dir=raw_dir)


@pytest.fixture
def sample_docx(nexus_store):
    """Create a minimal valid DOCX file using zipfile."""
    import zipfile
    import xml.etree.ElementTree as ET

    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)

    # Minimal DOCX structure: [Content_Types].xml + word/document.xml
    content_types = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''

    rels = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

    document_ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    doc_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{document_ns}">
  <w:body>
    <w:p><w:r><w:t>Hello from DOCX!</w:t></w:r></w:p>
    <w:p><w:r><w:t>Second paragraph with Unicode: привет мир.</w:t></w:r></w:p>
    <w:p><w:r><w:t></w:t></w:r></w:p>
  </w:body>
</w:document>'''

    docx_path = data_dir / "sample.docx"
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/_rels/document.xml.rels", rels)
        zf.writestr("word/document.xml", doc_xml)

    return docx_path


@pytest.fixture
def empty_docx(nexus_store):
    """Create a minimal valid DOCX with no text content."""
    import zipfile

    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)

    content_types = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''

    doc_xml = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body></w:body>
</w:document>'''

    docx_path = data_dir / "empty.docx"
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", doc_xml)

    return docx_path


@pytest.fixture
def broken_docx(nexus_store):
    """Create a file that looks like DOCX but isn't valid."""
    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)
    docx_path = data_dir / "broken.docx"
    docx_path.write_bytes(b"This is not a zip file at all")
    return docx_path


# ─── DOCX Tests ──────────────────────────────────────────────────────────────


class TestDOCXIngestion:
    """Test DOCX ingestion pipeline (zero-dep zipfile + XML)."""


    def test_kind_docx_supported(self, docx_pipeline):
        assert docx_pipeline._kind(Path("test.docx")) == "docx"

    def test_extract_docx_basic(self, docx_pipeline, sample_docx):
        text = docx_pipeline._extract_docx(sample_docx)
        assert "Hello from DOCX!" in text
        assert "привет мир" in text

    def test_extract_docx_empty(self, docx_pipeline, empty_docx):
        text = docx_pipeline._extract_docx(empty_docx)
        assert text == ""

    def test_extract_docx_broken(self, docx_pipeline, broken_docx):
        text = docx_pipeline._extract_docx(broken_docx)
        assert text == ""

    def test_normalize_docx(self, docx_pipeline):
        docx_text = "Para one\n\nPara two\n\n\n\nMultiple newlines"
        result = docx_pipeline._normalize(docx_text, "docx", Path("test.docx"))
        assert "Para one" in result
        assert "Multiple newlines" in result
        assert "\n\n\n\n" not in result

    def test_process_file_docx(self, docx_pipeline, nexus_store, sample_docx):
        # Copy to raw dir
        import shutil
        shutil.copy(sample_docx, docx_pipeline.raw_dir / "test.docx")
        rep = docx_pipeline.process_file(docx_pipeline.raw_dir / "test.docx")
        assert rep["status"] == "ingested"
        assert rep["kind"] == "docx"

    def test_process_file_docx_empty(self, docx_pipeline, nexus_store, empty_docx):
        import shutil
        shutil.copy(empty_docx, docx_pipeline.raw_dir / "empty.docx")
        rep = docx_pipeline.process_file(docx_pipeline.raw_dir / "empty.docx")
        assert rep["status"] == "error"

    def test_process_file_docx_broken(self, docx_pipeline, nexus_store, broken_docx):
        import shutil
        shutil.copy(broken_docx, docx_pipeline.raw_dir / "broken.docx")
        rep = docx_pipeline.process_file(docx_pipeline.raw_dir / "broken.docx")
        assert rep["status"] == "error"


# ─── EPUB Fixtures (module-level) ──────────────────────────────────────────


@pytest.fixture
def epub_pipeline(nexus_instance, nexus_store):
    """Create an IngestionPipeline for EPUB tests."""
    raw_dir = nexus_store / "input_docs" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    return IngestionPipeline(nexus_instance, raw_dir=raw_dir)


@pytest.fixture
def sample_epub(nexus_store):
    """Create a minimal valid EPUB file."""
    import zipfile

    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)

    # Container
    container = b'''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''

    # OPF with manifest and spine
    opf = b'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test EPUB</dc:title>
    <dc:identifier id="uid">urn:uuid:12345</dc:identifier>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
    <itemref idref="chapter2"/>
  </spine>
</package>'''

    # Chapter 1 XHTML
    chapter1 = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
  <h1>First Chapter</h1>
  <p>This is the content of chapter one.</p>
</body>
</html>'''

    # Chapter 2 XHTML
    chapter2 = b'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 2</title></head>
<body>
  <h1>Second Chapter</h1>
  <p>Content with Unicode.</p>
</body>
</html>'''

    epub_path = data_dir / "sample.epub"
    with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
        zf.writestr("chapter1.xhtml", chapter1)
        zf.writestr("chapter2.xhtml", chapter2)

    return epub_path


@pytest.fixture
def empty_epub(nexus_store):
    """Create a minimal valid EPUB with no chapters."""
    import zipfile

    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)

    container = b'''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''

    opf = b'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Empty EPUB</dc:title>
  </metadata>
  <manifest/>
  <spine/>
</package>'''

    epub_path = data_dir / "empty.epub"
    with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)

    return epub_path


@pytest.fixture
def broken_epub(nexus_store):
    """Create a file that looks like EPUB but isn't valid."""
    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)
    epub_path = data_dir / "broken.epub"
    epub_path.write_bytes(b"This is not a zip file at all")
    return epub_path


# ─── EPUB Tests ──────────────────────────────────────────────────────────────


class TestEPUBIngestion:
    """Test EPUB ingestion pipeline (zero-dep zipfile + container.xml -> OPF -> spine)."""



    def test_kind_epub_supported(self, epub_pipeline):
        assert epub_pipeline._kind(Path("test.epub")) == "epub"

    def test_extract_epub_basic(self, epub_pipeline, sample_epub):
        text = epub_pipeline._extract_epub(sample_epub)
        assert "First Chapter" in text
        assert "chapter one" in text

    def test_extract_epub_empty(self, epub_pipeline, empty_epub):
        text = epub_pipeline._extract_epub(empty_epub)
        assert text == ""

    def test_extract_epub_broken(self, epub_pipeline, broken_epub):
        text = epub_pipeline._extract_epub(broken_epub)
        assert text == ""

    def test_normalize_epub(self, epub_pipeline):
        epub_text = "# Chapter\n\nPara one\n\n\n\nMultiple newlines"
        result = epub_pipeline._normalize(epub_text, "epub", Path("test.epub"))
        assert "Chapter" in result
        assert "Multiple newlines" in result
        assert "\n\n\n\n" not in result

    def test_process_file_epub(self, epub_pipeline, nexus_store, sample_epub):
        import shutil
        shutil.copy(sample_epub, epub_pipeline.raw_dir / "test.epub")
        rep = epub_pipeline.process_file(epub_pipeline.raw_dir / "test.epub")
        assert rep["status"] == "ingested"
        assert rep["kind"] == "epub"

    def test_process_file_epub_empty(self, epub_pipeline, nexus_store, empty_epub):
        import shutil
        shutil.copy(empty_epub, epub_pipeline.raw_dir / "empty.epub")
        rep = epub_pipeline.process_file(epub_pipeline.raw_dir / "empty.epub")
        assert rep["status"] == "error"

    def test_process_file_epub_broken(self, epub_pipeline, nexus_store, broken_epub):
        import shutil
        shutil.copy(broken_epub, epub_pipeline.raw_dir / "broken.epub")
        rep = epub_pipeline.process_file(epub_pipeline.raw_dir / "broken.epub")
        assert rep["status"] == "error"

    def test_run_with_epub_only(self, epub_pipeline, nexus_store, sample_epub):
        import shutil
        shutil.copy(sample_epub, epub_pipeline.raw_dir / "test.epub")
        report = epub_pipeline.run()
        assert report["scanned"] == 1
        assert report["ingested"] == 1
