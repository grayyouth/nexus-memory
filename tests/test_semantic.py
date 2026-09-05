"""
Tests for core/semantic.py - HashingEmbedder and SemanticSearch.
"""
import math
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.semantic import HashingEmbedder, SemanticSearch, _cosine, _read_chunk_body


class TestHashingEmbedder:
    """Test HashingEmbedder class."""

    def test_embedder_deterministic(self):
        emb = HashingEmbedder(512)
        v1 = emb.embed("hello world")
        v2 = emb.embed("hello world")
        assert v1 == v2

    def test_embedder_vector_size(self):
        emb = HashingEmbedder(512)
        v = emb.embed("test")
        assert len(v) == 512

    def test_embedder_l2_normalized(self):
        emb = HashingEmbedder(512)
        v = emb.embed("test text here")
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 0.01

    def test_embedder_different_text_different_vector(self):
        emb = HashingEmbedder(512)
        v1 = emb.embed("hello world")
        v2 = emb.embed("goodbye universe")
        assert v1 != v2

    def test_embedder_is_zero_empty(self):
        emb = HashingEmbedder(512)
        v = emb.embed("")
        assert emb.is_zero(v) is True

    def test_embedder_russian_english(self):
        emb = HashingEmbedder(512)
        v_ru = emb.embed("привет мир")
        v_en = emb.embed("hello world")
        assert len(v_ru) == 512
        assert len(v_en) == 512
        # Deterministic
        assert emb.embed("привет мир") == emb.embed("привет мир")


class TestSemanticSearch:
    """Test SemanticSearch class."""

    @pytest.fixture
    def sem(self, nexus_instance):
        return SemanticSearch(nexus_instance)

    def test_search_empty_library(self, sem):
        results = sem.search("anything")
        assert results == []

    def test_search_returns_results(self, sem, nm):
        nm.add_chunk("hello world semantic search", ["#tag"])
        results = sem.search("hello world")
        assert len(results) >= 1

    def test_search_top_k(self, sem, nm):
        for i in range(10):
            nm.add_chunk(f"test item number {i}", ["#tag"])
        results = sem.search("test item", top_k=3)
        assert len(results) <= 3

    def test_search_filters_by_tags(self, sem, nm):
        nm.add_chunk("tag a content", ["#a"])
        nm.add_chunk("tag b content", ["#b"])
        results = sem.search("content", tags=["#a"])
        assert len(results) >= 1
        assert "#a" in results[0]["tags"]

    def test_search_filters_by_project(self, sem, nm):
        nm.add_chunk("project content", ["#tag"], project_id="proj1")
        nm.add_chunk("project content", ["#tag"], project_id="proj2")
        results = sem.search("project", project_id="proj1")
        assert len(results) == 1

    def test_search_cache(self, sem, nm):
        nm.add_chunk("cached content", ["#tag"])
        # First search
        results1 = sem.search("cached", top_k=5)
        # Second search - should use cache
        results2 = sem.search("cached", top_k=5)
        assert results1 == results2

    def test_search_hybrid_keyword_bonus(self, sem, nm):
        nm.add_chunk("fast hash function testing", ["#hash"])
        results = sem.search("hash function", hybrid=True)
        assert len(results) >= 1
        # Check that keyword hit is marked
        has_keyword = any(r.get("keyword_hit") for r in results)
        assert has_keyword

    def test_build_prompt_block_empty(self, sem):
        result = sem.build_prompt_block("nothing here")
        assert result["chunks"] == []
        assert "не найдено" in result["text"].lower() or "not found" in result["text"].lower() or "no" in result["text"].lower()

    def test_build_prompt_block_fits_budget(self, sem, nm):
        nm.add_chunk("important content for testing", ["#tag"])
        result = sem.build_prompt_block("important", max_chars=500)
        assert result["chars"] <= 500

    def test_build_prompt_block_max_chunks(self, sem, nm):
        for i in range(10):
            nm.add_chunk(f"chunk {i} content", ["#tag"])
        result = sem.build_prompt_block("chunk", max_chunks=3)
        assert len(result["chunks"]) <= 3

    def test_related_empty(self, sem):
        results = sem.related("nonexistent_id")
        assert results == []

    def test_related_returns_results(self, sem, nm):
        nm.add_chunk("similar content about testing", ["#tag"])
        nm.add_chunk("another similar test content", ["#tag"])
        # Need to add a chunk first to get its ID
        chunk_id = nm.add_chunk("base test content", ["#tag"])
        results = sem.related(chunk_id, top_k=5)
        assert len(results) >= 1
