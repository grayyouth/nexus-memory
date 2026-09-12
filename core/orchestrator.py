"""
Nexus Orchestrator — мост между Nexus и Agent Manager (multi-agent orchestration).

Позволяет Agent Manager делегировать задачи агентам с общей памятью:
- На старте задачи: `get_context()` для загрузки памяти
- На завершении: `end_session()` для сохранения результатов
- Общий `project_id` для связанных задач
- Трекинг статусов задач и агентов

Использование:
    from core.orchestrator import Orchestrator

    orch = Orchestrator()

    # Запустить задачу
    task = orch.start_task(
        task_id="task_001",
        agent_id="cline",
        project_id="MyProject",
        description="Fix login bug",
    )

    # Завершить задачу
    orch.end_task(
        task_id="task_001",
        agent_id="cline",
        session_data={"actions": [...], "decisions": [...], "next_steps": [...]},
    )

    # Показать статус
    orch.get_task_status("task_001")
    orch.get_all_tasks()
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.nexus_core import Nexus
from core.summarizer import SessionSummarizer
from core.session_hook import SessionHook
from core.safe_io import atomic_write_json

logger = logging.getLogger(__name__)


class Orchestrator:
    """Bridge between Nexus and Agent Manager for multi-agent tasks.

    Manages task lifecycle, agent tracking, and project memory coordination.
    """

    def __init__(self, nexus: Optional[Nexus] = None) -> None:
        self.nm = nexus or Nexus()
        self._tasks: Dict[str, Dict[str, Any]] = {}  # task_id -> task info
        self._agents: Dict[str, Dict[str, Any]] = {}  # agent_id -> agent info
        self._lock = threading.Lock()
        # Store tasks in the sessions directory
        self._tasks_file = self.nm.base_dir / "sessions" / "orchestrator_tasks.json"

    # --- Task Management ---

    def start_task(
        self,
        task_id: str,
        agent_id: str,
        project_id: str,
        description: str = "",
        model: Optional[str] = None,
        variant: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Start a new task in the orchestrator.

        Loads context from Nexus for the agent and project,
        then tracks the task with its status.

        Args:
            task_id: Unique task identifier.
            agent_id: Agent executing the task (e.g., "cline", "Gea").
            project_id: Project the task belongs to.
            description: Human-readable task description.
            model: Optional model name for the agent.
            variant: Optional reasoning variant.
            tags: Optional task tags.

        Returns:
            Task info dict with status "running" and loaded context.
        """
        with self._lock:
            task_info = {
                "task_id": task_id,
                "agent_id": agent_id,
                "project_id": project_id,
                "description": description,
                "model": model,
                "variant": variant,
                "tags": tags or [],
                "status": "running",
                "started_at": datetime.now().isoformat(),
                "ended_at": None,
                "result": None,
                "context_loaded": False,
            }

            # Load context from Nexus
            context = self.nm.get_context_for_agent(agent_id=agent_id, project_id=project_id)
            if context:
                task_info["context_loaded"] = True
                task_info["context_preview"] = context[:200]

            self._tasks[task_id] = task_info

            # Update agent info
            if agent_id not in self._agents:
                self._agents[agent_id] = {
                    "agent_id": agent_id,
                    "tasks": [],
                    "last_task": None,
                    "last_project": None,
                    "started_at": task_info["started_at"],
                }
            self._agents[agent_id]["tasks"].append(task_id)
            self._agents[agent_id]["last_task"] = task_id
            self._agents[agent_id]["last_project"] = project_id

            self._save_tasks()
            logger.info("Task started: %s (agent=%s, project=%s)", task_id, agent_id, project_id)

            return task_info

    def end_task(
        self,
        task_id: str,
        agent_id: str,
        session_data: Optional[Dict[str, Any]] = None,
        collapse_after: bool = False,
        keep_last: int = 1,
    ) -> Dict[str, Any]:
        """End a task and save session data to Nexus.

        Args:
            task_id: Task to end.
            agent_id: Agent that ran the task.
            session_data: Session data for Nexus archiving.
            collapse_after: Also collapse old archives.
            keep_last: How many archives to keep.

        Returns:
            Task info with updated status and optional summary.
        """
        with self._lock:
            if task_id not in self._tasks:
                return {"status": "error", "message": f"Task not found: {task_id}"}

            task_info = self._tasks[task_id]
            task_info["status"] = "completed"
            task_info["ended_at"] = datetime.now().isoformat()

            # Save to Nexus if session_data provided
            summary = None
            if session_data:
                hook = SessionHook(self.nm)
                info = hook.end_session(
                    agent_id=agent_id,
                    session_data=session_data,
                    collapse_after=collapse_after,
                    keep_last=keep_last,
                )
                if info.get("status") == "ok":
                    summary = {
                        "archive": info.get("archive_file"),
                        "stats": info.get("stats"),
                    }
                    task_info["result"] = summary

            self._save_tasks()
            logger.info("Task ended: %s (agent=%s)", task_id, agent_id)

            return {**task_info, "summary": summary}

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """Cancel a running task."""
        with self._lock:
            if task_id not in self._tasks:
                return {"status": "error", "message": f"Task not found: {task_id}"}

            task_info = self._tasks[task_id]
            task_info["status"] = "cancelled"
            task_info["ended_at"] = datetime.now().isoformat()

            self._save_tasks()
            logger.info("Task cancelled: %s", task_id)

            return task_info

    # --- Query Methods ---

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific task."""
        return self._tasks.get(task_id)

    def get_agent_tasks(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all tasks for an agent."""
        return [t for t in self._tasks.values() if t["agent_id"] == agent_id]

    def get_project_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all tasks for a project."""
        return [t for t in self._tasks.values() if t["project_id"] == project_id]

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks."""
        return list(self._tasks.values())

    def get_running_tasks(self) -> List[Dict[str, Any]]:
        """Get all running tasks."""
        return [t for t in self._tasks.values() if t["status"] == "running"]

    def get_agents(self) -> Dict[str, Dict[str, Any]]:
        """Get all tracked agents."""
        return dict(self._agents)

    # --- Statistics ---

    def get_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics."""
        all_tasks = list(self._tasks.values())
        running = len([t for t in all_tasks if t["status"] == "running"])
        completed = len([t for t in all_tasks if t["status"] == "completed"])
        cancelled = len([t for t in all_tasks if t["status"] == "cancelled"])

        # Per-agent stats
        agent_stats = {}
        for agent_id, info in self._agents.items():
            agent_tasks = [t for t in all_tasks if t["agent_id"] == agent_id]
            agent_stats[agent_id] = {
                "total_tasks": len(agent_tasks),
                "running": len([t for t in agent_tasks if t["status"] == "running"]),
                "completed": len([t for t in agent_tasks if t["status"] == "completed"]),
                "cancelled": len([t for t in agent_tasks if t["status"] == "cancelled"]),
                "last_task": info.get("last_task"),
                "last_project": info.get("last_project"),
            }

        # Per-project stats
        project_ids = set(t["project_id"] for t in all_tasks)
        project_stats = {}
        for pid in project_ids:
            proj_tasks = [t for t in all_tasks if t["project_id"] == pid]
            project_stats[pid] = {
                "total_tasks": len(proj_tasks),
                "agents": list(set(t["agent_id"] for t in proj_tasks)),
            }

        return {
            "total_tasks": len(all_tasks),
            "running": running,
            "completed": completed,
            "cancelled": cancelled,
            "agents": len(self._agents),
            "projects": len(project_ids),
            "agent_stats": agent_stats,
            "project_stats": project_stats,
        }

    # --- Persistence ---

    def _save_tasks(self) -> None:
        """Save tasks to JSON file (atomically — temp + rename, Phase 1)."""
        data = {
            "version": "0.9.4",
            "timestamp": datetime.now().isoformat(),
            "tasks": self._tasks,
            "agents": self._agents,
        }
        atomic_write_json(self._tasks_file, data)

    def load_tasks(self) -> None:
        """Load tasks from JSON file."""
        if not self._tasks_file.exists():
            return
        try:
            with open(self._tasks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._tasks = data.get("tasks", {})
            self._agents = data.get("agents", {})
            logger.info("Loaded %d tasks, %d agents", len(self._tasks), len(self._agents))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load tasks: %s", e)

    def clear_tasks(self) -> int:
        """Clear all tracked tasks. Returns count of cleared tasks."""
        count = len(self._tasks)
        self._tasks.clear()
        self._agents.clear()
        self._save_tasks()
        logger.info("Cleared %d tasks", count)
        return count


# --- Convenience functions for MCP/CLI ---

def start_orchestrator_task(
    task_id: str,
    agent_id: str,
    project_id: str,
    description: str = "",
    model: Optional[str] = None,
    variant: Optional[str] = None,
    base_dir: Optional[str | Path] = None,
) -> str:
    """Start a task via MCP."""
    if base_dir:
        orch = Orchestrator(nexus=Nexus(base_dir=Path(base_dir)))
    else:
        orch = Orchestrator()
    orch.load_tasks()
    task = orch.start_task(
        task_id=task_id,
        agent_id=agent_id,
        project_id=project_id,
        description=description,
        model=model,
        variant=variant,
    )
    context_status = "loaded" if task["context_loaded"] else "none"
    return (
        f"Task started: {task_id}\n"
        f"Agent: {agent_id} | Project: {project_id}\n"
        f"Context: {context_status}\n"
        f"Description: {description}"
    )


def end_orchestrator_task(
    task_id: str,
    agent_id: str,
    session_data: Optional[Dict[str, Any]] = None,
    base_dir: Optional[str | Path] = None,
) -> str:
    """End a task via MCP."""
    if base_dir:
        orch = Orchestrator(nexus=Nexus(base_dir=Path(base_dir)))
    else:
        orch = Orchestrator()
    orch.load_tasks()
    result = orch.end_task(task_id=task_id, agent_id=agent_id, session_data=session_data)
    if result.get("status") == "error":
        return f"Error: {result.get('message')}"
    return f"Task ended: {task_id} (status={result.get('status')})"


def get_orchestrator_status(base_dir: Optional[str | Path] = None) -> str:
    """Get orchestrator status via MCP."""
    if base_dir:
        orch = Orchestrator(nexus=Nexus(base_dir=Path(base_dir)))
    else:
        orch = Orchestrator()
    orch.load_tasks()
    stats = orch.get_stats()

    lines = [
        f"Total tasks: {stats['total_tasks']}",
        f"Running: {stats['running']}",
        f"Completed: {stats['completed']}",
        f"Cancelled: {stats['cancelled']}",
        f"Agents: {stats['agents']}",
        f"Projects: {stats['projects']}",
    ]

    if stats["agent_stats"]:
        lines.append("\nAgent stats:")
        for aid, astat in stats["agent_stats"].items():
            lines.append(f"  {aid}: {astat['total_tasks']} tasks, {astat['running']} running")

    if stats["project_stats"]:
        lines.append("\nProject stats:")
        for pid, pstat in stats["project_stats"].items():
            lines.append(f"  {pid}: {pstat['total_tasks']} tasks, agents={', '.join(pstat['agents'])}")

    return "\n".join(lines)


def list_orchestrator_tasks(
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    base_dir: Optional[str | Path] = None,
) -> str:
    """List tasks with optional filters."""
    if base_dir:
        orch = Orchestrator(nexus=Nexus(base_dir=Path(base_dir)))
    else:
        orch = Orchestrator()
    orch.load_tasks()

    tasks = orch.get_all_tasks()

    if agent_id:
        tasks = [t for t in tasks if t["agent_id"] == agent_id]
    if project_id:
        tasks = [t for t in tasks if t["project_id"] == project_id]
    if status:
        tasks = [t for t in tasks if t["status"] == status]

    if not tasks:
        return "No tasks found."

    lines = [f"Found {len(tasks)} task(s):"]
    for t in tasks:
        status_icon = {"running": "🟡", "completed": "🟢", "cancelled": "⚪"}.get(t["status"], "⚪")
        lines.append(
            f"  {status_icon} {t['task_id']} | {t['agent_id']} | {t['project_id']} | {t['status']}"
        )
        if t.get("description"):
            lines.append(f"      {t['description']}")

    return "\n".join(lines)
