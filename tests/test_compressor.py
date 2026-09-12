"""
Tests for session compression — SessionSummarizer.compress_session and the
standalone compress_session() helper (Nexus roadmap chunk c7464edb).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.summarizer import SessionSummarizer, compress_session


class TestStandaloneCompress:
    """compress_session(raw_log=...) works without a store (NeoKron etc.)."""

    SAMPLE = {
        "actions": ["implemented blob storage", "fixed login bug"],
        "decisions": ["use blake3 for hashing"],
        "next_steps": ["add compression notes", "run full training"],
        "chat_history": [
            {"role": "user", "content": "please fix the login"},
            {"role": "assistant", "content": "done, bug was in session check"},
        ],
    }

    def test_basic(self):
        res = compress_session(self.SAMPLE)
        assert res["status"] == "ok"
        assert res["level"] == 1
        assert res["source"] == "raw_log"
        assert "implemented blob storage" in res["compact"]["done"]
        assert "use blake3" in res["compact"]["decisions"][0]
        assert res["stats"]["in_chars"] > res["stats"]["out_chars"]

    def test_markdown_block(self):
        res = compress_session(self.SAMPLE)
        md = res["markdown"]
        assert "Сжатая сессия" in md
        assert "Сделано" in md
        assert "Решения" in md
        assert "Дальше" in md
        assert "- use blake3" in md

    def test_russian_keys(self):
        res = compress_session(
            {"сделано": ["a"], "решения": ["b"], "дальше": ["c"]}
        )
        assert res["compact"]["done"] == ["a"]
        assert res["compact"]["decisions"] == ["b"]
        assert res["compact"]["next"] == ["c"]

    def test_invalid_input_no_source(self):
        res = compress_session()
        assert res["status"] == "invalid_input"

    def test_not_found_unknown_agent(self, nexus_instance):
        res = SessionSummarizer(nexus_instance).compress_session(agent_id="ghost")
        assert res["status"] == "not_found"

    def test_empty_log(self):
        res = compress_session({})
        assert res["status"] == "ok"
        assert res["compact"]["done"] == []

    def test_level2_no_llm_falls_back(self):
        res = compress_session(self.SAMPLE, level=2)
        assert res["status"] == "ok"
        assert res["level"] == 1
        assert res["llm"]["status"] == "skipped"

    def test_level2_with_llm_call(self):
        calls = []

        def fake_llm(prompt):
            calls.append(prompt)
            return "СДЕЛАНО: всё; РЕШЕНИЯ: ok; ДАЛЬШЕ: отдыхать"

        res = compress_session(self.SAMPLE, level=2, llm_call=fake_llm)
        assert res["level"] == 2
        assert res["llm"]["status"] == "ok"
        assert calls  # the prompt was actually built and passed
        assert "всё" in res["llm"]["summary"]

    def test_level2_with_failing_llm(self):
        def bad_llm(_prompt):
            raise RuntimeError("llm offline")

        res = compress_session(self.SAMPLE, level=2, llm_call=bad_llm)
        assert res["status"] == "ok"
        assert res["level"] == 1  # graceful fallback
        assert res["llm"]["status"] == "error"

    def test_max_chars_budget(self):
        res = compress_session(self.SAMPLE, max_chars=200)
        assert len(res["markdown"]) <= 202  # hard-tail ellipsis allowance


class TestArchiveBased:
    """compress_session(agent_id=...) reads a recent archived session."""

    @pytest.fixture
    def summarizer(self, nexus_instance):
        return SessionSummarizer(nexus_instance)

    def test_from_archive(self, summarizer, nm):
        nm.archive_session(
            "agent_compact",
            {"actions": ["храним память"], "next_steps": ["продолжить"]},
        )
        res = summarizer.compress_session(agent_id="agent_compact")
        assert res["status"] == "ok"
        assert res["source"].startswith("archive:agent_compact:")
        assert "храним память" in res["compact"]["done"]

    def test_compress_marks_nothing_in_index(self, summarizer, nm):
        """Compression must NOT touch the sessions index (read-only op)."""
        nm.archive_session("agent_ro", {"actions": ["x"]})
        before = nm._read_json(nm.sessions_index_file)
        summarizer.compress_session(agent_id="agent_ro")
        after = nm._read_json(nm.sessions_index_file)
        assert before == after

    def test_compress_from_archived_chain(self, summarizer, nm):
        from core.nexus_core import Nexus

        summ = SessionSummarizer(nm)
        nm.archive_session("chain", {"done": ["step1"], "decisions": ["alpha"]})
        res = summ.compress_session(agent_id="chain")
        assert res["status"] == "ok"
        assert "step1" in res["compact"]["done"]
        assert "alpha" in res["compact"]["decisions"][0]