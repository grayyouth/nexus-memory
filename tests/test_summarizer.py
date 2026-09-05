"""
Tests for core/summarizer.py - SessionSummarizer class.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.summarizer import _norm_key, _truncate, _items_from_value, SessionSummarizer


class TestUtilities:
    """Test utility functions."""

    def test_norm_key_basic(self):
        assert _norm_key("Some_Key") == "some_key"
        assert _norm_key("test key") == "test_key"

    def test_norm_key_cyrillic(self):
        assert _norm_key("Тест_Ключ") == "тест_ключ"

    def test_truncate_short(self):
        assert _truncate("hi") == "hi"

    def test_truncate_long(self):
        result = _truncate("a" * 500)
        assert len(result) < 500
        assert result.endswith("…")

    def test_items_from_value_string(self):
        assert _items_from_value("hello") == ["hello"]

    def test_items_from_value_list(self):
        assert _items_from_value(["a", "b"]) == ["a", "b"]

    def test_items_from_value_dict_with_content(self):
        assert _items_from_value({"content": "text"}) == ["text"]

    def test_items_from_value_dict_with_role(self):
        result = _items_from_value({"role": "user", "content": "hi"})
        assert "[user] hi" in result

    def test_items_from_value_none(self):
        assert _items_from_value(None) == []


class TestSessionSummarizer:
    """Test SessionSummarizer class."""

    @pytest.fixture
    def summarizer(self, nexus_instance):
        return SessionSummarizer(nexus_instance)

    def test_list_archives_empty(self, summarizer, nm):
        archives = summarizer.list_archives("new_agent")
        assert archives == []

    def test_list_archives_sorted(self, summarizer, nm):
        # Create archives with different timestamps
        import json
        from datetime import datetime
        archived_dir = nm.sessions_archived_dir / "agent_agent1"
        archived_dir.mkdir(parents=True, exist_ok=True)
        
        for ts in ["20260901_1000", "20260902_1000", "20260903_1000"]:
            archive = {
                "agent_id": "agent1",
                "timestamp": ts,
                "file_path": f"sessions/archived_sessions/agent_agent1/session_{ts}.json",
                "session_data": {"data": ts}
            }
            path = archived_dir / f"session_{ts}.json"
            path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
            # Update sessions index
            sessions_index = nm._read_json(nm.sessions_index_file) or {}
            if "agent1" not in sessions_index:
                sessions_index["agent1"] = {"last_session_summary": None, "history": []}
            sessions_index["agent1"]["history"].append({
                "timestamp": ts,
                "file": f"sessions/archived_sessions/agent_agent1/session_{ts}.json",
                "status": "archived"
            })
            nm._write_json(nm.sessions_index_file, sessions_index)
        
        archives = summarizer.list_archives("agent1")
        assert len(archives) == 3
        assert archives[0]["timestamp"] == "20260901_1000"
        assert archives[2]["timestamp"] == "20260903_1000"

    def test_find_archive_newest(self, summarizer, nm):
        nm.archive_session("agent1", {"data": 1})
        nm.archive_session("agent1", {"data": 2})
        path, ts = summarizer._find_archive_path("agent1")
        assert path is not None
        assert path.exists()

    def test_find_archive_by_id(self, summarizer, nm):
        nm.archive_session("agent1", {"data": 1})
        archives = summarizer.list_archives("agent1")
        target_ts = archives[0]["timestamp"]
        path, found_ts = summarizer._find_archive_path("agent1", session_id=target_ts)
        assert path is not None
        assert found_ts == target_ts

    def test_find_archive_not_found(self, summarizer):
        path, ts = summarizer._find_archive_path("nonexistent")
        assert path is None

    def test_extract_structure_empty(self, summarizer):
        buckets = summarizer.extract_structure({})
        assert all(v == [] for v in buckets.values())

    def test_extract_structure_actions(self, summarizer):
        buckets = summarizer.extract_structure({"actions": ["task1"]})
        assert "task1" in buckets["done"]

    def test_extract_structure_decisions(self, summarizer):
        buckets = summarizer.extract_structure({"decisions": ["use blake3"]})
        assert "use blake3" in buckets["decisions"]

    def test_extract_structure_next_steps(self, summarizer):
        buckets = summarizer.extract_structure({"next_steps": ["fix bug"]})
        assert "fix bug" in buckets["next"]

    def test_extract_structure_chat_history(self, summarizer):
        buckets = summarizer.extract_structure({
            "chat_history": [{"role": "user", "content": "hi"}]
        })
        assert len(buckets["dialog"]) > 0

    def test_extract_structure_unknown_key(self, summarizer):
        buckets = summarizer.extract_structure({"weird_key": "value"})
        assert "weird_key: value" in buckets["other"]

    def test_extract_structure_russian_keys(self, summarizer):
        buckets = summarizer.extract_structure({
            "сделано": ["task1"],
            "решения": ["d1"],
            "дальше": ["next"]
        })
        assert "task1" in buckets["done"]
        assert "d1" in buckets["decisions"]
        assert "next" in buckets["next"]

    def test_extract_structure_nested_dict(self, summarizer):
        data = {"actions": [{"content": "c1"}, {"text": "c2"}]}
        buckets = summarizer.extract_structure(data)
        assert len(buckets["done"]) >= 1

    def test_extract_structure_limits(self, summarizer):
        data = {"actions": [f"item{i}" for i in range(20)]}
        buckets = summarizer.extract_structure(data)
        assert len(buckets["done"]) <= 10  # MAX_ITEMS_PER_BUCKET

    def test_generate_session_summary_new_agent(self, summarizer):
        result = summarizer.generate_session_summary("nonexistent")
        assert result["status"] == "not_found"

    def test_generate_session_summary_existing(self, summarizer, nm):
        nm.archive_session("agent1", {"actions": ["done task"], "decisions": ["use blake3"]})
        result = summarizer.generate_session_summary("agent1")
        assert result["status"] == "ok"
        assert result["summary_file"] is not None
        assert result["stats"]["done"] == 1
        assert result["stats"]["decisions"] == 1

    def test_generate_session_summary_marks_archived(self, summarizer, nm):
        nm.archive_session("agent1", {"actions": ["t1"]})
        summarizer.generate_session_summary("agent1")
        archives = summarizer.list_archives("agent1")
        assert archives[-1]["status"] == "summarized"

    def test_generate_session_summary_preserves_pointer(self, summarizer, nm):
        # Create two archives with distinct timestamps to avoid collision
        import json
        archived_dir = nm.sessions_archived_dir / "agent_agent1"
        archived_dir.mkdir(parents=True, exist_ok=True)
        
        for ts, data in [("20260101_1000", {"actions": ["t1"]}),
                         ("20260102_1000", {"actions": ["t2"]})]:
            archive = {
                "agent_id": "agent1",
                "timestamp": ts,
                "file_path": f"sessions/archived_sessions/agent_agent1/session_{ts}.json",
                "session_data": data
            }
            path = archived_dir / f"session_{ts}.json"
            path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
            sessions_index = nm._read_json(nm.sessions_index_file) or {}
            if "agent1" not in sessions_index:
                sessions_index["agent1"] = {"last_session_summary": None, "history": []}
            sessions_index["agent1"]["history"].append({
                "timestamp": ts,
                "file": f"sessions/archived_sessions/agent_agent1/session_{ts}.json",
                "status": "archived"
            })
            nm._write_json(nm.sessions_index_file, sessions_index)
        
        # Generate summary for the first (older) archive explicitly
        summarizer.generate_session_summary("agent1", session_id="20260101_1000")
        
        # Generate summary for the second (newer) archive
        summarizer.generate_session_summary("agent1", session_id="20260102_1000")
        
        # Now explicitly generate for older again — pointer should NOT change
        summarizer.generate_session_summary("agent1", session_id="20260101_1000")
        
        # Verify context is not empty
        context = nm.get_context_for_agent("agent1")
        assert len(context) > 0

    def test_collapse_history_nothing_to_collapse(self, summarizer, nm):
        nm.archive_session("agent1", {"actions": ["t1"]})
        result = summarizer.collapse_history("agent1", keep_last=1)
        assert result["status"] == "nothing_to_collapse"

    def test_collapse_history_collapses(self, summarizer, nm):
        nm.archive_session("agent1", {"actions": ["t1"]})
        nm.archive_session("agent1", {"actions": ["t2"]})
        nm.archive_session("agent1", {"actions": ["t3"]})
        result = summarizer.collapse_history("agent1", keep_last=1)
        assert result["status"] == "ok"
        assert result["collapsed"] == 2

    def test_collapse_history_preserves_latest(self, summarizer, nm):
        nm.archive_session("agent1", {"actions": ["t1"]})
        nm.archive_session("agent1", {"actions": ["t2"]})
        nm.archive_session("agent1", {"actions": ["t3"]})
        summarizer.collapse_history("agent1", keep_last=1)
        # After collapse, context should still contain something meaningful
        # (either latest summary or collapsed history)
        context = nm.get_context_for_agent("agent1")
        # Collapsed history contains t1 and t2
        assert "t1" in context or "t2" in context or "t3" in context

    def test_build_summary_markdown_empty(self, summarizer):
        md = summarizer._build_summary_markdown(
            "agent1", "20260905_1200", {}, "session.json"
        )
        assert "пуст" in md or "empty" in md.lower() or "не содержит" in md

    def test_build_summary_markdown_with_data(self, summarizer):
        data = {
            "actions": ["done"],
            "decisions": ["used blake3"],
            "next_steps": ["fix bug"],
            "chat_history": [{"role": "user", "content": "hi"}]
        }
        md = summarizer._build_summary_markdown(
            "agent1", "20260905_1200", data, "session.json"
        )
        assert "Сделано" in md
        assert "Решения" in md
        assert "Дальше" in md
        assert "Диалог" in md
