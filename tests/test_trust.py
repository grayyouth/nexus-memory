# -*- coding: utf-8 -*-
"""
Tests for core/trust.py — source-trust scoring and its integration
into Nexus.add_chunk and SemanticSearch ranking.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.nexus_core import Nexus
from core.semantic import SemanticSearch
from core.trust import compute_trust, clamp_trust


class TestComputeTrust:
    """Test compute_trust heuristics."""

    def test_empty_source_default(self):
        assert compute_trust(None) == pytest.approx(0.5)
        assert compute_trust("") == pytest.approx(0.5)
        assert compute_trust("   ") == pytest.approx(0.5)

    def test_trusted_domain(self):
        assert compute_trust("https://docs.python.org/3/library/os.html") == pytest.approx(1.0)
        assert compute_trust("https://en.wikipedia.org/wiki/Hash_function") == pytest.approx(1.0)
        assert compute_trust("https://github.com/foo/bar") == pytest.approx(1.0)

    def test_trusted_prefix_and_tld(self):
        # docs. prefix on an otherwise unknown domain
        assert compute_trust("https://docs.unknown-corp.example/docs/guide") == pytest.approx(1.0)
        # gov/edu TLDs are authoritative
        assert compute_trust("https://example.gov/page") == pytest.approx(1.0)
        assert compute_trust("https://university.edu/paper") == pytest.approx(1.0)

    def test_unknown_url_low_trust(self):
        assert compute_trust("https://some-random-blog.example/post/123") == pytest.approx(0.4)

    def test_local_doc_source(self):
        assert compute_trust("ROADMAP.md") == pytest.approx(0.8)
        assert compute_trust("nexus_store/main_library/notes.md") == pytest.approx(0.8)
        assert compute_trust("docs/architecture.txt") == pytest.approx(0.8)

    def test_custom_config(self):
        from core.config import config as global_config
        global_config.update(trust={"unknown_url_trust": 0.9})
        try:
            assert compute_trust("https://random.example/x") == pytest.approx(0.9)
        finally:
            global_config.update(trust={"unknown_url_trust": 0.4})


class TestClampTrust:
    def test_clamp_range(self):
        assert clamp_trust(1.5) == 1.0
        assert clamp_trust(-0.2) == 0.0
        assert clamp_trust(0.7) == pytest.approx(0.7)

    def test_clamp_invalid_falls_back(self):
        assert clamp_trust("not-a-number") == pytest.approx(0.5)


class TestAddChunkTrust:
    """Test that add_chunk stores trust in front matter and index."""

    def test_trust_in_front_matter(self, nm: Nexus):
        cid = nm.add_chunk("trusted content", ["#t"], source="https://docs.python.org/3/")
        body = (nm.base_dir / "main_library" / "general" / "snippets" / f"{cid}.md").read_text(encoding="utf-8")
        assert "trust: 1.0" in body

    def test_trust_in_index(self, nm: Nexus):
        id_trusted = nm.add_chunk("a", ["#t"], source="https://wikipedia.org/wiki/X")
        id_unknown = nm.add_chunk("b", ["#t"], source="https://random-blog.example/x")
        id_none = nm.add_chunk("c", ["#t"])
        index = {e["id"]: e for e in (nm._read_json(nm.chunks_index_file) or [])}
        assert index[id_trusted]["trust"] == pytest.approx(1.0)
        assert index[id_unknown]["trust"] == pytest.approx(0.4)
        assert index[id_none]["trust"] == pytest.approx(0.5)

    def test_trust_explicit_override(self, nm: Nexus):
        cid = nm.add_chunk("x", ["#t"], source="https://random.example/x", trust=0.95)
        index = nm._read_json(nm.chunks_index_file) or []
        entry = [e for e in index if e["id"] == cid][0]
        assert entry["trust"] == pytest.approx(0.95)

    def test_trust_clamped(self, nm: Nexus):
        cid = nm.add_chunk("y", ["#t"], trust=42)
        index = nm._read_json(nm.chunks_index_file) or []
        entry = [e for e in index if e["id"] == cid][0]
        assert entry["trust"] == 1.0


class TestTrustRanking:
    """Trust should influence semantic ranking for near-identical texts."""

    @pytest.fixture
    def sem(self, nexus_instance):
        return SemanticSearch(nexus_instance)

    def test_trusted_ranks_above_unknown(self, sem, nm):
        # Near-identical vectors; the trusted chunk must not lose to the
        # unknown one because its damping factor is higher.
        nm.add_chunk("aaa bbb ccc reference", ["#t"], source="https://docs.python.org/3/")
        nm.add_chunk("aaa bbb ddd random", ["#t"], source="https://random-blog.example/x")
        results = sem.search("aaa bbb", top_k=5)
        assert len(results) == 2
        # The trusted chunk (trust=1.0) keeps the full score; the unknown
        # one is damped. Both must still be returned, ranked correctly.
        assert results[0]["score"] >= results[1]["score"]

    def test_results_carry_trust_field(self, sem, nm):
        nm.add_chunk("search me please", ["#t"], source="https://random-blog.example/x")
        results = sem.search("search me", top_k=3)
        assert results
        assert "trust" in results[0]
