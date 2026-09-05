"""
Tests for core/nexus_core.py - Nexus memory manager class.
"""
import json
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.nexus_core import Nexus


class TestNexusCreation:
    """Test Nexus class initialization."""

    def test_nexus_creates_directories(self, nexus_store: Path):
        nm = Nexus(base_dir=nexus_store)
        assert (nm.input_raw_dir).is_dir()
        assert (nm.input_processed_dir).is_dir()
        assert (nm.main_lib_dir).is_dir()
        assert (nm.index_dir).is_dir()
        assert (nm.sessions_archived_dir).is_dir()
        assert (nm.sessions_summaries_dir).is_dir()
        assert (nm.sessions_cross_dir).is_dir()

    def test_nexus_custom_base_dir(self, tmp_path: Path):
        custom = tmp_path / "custom_store"
        nm = Nexus(base_dir=custom)
        assert nm.base_dir == custom.resolve()


class TestAddChunk:
    """Test add_chunk method."""

    def test_add_chunk_creates_file(self, nm: Nexus):
        chunk_id = nm.add_chunk("test content", ["#tag"])
        snippet_path = nm.main_lib_dir / "general" / "snippets" / f"{chunk_id}.md"
        assert snippet_path.exists()
        content = snippet_path.read_text(encoding="utf-8")
        assert "test content" in content
        assert "tags:" in content
        assert "created_at:" in content

    def test_add_chunk_updates_index(self, nm: Nexus):
        index_before = nm._read_json(nm.chunks_index_file) or []
        assert len(index_before) == 0
        nm.add_chunk("test", ["#test"])
        index_after = nm._read_json(nm.chunks_index_file) or []
        assert len(index_after) == 1
        entry = index_after[0]
        assert "id" in entry
        assert "tags" in entry
        assert "source_file" in entry
        assert "created_at" in entry

    def test_add_chunk_updates_tags(self, nm: Nexus):
        chunk_id = nm.add_chunk("test", ["#alpha", "#beta"])
        tags_index = nm._read_json(nm.tags_index_file) or {}
        assert "#alpha" in tags_index
        assert "#beta" in tags_index
        assert chunk_id in tags_index["#alpha"]
        assert chunk_id in tags_index["#beta"]

    def test_add_chunk_idempotent(self, nm: Nexus):
        chunk_id1 = nm.add_chunk("same content", ["#tag"])
        chunk_id2 = nm.add_chunk("same content", ["#tag"])
        assert chunk_id1 == chunk_id2
        index = nm._read_json(nm.chunks_index_file) or []
        assert len(index) == 1

    def test_add_chunk_with_project_id(self, nm: Nexus):
        chunk_id = nm.add_chunk("test", ["#tag"], project_id="MyProject")
        snippet_path = nm.main_lib_dir / "projects" / "MyProject" / "snippets" / f"{chunk_id}.md"
        assert snippet_path.exists()
        index = nm._read_json(nm.chunks_index_file) or []
        assert index[0]["project_id"] == "MyProject"

    def test_add_chunk_with_agent_id(self, nm: Nexus):
        chunk_id = nm.add_chunk("test", ["#tag"], agent_id="cline")
        index = nm._read_json(nm.chunks_index_file) or []
        assert index[0]["agent_id"] == "cline"

    def test_add_chunk_with_source(self, nm: Nexus):
        chunk_id = nm.add_chunk("test", ["#tag"], source="https://example.com")
        snippet_path = nm.main_lib_dir / "general" / "snippets" / f"{chunk_id}.md"
        content = snippet_path.read_text(encoding="utf-8")
        assert "source:" in content


class TestSearch:
    """Test search method."""

    def test_search_by_keyword(self, nm: Nexus):
        nm.add_chunk("hello world test", ["#tag"])
        results = nm.search("hello")
        assert len(results) == 1

    def test_search_empty_index(self, nm: Nexus):
        results = nm.search("anything")
        assert results == []

    def test_search_filter_by_tags(self, nm: Nexus):
        nm.add_chunk("test1", ["#a", "#b"])
        nm.add_chunk("test2", ["#c"])
        results = nm.search("test", tags=["#a"])
        assert len(results) == 1
        assert "#a" in results[0]["tags"]

    def test_search_filter_by_project(self, nm: Nexus):
        nm.add_chunk("test", ["#tag"], project_id="proj1")
        nm.add_chunk("test", ["#tag"], project_id="proj2")
        results = nm.search("test", project_id="proj1")
        assert len(results) == 1

    def test_search_filter_by_agent(self, nm: Nexus):
        nm.add_chunk("test", ["#tag"], agent_id="alice")
        nm.add_chunk("test", ["#tag"], agent_id="bob")
        results = nm.search("test", agent_id="alice")
        assert len(results) == 1
        assert results[0]["agent_id"] == "alice"

    def test_search_no_match(self, nm: Nexus):
        nm.add_chunk("hello world", ["#tag"])
        results = nm.search("xyznotfound")
        assert results == []


class TestArchiveSession:
    """Test archive_session method."""

    def test_archive_session_creates_file(self, nm: Nexus):
        session_data = {"chat": ["hello", "world"]}
        result = nm.archive_session("agent1", session_data)
        # result is relative path
        archive_path = nm.base_dir / result
        assert archive_path.exists()
        data = json.loads(archive_path.read_text(encoding="utf-8"))
        assert data["session_data"] == session_data
        assert data["agent_id"] == "agent1"

    def test_archive_session_updates_index(self, nm: Nexus):
        nm.archive_session("agent1", {"data": 1})
        sessions_index = nm._read_json(nm.sessions_index_file) or {}
        assert "agent1" in sessions_index
        assert len(sessions_index["agent1"]["history"]) == 1
        assert sessions_index["agent1"]["history"][0]["status"] == "archived"

    def test_archive_session_unique_timestamp(self, nm: Nexus):
        nm.archive_session("agent1", {"data": 1})
        nm.archive_session("agent1", {"data": 2})
        sessions_index = nm._read_json(nm.sessions_index_file) or {}
        assert len(sessions_index["agent1"]["history"]) == 2

    def test_archive_session_empty_agent_id(self, nm: Nexus):
        # Empty agent_id should still create archive (validation is in SessionHook)
        result = nm.archive_session("", {"data": 1})
        assert result is not None


class TestStoreSessionSummary:
    """Test store_session_summary method."""

    def test_store_session_summary_creates_file(self, nm: Nexus):
        summary = "# Summary\nTest content"
        result = nm.store_session_summary("agent1", summary)
        summary_path = nm.base_dir / result
        assert summary_path.exists()
        content = summary_path.read_text(encoding="utf-8")
        assert "Summary" in content
        assert "Test content" in content

    def test_store_session_summary_updates_index(self, nm: Nexus):
        nm.store_session_summary("agent1", "test summary")
        sessions_index = nm._read_json(nm.sessions_index_file) or {}
        assert sessions_index["agent1"]["last_session_summary"] is not None

    def test_store_session_summary_with_session_id(self, nm: Nexus):
        result = nm.store_session_summary("agent1", "test", session_id="20260905_1200")
        summary_path = nm.base_dir / result
        assert "20260905_1200" in str(result)


class TestGetContextForAgent:
    """Test get_context_for_agent method."""

    def test_get_context_returns_summary(self, nm: Nexus):
        nm.store_session_summary("agent1", "# Test Summary\nDone: task1")
        context = nm.get_context_for_agent("agent1")
        assert "Test Summary" in context

    def test_get_context_empty_for_new_agent(self, nm: Nexus):
        context = nm.get_context_for_agent("new_agent_xyz")
        assert context == ""

    def test_get_context_with_project(self, nm: Nexus):
        nm.add_joint_decision("proj1", {
            "decision": "use blake3",
            "by_agents": ["a", "b"],
            "reason": "fast"
        })
        nm.store_session_summary("agent1", "test")
        context = nm.get_context_for_agent("agent1", project_id="proj1")
        assert "use blake3" in context

    def test_get_context_fallback_collapsed(self, nm: Nexus):
        # Set collapsed_summary in index
        sessions_index = nm._read_json(nm.sessions_index_file) or {}
        sessions_index["agent1"] = {
            "last_session_summary": None,
            "history": [],
            "collapsed_summary": "sessions/session_summaries/agent_agent1/summary_collapsed.md"
        }
        nm._write_json(nm.sessions_index_file, sessions_index)
        # Create collapsed file
        collapsed_path = nm.base_dir / "sessions" / "session_summaries" / "agent_agent1"
        collapsed_path.mkdir(parents=True, exist_ok=True)
        (collapsed_path / "summary_collapsed.md").write_text("Collapsed history", encoding="utf-8")
        context = nm.get_context_for_agent("agent1")
        assert "Collapsed history" in context


class TestAddJointDecision:
    """Test add_joint_decision method."""

    def test_add_joint_decision_creates_file(self, nm: Nexus):
        nm.add_joint_decision("proj1", {
            "decision": "test decision",
            "by_agents": ["a", "b"],
            "reason": "test reason"
        })
        decisions_file = nm.sessions_cross_dir / "collab_proj1" / "joint_decisions.json"
        assert decisions_file.exists()

    def test_add_joint_decision_appends(self, nm: Nexus):
        nm.add_joint_decision("proj1", {
            "decision": "d1",
            "by_agents": ["a"],
            "reason": "r"
        })
        nm.add_joint_decision("proj1", {
            "decision": "d2",
            "by_agents": ["b"],
            "reason": "r"
        })
        decisions_file = nm.sessions_cross_dir / "collab_proj1" / "joint_decisions.json"
        decisions = json.loads(decisions_file.read_text(encoding="utf-8"))
        assert len(decisions) == 2
        assert decisions[0]["decision"] == "d1"
        assert decisions[1]["decision"] == "d2"
