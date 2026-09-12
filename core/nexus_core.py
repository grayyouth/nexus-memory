"""
Memory Manager: Core logic for the Smart Memory system.

This module provides the programmatic interface to interact with the memory store.
It handles:
- Indexing and searching chunks of knowledge.
- Managing agent sessions (archiving, restoring, summaries).
- Managing cross-agent collaborative knowledge.
"""

import os
import json
import hashlib
from datetime import datetime
from typing import List, Dict, Optional, Any, Callable
from pathlib import Path

from core.config import config
from core.trust import compute_trust, clamp_trust
from core.safe_io import (
    atomic_write_text,
    atomic_write_json,
    read_json as _safe_read_json,
    update_json_file,
)

# Default base directory (when Nexus() is created without arguments).
# NOTE: All runtime paths are derived from the instance's self.base_dir, so a
# Nexus(base_dir=...) instance is fully self-contained. The module-level
# constants below only describe the default store layout and are kept for
# backward compatibility (core.ingestion imports INPUT_RAW_DIR /
# INPUT_PROCESSED_DIR).
BASE_DIR = Path(__file__).resolve().parent.parent / "nexus_store"

# Default subdirectories (deprecated for direct use; see instance attrs)
INPUT_RAW_DIR = BASE_DIR / "input_docs" / "raw"
INPUT_PROCESSED_DIR = BASE_DIR / "input_docs" / "processed"
MAIN_LIB_DIR = BASE_DIR / "main_library"
INDEX_DIR = MAIN_LIB_DIR / "_index"

# Default session directories
SESSIONS_ARCHIVED_DIR = BASE_DIR / "sessions" / "archived_sessions"
SESSIONS_SUMMARIES_DIR = BASE_DIR / "sessions" / "session_summaries"
SESSIONS_CROSS_DIR = BASE_DIR / "sessions" / "cross_sessions"

# Default index files
CHUNKS_INDEX_FILE = INDEX_DIR / "chunks_index.json"
TAGS_INDEX_FILE = INDEX_DIR / "tags.json"
SESSIONS_INDEX_FILE = BASE_DIR / "sessions" / "index.json"

class Nexus:
    """Central class for managing the smart memory system."""

    def __init__(self, base_dir: Optional[Path] = None):
        # Resolve base_dir: explicit > config > default
        if base_dir is None:
            configured = config.store_base_dir
            if configured:
                base_dir = configured
            else:
                base_dir = Path(__file__).resolve().parent.parent / "nexus_store"
        self.base_dir = Path(base_dir).resolve()
        # All paths derive from self.base_dir so a custom store location works.
        self.input_raw_dir = self.base_dir / "input_docs" / "raw"
        self.input_processed_dir = self.base_dir / "input_docs" / "processed"
        self.main_lib_dir = self.base_dir / "main_library"
        self.index_dir = self.main_lib_dir / "_index"
        self.chunks_index_file = self.index_dir / "chunks_index.json"
        self.tags_index_file = self.index_dir / "tags.json"
        self.sessions_archived_dir = self.base_dir / "sessions" / "archived_sessions"
        self.sessions_summaries_dir = self.base_dir / "sessions" / "session_summaries"
        self.sessions_cross_dir = self.base_dir / "sessions" / "cross_sessions"
        self.sessions_index_file = self.base_dir / "sessions" / "index.json"
        self._ensure_directories()

    def _ensure_directories(self):
        """Creates all necessary directories if they don't exist."""
        dirs = [
            self.input_raw_dir, self.input_processed_dir, self.main_lib_dir, self.index_dir,
            self.main_lib_dir / "projects", self.main_lib_dir / "general" / "snippets",
            self.sessions_archived_dir, self.sessions_summaries_dir, self.sessions_cross_dir
        ]
        for dir_path in dirs:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize index files if they don't exist
        if not self.chunks_index_file.exists():
            self._write_json(self.chunks_index_file, [])
        if not self.tags_index_file.exists():
            self._write_json(self.tags_index_file, {})
        if not self.sessions_index_file.exists():
            self._write_json(self.sessions_index_file, {})

    def _write_json(self, path: Path, data: Any) -> None:
        """Atomic JSON write (temp + rename, Phase 1 of the autonomous-server
        roadmap): a crash or a concurrent reader never sees a half-written
        file — only the old or the new complete content."""
        atomic_write_json(path, data)

    def _read_json(self, path: Path) -> Any:
        return _safe_read_json(path)

    def _update_json(
        self,
        path: Path,
        mutator: Callable[[Any], Any],
        empty: Any = None,
        timeout: float = 10.0,
    ) -> Any:
        """Locked read-modify-write JSON cycle (file lock + atomic write).

        Reads the current content (or ``empty`` when the file is missing or
        corrupt), applies ``mutator(data)`` under a cross-process file lock
        and atomically persists the result. Returns the new data.

        This is the single safe primitive for concurrent writers (multiple
        MCP processes / agents) on the same index file.
        """
        return update_json_file(path, mutator, empty=empty, timeout=timeout)

    def _generate_id(self, content: str) -> str:
        """Generates a unique ID based on content hash."""
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    
    # --- Knowledge Base Management ---

    def add_chunk(self, content: str, tags: List[str], project_id: Optional[str] = None, source: Optional[str] = None, agent_id: Optional[str] = None, trust: Optional[float] = None) -> str:
        """
        Adds a single chunk of knowledge to the library and index.
        Returns the unique ID of the chunk.

        If `trust` is not given, it is computed from the source by
        core.trust.compute_trust (official docs -> 1.0, unknown URL -> 0.4,
        local docs -> 0.8, none -> 0.5).
        """
        if trust is None:
            trust = compute_trust(source)
        trust = clamp_trust(trust)

        chunk_id = self._generate_id(content)

        # Determine content file path
        if project_id:
            category_dir = self.main_lib_dir / "projects" / project_id
        else:
            category_dir = self.main_lib_dir / "general"

        # Use a snippets directory for individual chunks
        snippet_path = category_dir / "snippets" / f"{chunk_id}.md"
        snippet_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save snippet content (atomically — temp + rename).
        header_lines = ["---", f"tags: {json.dumps(tags)}"]
        if project_id:
            header_lines.append(f"project_id: {project_id}")
        if source:
            header_lines.append(f"source: {source}")
        if agent_id:
            header_lines.append(f"agent_id: {agent_id}")
        header_lines.append(f"trust: {trust}")
        header_lines.append(f"created_at: {datetime.now().isoformat()}")
        snippet_body = "\n".join(header_lines) + "\n---\n\n" + content
        atomic_write_text(snippet_path, snippet_body)

        index_entry = {
            "id": chunk_id,
            "tags": tags,
            "project_id": project_id,
            "source_file": str(snippet_path.relative_to(self.base_dir)),
            "created_at": datetime.now().isoformat(),
            "agent_id": agent_id,
            "trust": trust,
        }

        # Update chunks index — locked read-modify-write so concurrent
        # MCP processes never lose each other's entries (Phase 1).
        def _mutate_chunks(chunks_index: Any) -> Any:
            chunks_index = chunks_index or []
            if any(c.get("id") == chunk_id for c in chunks_index):
                return chunks_index
            chunks_index.append(index_entry)
            return chunks_index

        def _mutate_tags(tags_index: Any) -> Any:
            tags_index = tags_index or {}
            for tag in tags:
                if tag not in tags_index:
                    tags_index[tag] = []
                if chunk_id not in tags_index[tag]:
                    tags_index[tag].append(chunk_id)
            return tags_index

        updated = self._update_json(self.chunks_index_file, _mutate_chunks, empty=[])
        if any(c.get("id") == chunk_id for c in updated):
            self._update_json(self.tags_index_file, _mutate_tags, empty={})

        return chunk_id

    def search(self, query: str, tags: Optional[List[str]] = None, project_id: Optional[str] = None, agent_id: Optional[str] = None) -> List[Dict]:
        """
        Searches the knowledge base for relevant chunks.
        Performs a simple keyword match for now.
        """
        chunks_index = self._read_json(self.chunks_index_file) or []
        if not chunks_index:
            return []

        results = []
        query_lower = query.lower()
        
        for entry in chunks_index:
            # Filter by tags if provided
            if tags and not all(t in entry.get('tags', []) for t in tags):
                continue
            # Filter by project_id if provided
            if project_id and entry.get('project_id') != project_id:
                continue
            # Filter by agent_id if provided (for personal notes)
            if agent_id and entry.get('agent_id') != agent_id and not entry.get('project_id'):
                continue

            snippet_path = self.base_dir / entry['source_file']
            if snippet_path.exists():
                try:
                    with open(snippet_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if query_lower in content.lower():
                            results.append(entry)
                except Exception as e:
                    print(f"Error reading {snippet_path}: {e}")
        
        return results
        
    # --- Session Management ---

    def archive_session(self, agent_id: str, session_data: Dict, project_id: Optional[str] = None) -> str:
        """
        Archives a raw session for an agent.
        `session_data` should contain the raw session history/log.
        `project_id` (optional) tags the session with a project so the
        cross-agent project digest (ProjectDigest) can attribute it.
        """
        base_ts = datetime.now().strftime("%Y%m%d_%H%M")
        agent_dir = self.sessions_archived_dir / f"agent_{agent_id}"
        agent_dir.mkdir(parents=True, exist_ok=True)
        # Ensure a unique archive file even when several sessions are archived
        # within the same minute (timestamps are minute-resolution).
        timestamp = base_ts
        counter = 1
        while (agent_dir / f"session_{timestamp}.json").exists():
            timestamp = f"{base_ts}_{counter}"
            counter += 1
        session_file = agent_dir / f"session_{timestamp}.json"

        archive_data = {
            "agent_id": agent_id,
            "timestamp": timestamp,
            "file_path": str(session_file.relative_to(self.base_dir)),
            "session_data": session_data
        }
        if project_id:
            archive_data["project_id"] = project_id
        self._write_json(session_file, archive_data)

        # Update sessions index — locked read-modify-write so concurrent
        # MCP processes never lose each other's history entries (Phase 1).
        history_entry = {
            "timestamp": timestamp,
            "file": str(session_file.relative_to(self.base_dir)),
            "status": "archived"
        }
        if project_id:
            history_entry["project_id"] = project_id

        def _mutate_sessions(sessions_index: Any) -> Any:
            sessions_index = sessions_index or {}
            if agent_id not in sessions_index:
                sessions_index[agent_id] = {"last_session_summary": None, "history": []}
            sessions_index[agent_id]["history"].append(history_entry)
            return sessions_index

        self._update_json(self.sessions_index_file, _mutate_sessions, empty={})

        return str(session_file.relative_to(self.base_dir))

    def store_session_summary(self, agent_id: str, summary: str, session_id: Optional[str] = None) -> str:
        """
        Stores a generated summary for an agent's session.
        """
        timestamp = session_id or datetime.now().strftime("%Y%m%d_%H%M")
        agent_summaries_dir = self.sessions_summaries_dir / f"agent_{agent_id}"
        agent_summaries_dir.mkdir(parents=True, exist_ok=True)
        summary_file = agent_summaries_dir / f"summary_{timestamp}.md"
        if session_id is None:
            # Unique auto name even for several summaries within one minute.
            base_ts = timestamp
            counter = 1
            while summary_file.exists():
                timestamp = f"{base_ts}_{counter}"
                summary_file = agent_summaries_dir / f"summary_{timestamp}.md"
                counter += 1

        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(f"# Session Summary for Agent: {agent_id}\n\n")
            f.write(f"**Timestamp:** {timestamp}\n\n")
            f.write(summary)

        # Update sessions index — locked read-modify-write (Phase 1).
        def _mutate_sessions(sessions_index: Any) -> Any:
            sessions_index = sessions_index or {}
            if agent_id not in sessions_index:
                sessions_index[agent_id] = {"last_session_summary": None, "history": []}
            sessions_index[agent_id]["last_session_summary"] = str(
                summary_file.relative_to(self.base_dir)
            )
            return sessions_index

        self._update_json(self.sessions_index_file, _mutate_sessions, empty={})

        return str(summary_file.relative_to(self.base_dir))

    def get_context_for_agent(self, agent_id: str, project_id: Optional[str] = None) -> str:
        """
        Retrieves the context block for an agent starting a new session.
        This includes their personal session summary and any shared project knowledge.
        """
        context_parts = []

        # 1. Get personal session summary
        sessions_index = self._read_json(self.sessions_index_file) or {}
        agent_info = sessions_index.get(agent_id, {})
        last_summary_path = agent_info.get("last_session_summary")
        
        summary_loaded = False
        if last_summary_path:
            summary_file = self.base_dir / last_summary_path
            if summary_file.exists():
                with open(summary_file, 'r', encoding='utf-8') as f:
                    context_parts.append(f.read())
                summary_loaded = True
        if not summary_loaded:
            # Fallback: if there is no latest-session summary, serve the
            # collapsed history summary (produced by SessionSummarizer).
            collapsed = agent_info.get("collapsed_summary")
            if collapsed:
                collapsed_file = self.base_dir / collapsed
                if collapsed_file.exists():
                    with open(collapsed_file, 'r', encoding='utf-8') as f:
                        context_parts.append(f.read())

        # 2. Get shared project knowledge (if project_id is given)
        shared = self._shared_project_knowledge(project_id)
        if shared:
            context_parts.append(shared)

        # 3. Cross-agent project digest: what changed while the agent was away.
        if project_id:
            try:
                from core.project_digest import ProjectDigest
            except Exception:
                ProjectDigest = None
            if ProjectDigest is not None:
                # "Since" = the caller's own last archived session, so the
                # digest shows work done by OTHERS after that point.
                history = (sessions_index.get(agent_id) or {}).get("history") or []
                since = history[-1].get("timestamp") if history else None
                try:
                    digest = ProjectDigest(self).get_digest(
                        project_id, since=since, empty_notice=False
                    )
                except Exception as e:
                    digest = f"# Project digest error: {e}"
                if digest:
                    context_parts.append(digest)

        return "\n---\n".join(context_parts) if context_parts else ""

    def _shared_project_knowledge(self, project_id: Optional[str]) -> str:
        """Joint decisions of a project as a context block ("" if none)."""
        if not project_id:
            return ""
        collab_dir = self.sessions_cross_dir / f"collab_{project_id}"
        joint_decisions_file = collab_dir / "joint_decisions.json"
        if not joint_decisions_file.exists():
            return ""
        shared_knowledge = self._read_json(joint_decisions_file)
        if not shared_knowledge:
            return ""
        parts = [f"## Shared Project Knowledge ({project_id})"]
        for decision in shared_knowledge:
            parts.append(f"- **Decision:** {decision.get('decision', 'N/A')}")
            parts.append(f"  - **By Agents:** {', '.join(decision.get('by_agents', []))}")
            parts.append(f"  - **Reason:** {decision.get('reason', 'N/A')}\n")
        return "\n".join(parts)

    # --- Collaborative Knowledge ---

    def add_joint_decision(self, project_id: str, decision: Dict) -> None:
        """
        Adds a decision made collaboratively by multiple agents.
        Each stored decision gets a `recorded_at` timestamp (ISO) unless the
        caller already provided one — so ProjectDigest can filter by time.
        """
        collab_dir = self.sessions_cross_dir / f"collab_{project_id}"
        joint_decisions_file = collab_dir / "joint_decisions.json"
        joint_decisions_file.parent.mkdir(parents=True, exist_ok=True)

        stored = dict(decision)
        if "recorded_at" not in stored and "timestamp" not in stored:
            stored["recorded_at"] = datetime.now().isoformat()

        def _mutate_decisions(decisions: Any) -> Any:
            decisions = decisions or []
            decisions.append(stored)
            return decisions

        self._update_json(joint_decisions_file, _mutate_decisions, empty=[])