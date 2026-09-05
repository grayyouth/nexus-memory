"""
Pytest fixtures for Nexus tests.

All tests use tmp_path to ensure isolation.
"""
import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))


@pytest.fixture
def nexus_store(tmp_path: Path) -> Path:
    """Create an empty Nexus store in a temporary directory."""
    return tmp_path


@pytest.fixture
def nexus_instance(nexus_store: Path):
    """Create a Nexus instance with a temporary base directory."""
    from core.nexus_core import Nexus
    return Nexus(base_dir=nexus_store)


@pytest.fixture
def nm(nexus_instance):
    """Alias for nexus_instance — used by tests."""
    return nexus_instance


@pytest.fixture
def sample_markdown(nexus_store: Path) -> Path:
    """Create a sample markdown file with front-matter."""
    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)
    content = """---
tags: [test, sample]
project: TestProject
source: https://example.com
---

# Sample Markdown

This is a test markdown file for Nexus tests.

## Section One

Content here.

## Section Two

More content.
"""
    path = data_dir / "sample.md"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sample_json(nexus_store: Path) -> Path:
    """Create a sample JSON file."""
    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)
    content = '{"key": "value", "number": 42, "list": [1, 2, 3]}'
    path = data_dir / "sample.json"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sample_csv(nexus_store: Path) -> Path:
    """Create a sample CSV file with 3 columns and 5 rows."""
    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)
    content = """name,age,city
Alice,30,New York
Bob,25,London
Charlie,35,Paris
Diana,28,Tokyo
Eve,32,Berlin
"""
    path = data_dir / "sample.csv"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def sample_html(nexus_store: Path) -> Path:
    """Create a sample HTML file with headings and paragraphs."""
    data_dir = nexus_store / "_data"
    data_dir.mkdir(exist_ok=True)
    content = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
<h1>Main Title</h1>
<p>First paragraph with content.</p>
<h2>Subtitle One</h2>
<p>Content under subtitle one.</p>
<h3>Deep Heading</h3>
<p>More content.</p>
<script>alert('ignored');</script>
<style>.hidden { display: none; }</style>
</body>
</html>
"""
    path = data_dir / "sample.html"
    path.write_text(content, encoding="utf-8")
    return path
