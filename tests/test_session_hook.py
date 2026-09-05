"""
Tests for core/session_hook.py - SessionHook class.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.session_hook import SessionHook


class TestSessionHook:
    """Test SessionHook class."""

    @pytest.fixture
    def hook(self, nexus_instance):
        return SessionHook(nexus_instance)

    def test_end_session_empty_agent_id(self, hook):
        result = hook.end_session("", {"actions": []})
        assert result["status"] == "invalid_input"

    def test_end_session_empty_data(self, hook, nm):
        result = hook.end_session("agent1", {})
        assert result["status"] == "invalid_input"

    def test_end_session_creates_archive(self, hook, nm):
        result = hook.end_session("agent1", {"actions": ["task1"]})
        assert result["status"] == "ok"
        assert result["archive_file"] is not None
        archive_path = nm.base_dir / result["archive_file"]
        assert archive_path.exists()

    def test_end_session_creates_summary(self, hook, nm):
        result = hook.end_session("agent1", {"actions": ["task1"]})
        assert result["summary_file"] is not None
        assert result["summary_status"] == "ok"
        summary_path = nm.base_dir / result["summary_file"]
        assert summary_path.exists()

    def test_end_session_returns_stats(self, hook, nm):
        result = hook.end_session("agent1", {
            "actions": ["t1"],
            "decisions": ["d1"],
            "next_steps": ["n1"]
        })
        assert "stats" in result
        assert result["stats"]["done"] == 1
        assert result["stats"]["decisions"] == 1
        assert result["stats"]["next"] == 1

    def test_end_session_collapse_after_true(self, hook, nm):
        # Create old archives
        nm.archive_session("agent1", {"actions": ["old1"]})
        nm.archive_session("agent1", {"actions": ["old2"]})
        result = hook.end_session("agent1", {"actions": ["new"]}, collapse_after=True)
        assert result["status"] == "ok"
        assert result["collapsed"] is not None

    def test_end_session_collapse_after_false(self, hook, nm):
        result = hook.end_session("agent1", {"actions": ["t1"]}, collapse_after=False)
        assert result["status"] == "ok"
        assert result["collapsed"] is None

    def test_end_session_message_contains_paths(self, hook, nm):
        result = hook.end_session("agent1", {"actions": ["t1"]})
        assert "archive" in result["message"].lower() or "архив" in result["message"].lower()
        assert "Сессия сохранена" in result["message"]
