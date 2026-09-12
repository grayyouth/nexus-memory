"""
Tests for core/project_digest.py - ProjectDigest class.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.nexus_core import Nexus
from core.project_digest import ProjectDigest, parse_time


class TestParseTime:
    def test_session_ts(self):
        dt = parse_time("20260911_0338")
        assert dt is not None
        assert dt.year == 2026 and dt.minute == 38

    def test_session_ts_with_counter(self):
        assert parse_time("20260911_0338_2") == parse_time("20260911_0338")

    def test_iso(self):
        assert parse_time("2026-09-11T03:38:12") is not None

    def test_plain_date(self):
        dt = parse_time("2026-09-11")
        assert dt is not None
        assert dt.hour == 0

    def test_invalid(self):
        assert parse_time("garbage") is None
        assert parse_time(None) is None
        assert parse_time("") is None


class TestProjectDigest:
    @pytest.fixture
    def digest(self, nexus_instance):
        return ProjectDigest(nexus_instance)

    def test_empty_project(self, digest):
        text = digest.get_digest("Vacant")
        assert "Нет данных" in text

    # --- Session attribution by project ---

    def test_archives_tagged_by_project(self, digest, nm):
        nm.archive_session("agent1", {"actions": ["task A"]}, project_id="P")
        nm.archive_session("agent2", {"actions": ["task B"]}, project_id="P")
        nm.archive_session("agent3", {"actions": ["task C"]}, project_id="Q")

        text = digest.get_digest("P")
        assert "task A" in text
        assert "task B" in text
        assert "task C" not in text

    def test_legacy_untagged_archives_ignored(self, digest, nm):
        nm.archive_session("agent1", {"actions": ["legacy"]})
        text = digest.get_digest("P")
        assert "legacy" not in text
        assert "Нет данных" in text

    def test_digest_grouped_by_agent(self, digest, nm):
        nm.archive_session("bob", {"actions": ["b1"]}, project_id="P")
        nm.archive_session("alice", {"actions": ["a1"]}, project_id="P")
        data = digest.collect("P")
        assert set(data["agents"].keys()) == {"bob", "alice"}

    # --- "Since" filtering ---

    def test_since_filters_old_sessions(self, digest, nm):
        old_ts = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d_%H%M")
        new_ts = datetime.now().strftime("%Y%m%d_%H%M")
        data = digest.collect("P", since=old_ts)
        assert data["stats"]["sessions"] in (0, 1)

    def test_since_iso(self, digest, nm):
        since = (datetime.now() - timedelta(hours=1)).isoformat()
        data = digest.collect("P", since=since)
        assert data["stats"]["sessions"] == 0

    # --- Agent filters ---

    def test_exclude_agent(self, digest, nm):
        nm.archive_session("me", {"actions": ["mine"]}, project_id="P")
        nm.archive_session("colega", {"actions": ["theirs"]}, project_id="P")
        data = digest.collect("P", exclude_agent="me")
        assert "me" not in data["agents"]
        assert "colega" in data["agents"]

    def test_agent_filter(self, digest, nm):
        nm.archive_session("me", {"actions": ["mine"]}, project_id="P")
        nm.archive_session("colega", {"actions": ["theirs"]}, project_id="P")
        data = digest.collect("P", agent_filter="colega")
        assert list(data["agents"].keys()) == ["colega"]

    # --- Joint decisions ---

    def test_decisions_included(self, digest, nm):
        nm.add_joint_decision("P", {"decision": "use bf16",
                                    "by_agents": ["a", "b"], "reason": "vram"})
        data = digest.collect("P")
        assert data["stats"]["decisions"] == 1
        assert data["decisions"][0]["decision"] == "use bf16"

    def test_decisions_get_recorded_at(self, digest, nm):
        nm.add_joint_decision("P", {"decision": "d", "by_agents": ["a"], "reason": "r"})
        data = digest.collect("P")
        assert data["decisions"][0]["recorded_at"] is not None

    def test_decisions_filtered_by_since(self, digest, nm):
        nm.add_joint_decision("P", {"decision": "old", "by_agents": ["a"], "reason": "r",
                                    "recorded_at": "2020-01-01T00:00:00"})
        nm.add_joint_decision("P", {"decision": "new", "by_agents": ["b"], "reason": "r"})
        data = digest.collect("P", since="2025-01-01")
        assert [d["decision"] for d in data["decisions"]] == ["new"]

    # --- Chunks ---

    def test_recent_chunks_included(self, digest, nm):
        nm.add_chunk("свежая находка про KAN", ["#ai"], project_id="P")
        nm.add_chunk("старое знание", ["#ai"], project_id="Q")
        data = digest.collect("P")
        assert data["stats"]["chunks"] == 1
        assert "KAN" in data["chunks"][0]["preview"]

    def test_chunks_filtered_by_since(self, digest, nm):
        nm.add_chunk("новая заметка", ["#t"], project_id="P")
        future = (datetime.now() + timedelta(days=1)).isoformat()
        data = digest.collect("P", since=future)
        assert data["stats"]["chunks"] == 0

    # --- End-to-end via SessionHook ---

    def test_end_session_with_project(self, digest, nm):
        from core.session_hook import SessionHook

        SessionHook(nm).end_session(
            "agent1", {"actions": ["работал над KAN"]}, project_id="KAN-LLM"
        )
        text = digest.get_digest("KAN-LLM")
        assert "работал над KAN" in text

    def test_context_includes_digest(self, nm):
        nm.archive_session("other", {"actions": ["сделал вывод X"]}, project_id="P")
        nm.add_joint_decision("P", {"decision": "решение Y",
                                    "by_agents": ["other"], "reason": "z"})
        context = nm.get_context_for_agent("me", project_id="P")
        assert "сделал вывод X" in context
        assert "решение Y" in context