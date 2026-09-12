"""
Tests for core/orchestrator.py — multi-agent task orchestration.
"""
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.orchestrator import Orchestrator, get_orchestrator_status, list_orchestrator_tasks
from core.nexus_core import Nexus


class TestOrchestratorInit:
    """Test Orchestrator initialization."""

    def test_init(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        assert orch._tasks == {}
        assert orch._agents == {}

    def test_load_empty(self, tmp_path):
        """Load from non-existent file should be no-op."""
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        orch.load_tasks()
        assert orch._tasks == {}
        assert orch._agents == {}


class TestStartTask:
    """Test start_task method."""

    def test_start_task(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        task = orch.start_task(
            task_id="task_001",
            agent_id="cline",
            project_id="MyProject",
            description="Fix login bug",
        )
        
        assert task["task_id"] == "task_001"
        assert task["agent_id"] == "cline"
        assert task["project_id"] == "MyProject"
        assert task["status"] == "running"
        assert task["description"] == "Fix login bug"
        assert task["started_at"] is not None
        assert task["ended_at"] is None
        assert task["context_loaded"] is False  # new agent, no prior context

    def test_start_task_with_model(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        task = orch.start_task(
            task_id="task_002",
            agent_id="Gea",
            project_id="MyProject",
            model="giga-chat",
            variant="high",
        )
        
        assert task["model"] == "giga-chat"
        assert task["variant"] == "high"

    def test_agent_tracking(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "cline", "ProjectB")
        
        agents = orch.get_agents()
        assert "cline" in agents
        assert agents["cline"]["tasks"] == ["task_001", "task_002"]
        assert agents["cline"]["last_task"] == "task_002"
        assert agents["cline"]["last_project"] == "ProjectB"

    def test_tasks_persisted(self, tmp_path):
        """Tasks are saved to JSON file."""
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        
        # File should exist
        tasks_file = store / "sessions" / "orchestrator_tasks.json"
        assert tasks_file.exists()

    def test_reload_tasks(self, tmp_path):
        """Tasks can be reloaded from file."""
        store = tmp_path / "nexus_store"
        orch1 = Orchestrator(nexus=Nexus(base_dir=store))
        orch1.start_task("task_001", "cline", "ProjectA")
        
        # New instance should reload
        orch2 = Orchestrator(nexus=Nexus(base_dir=store))
        orch2.load_tasks()
        
        assert "task_001" in orch2._tasks
        assert orch2._tasks["task_001"]["agent_id"] == "cline"


class TestEndTask:
    """Test end_task method."""

    def test_end_task(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        result = orch.end_task("task_001", "cline")
        
        assert result["status"] == "completed"
        assert result["ended_at"] is not None

    def test_end_nonexistent_task(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        result = orch.end_task("nonexistent", "cline")
        assert result["status"] == "error"

    def test_end_task_with_session_data(self, tmp_path):
        """End task with session data saves to Nexus."""
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        result = orch.end_task(
            "task_001",
            "cline",
            session_data={"actions": ["Fixed bug"], "decisions": ["Use blake3"]},
        )
        
        assert result["status"] == "completed"
        assert result.get("summary") is not None
        assert result["summary"]["archive"] is not None


class TestCancelTask:
    """Test cancel_task method."""

    def test_cancel_task(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        result = orch.cancel_task("task_001")
        
        assert result["status"] == "cancelled"
        assert result["ended_at"] is not None

    def test_cancel_nonexistent(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        result = orch.cancel_task("nonexistent")
        assert result["status"] == "error"


class TestQueryMethods:
    """Test query methods."""

    def test_get_task_status(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        status = orch.get_task_status("task_001")
        
        assert status is not None
        assert status["task_id"] == "task_001"

    def test_get_agent_tasks(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "cline", "ProjectB")
        orch.start_task("task_003", "Gea", "ProjectA")
        
        cline_tasks = orch.get_agent_tasks("cline")
        assert len(cline_tasks) == 2
        assert all(t["agent_id"] == "cline" for t in cline_tasks)

    def test_get_project_tasks(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "Gea", "ProjectA")
        orch.start_task("task_003", "cline", "ProjectB")
        
        proj_tasks = orch.get_project_tasks("ProjectA")
        assert len(proj_tasks) == 2
        assert all(t["project_id"] == "ProjectA" for t in proj_tasks)

    def test_get_running_tasks(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "Gea", "ProjectA")
        orch.end_task("task_001", "cline")
        
        running = orch.get_running_tasks()
        assert len(running) == 1
        assert running[0]["task_id"] == "task_002"


class TestStatistics:
    """Test get_stats method."""

    def test_stats_empty(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        stats = orch.get_stats()
        assert stats["total_tasks"] == 0
        assert stats["running"] == 0
        assert stats["completed"] == 0
        assert stats["agents"] == 0
        assert stats["projects"] == 0

    def test_stats_with_tasks(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "cline", "ProjectA")
        orch.start_task("task_003", "Gea", "ProjectA")
        orch.end_task("task_001", "cline")
        orch.cancel_task("task_002")
        
        stats = orch.get_stats()
        assert stats["total_tasks"] == 3
        assert stats["running"] == 1  # task_003
        assert stats["completed"] == 1  # task_001
        assert stats["cancelled"] == 1  # task_002
        assert stats["agents"] == 2  # cline, Gea
        assert stats["projects"] == 1  # ProjectA

    def test_agent_stats(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "cline", "ProjectB")
        orch.start_task("task_003", "Gea", "ProjectA")
        
        stats = orch.get_stats()
        assert "cline" in stats["agent_stats"]
        assert stats["agent_stats"]["cline"]["total_tasks"] == 2
        assert stats["agent_stats"]["cline"]["running"] == 2

    def test_project_stats(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "Gea", "ProjectA")
        orch.start_task("task_003", "cline", "ProjectB")
        
        stats = orch.get_stats()
        assert "ProjectA" in stats["project_stats"]
        assert stats["project_stats"]["ProjectA"]["total_tasks"] == 2
        assert set(stats["project_stats"]["ProjectA"]["agents"]) == {"cline", "Gea"}


class TestClearTasks:
    """Test clear_tasks method."""

    def test_clear_tasks(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "Gea", "ProjectA")
        
        count = orch.clear_tasks()
        assert count == 2
        assert orch._tasks == {}
        assert orch._agents == {}


class TestConvenienceFunctions:
    """Test convenience functions for MCP/CLI."""

    def test_get_orchestrator_status(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        orch.start_task("task_001", "cline", "ProjectA")
        
        status_str = get_orchestrator_status(base_dir=store)
        assert "Total tasks: 1" in status_str
        assert "Running: 1" in status_str

    def test_list_orchestrator_tasks(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "Gea", "ProjectA")
        
        tasks_str = list_orchestrator_tasks(base_dir=store)
        assert "Found 2 task(s)" in tasks_str

    def test_list_orchestrator_tasks_filtered(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        orch.start_task("task_002", "Gea", "ProjectB")
        
        tasks_str = list_orchestrator_tasks(agent_id="cline", base_dir=store)
        assert "Found 1 task(s)" in tasks_str
        assert "cline" in tasks_str

    def test_list_orchestrator_tasks_no_match(self, tmp_path):
        store = tmp_path / "nexus_store"
        orch = Orchestrator(nexus=Nexus(base_dir=store))
        
        orch.start_task("task_001", "cline", "ProjectA")
        
        tasks_str = list_orchestrator_tasks(agent_id="nonexistent", base_dir=store)
        assert "No tasks found" in tasks_str
