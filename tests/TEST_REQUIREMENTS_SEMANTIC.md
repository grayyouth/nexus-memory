# Test Requirements for Semantic Search and Ingestion

## File: tests/test_semantic.py

### Fixtures
```python
import pytest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

class TestSemantic:
    @pytest.fixture
    def nm(self, nexus_store):
        from core.nexus_core import Nexus
        return Nexus(base_dir=nexus_store)
    
    @pytest.fixture
    def sem(self, nm):
        from core.semantic import SemanticSearch
        return SemanticSearch(nm)
```

### HashingEmbedder Tests

#### test_embedder_deterministic
- from core.semantic import HashingEmbedder
- emb = HashingEmbedder(512)
- v1 = emb.embed("hello world")
- v2 = emb.embed("hello world")
- assert v1 == v2

#### test_embedder_vector_size
- v = emb.embed("test")
- assert len(v) == 512

#### test_embedder_l2_normalized
- v = emb.embed("test text")
- import math
- norm = math.sqrt(sum(x*x for x in v))
- assert abs(norm - 1.0) < 0.01

#### test_embedder_different_text_different_vector
- v1 = emb.embed("hello world")
- v2 = emb.embed("goodbye universe")
- assert v1 != v2

#### test_embedder_is_zero_empty
- v = emb.embed("")
- assert emb.is_zero(v) == True

#### test_embedder_russian_english
- v_ru = emb.embed("привет мир")
- v_en = emb.embed("hello world")
- assert len(v_ru) == 512
- assert len(v_en) == 512
- assert emb.embed("привет мир") == emb.embed("привет мир")  # deterministic

## File: tests/test_ingestion.py

### Fixtures
```python
class TestIngestion:
    @pytest.fixture
    def nm(self, nexus_store):
        from core.nexus_core import Nexus
        return Nexus(base_dir=nexus_store)
    
    @pytest.fixture
    def pipeline(self, nm, nexus_store):
        from core.ingestion import IngestionPipeline
        raw_dir = nexus_store / "input_docs" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        return IngestionPipeline(nm, raw_dir=raw_dir)
```

### HTMLTextExtractor Tests

#### test_html_to_text_headings
- from core.ingestion import _HTMLTextExtractor
- parser = _HTMLTextExtractor()
- parser.feed("<h1>Title</h1><h2>Subtitle</h2>")
- text = parser.text()
- assert "# Title" in text
- assert "## Subtitle" in text

#### test_html_to_text_paragraphs
- parser = _HTMLTextExtractor()
- parser.feed("<p>Para one</p><p>Para two</p>")
- text = parser.text()
- assert "Para one" in text
- assert "Para two" in text

#### test_html_to_text_ignores_script
- parser = _HTMLTextExtractor()
- parser.feed("<script>alert(1)</script><p>visible</p>")
- text = parser.text()
- assert "alert" not in text
- assert "visible" in text

#### test_html_to_text_collapse_newlines
- parser = _HTMLTextExtractor()
- parser.feed("<p>a</p><p>b</p><p>c</p>")
- text = parser.text()
- assert "\n\n\n" not in text  # no triple newlines

### IngestionPipeline Tests

#### test_kind_supported
- from core.ingestion import SUPPORTED_EXTS
- assert pipeline._kind(Path("test.md")) == "markdown"
- assert pipeline._kind(Path("test.txt")) == "text"
- assert pipeline._kind(Path("test.json")) == "json"
- assert pipeline._kind(Path("test.csv")) == "csv"
- assert pipeline._kind(Path("test.html")) == "html"

#### test_kind_unsupported
- assert pipeline._kind(Path("test.pdf")) is None
- assert pipeline._kind(Path("test.png")) is None

#### test_normalize_markdown
- result = pipeline._normalize("# Header\nBody", "markdown", Path("test.md"))
- assert "# Header" in result

#### test_normalize_json
- result = pipeline._normalize('{"key": "val"}', "json", Path("test.json"))
- assert '"key"' in result
- assert '"val"' in result

#### test_normalize_csv
- csv_data = "a,b,c\n1,2,3\n4,5,6"
- result = pipeline._normalize(csv_data, "csv", Path("test.csv"))
- assert "| a |" in result
- assert "|---|" in result
- assert "| 1 |" in result

#### test_normalize_html
- html = "<h1>Title</h1><p>Content</p>"
- result = pipeline._normalize(html, "html", Path("test.html"))
- assert "# Title" in result
- assert "Content" in result

#### test_parse_front_matter_present
- md = "---\ntags: [a, b]\nproject: proj1\nsource: url\n---\nbody text"
- meta, body = IngestionPipeline._parse_front_matter(md)
- assert meta["tags"] == ["a", "b"]
- assert meta["project"] == "proj1"
- assert body == "body text"

#### test_parse_front_matter_absent
- meta, body = IngestionPipeline._parse_front_matter("no frontmatter")
- assert meta == {}

#### test_parse_front_matter_tags_list
- md = "---\ntags: [x, y, z]\n---\nbody"
- meta, _ = IngestionPipeline._parse_front_matter(md)
- assert meta["tags"] == ["x", "y", "z"]

#### test_chunk_markdown_by_headings
- md = "# H1\nsection1\n## H2\nsection2"
- chunks = pipeline._chunk_markdown(md)
- assert len(chunks) >= 1

#### test_chunk_markdown_size_limit
- md = "# Big\n" + "x" * 2000
- chunks = pipeline._chunk_markdown(md)
- for c in chunks:
    assert len(c) <= 1200 + 50  # some tolerance

#### test_chunk_plain_by_paragraphs
- text = "para1\n\npara2\n\npara3"
- chunks = pipeline._chunk_plain(text)
- assert len(chunks) >= 1

#### test_process_file_markdown
- raw_dir = pipeline.raw_dir
- (raw_dir / "test.md").write_text("# Test\nContent here", encoding="utf-8")
- rep = pipeline.process_file(raw_dir / "test.md")
- assert rep["status"] == "ingested"
- assert rep["chunks"] >= 1

#### test_process_file_json
- (raw_dir / "test.json").write_text('{"key": "val"}', encoding="utf-8")
- rep = pipeline.process_file(raw_dir / "test.json")
- assert rep["status"] == "ingested"

#### test_process_file_csv
- (raw_dir / "test.csv").write_text("a,b\n1,2\n3,4", encoding="utf-8")
- rep = pipeline.process_file(raw_dir / "test.csv")
- assert rep["status"] == "ingested"

#### test_process_file_unsupported
- (raw_dir / "test.pdf").write_bytes(b"fake pdf")
- rep = pipeline.process_file(raw_dir / "test.pdf")
- assert rep["status"] == "skipped"

#### test_process_file_empty
- (raw_dir / "empty.txt").write_text("", encoding="utf-8")
- rep = pipeline.process_file(raw_dir / "empty.txt")
- assert rep["status"] == "error"

#### test_run_processes_multiple
- (raw_dir / "a.md").write_text("# A", encoding="utf-8")
- (raw_dir / "b.json").write_text('{"x":1}', encoding="utf-8")
- (raw_dir / "c.pdf").write_bytes(b"pdf")
- report = pipeline.run()
- assert report["scanned"] == 3
- assert report["ingested"] == 2
- assert report["skipped"] == 1

#### test_run_report_summary
- report = pipeline.run()
- assert "started_at" in report
- assert "finished_at" in report
- assert "files" in report

#### test_archive_file_moved
- (raw_dir / "move.md").write_text("# Move", encoding="utf-8")
- pipeline.process_file(raw_dir / "move.md")
- processed_dir = pipeline.processed_dir
- assert any("move.md" in str(f) for f in processed_dir.rglob("*"))
