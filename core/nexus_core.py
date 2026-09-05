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
from typing import List, Dict, Optional, Any
from pathlib import Path

from core.config import config

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
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return None

    def _generate_id(self, content: str) -> str:
        """Generates a unique ID based on content hash."""
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    
    # --- Knowledge Base Management ---

    def add_chunk(self, content: str, tags: List[str], project_id: Optional[str] = None, source: Optional[str] = None, agent_id: Optional[str] = None) -> str:
        """
        Adds a single chunk of knowledge to the library and index.
        Returns the unique ID of the chunk.
        """
        chunk_id = self._generate_id(content)

        # Determine content file path
        if project_id:
            category_dir = self.main_lib_dir / "projects" / project_id
        else:
            category_dir = self.main_lib_dir / "general"

        # Use a snippets directory for individual chunks
        snippet_path = category_dir / "snippets" / f"{chunk_id}.md"
        snippet_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Save snippet content
        with open(snippet_path, 'w', encoding='utf-8') as f:
            f.write(f"---\n")
            f.write(f"tags: {json.dumps(tags)}\n")
            if project_id: f.write(f"project_id: {project_id}\n")
            if source: f.write(f"source: {source}\n")
            if agent_id: f.write(f"agent_id: {agent_id}\n")
            f.write(f"created_at: {datetime.now().isoformat()}\n")
            f.write(f"---\n\n")
            f.write(content)

        # Update chunks index
        chunks_index = self._read_json(self.chunks_index_file) or []
        existing_ids = [c['id'] for c in chunks_index]
        if chunk_id not in existing_ids:
            index_entry = {
                "id": chunk_id,
                "tags": tags,
                "project_id": project_id,
                "source_file": str(snippet_path.relative_to(self.base_dir)),
                "created_at": datetime.now().isoformat(),
                "agent_id": agent_id
            }
            chunks_index.append(index_entry)
            self._write_json(self.chunks_index_file, chunks_index)

            # Update tags index
            tags_index = self._read_json(self.tags_index_file) or {}
            for tag in tags:
                if tag not in tags_index:
                    tags_index[tag] = []
                if chunk_id not in tags_index[tag]:
                    tags_index[tag].append(chunk_id)
            self._write_json(self.tags_index_file, tags_index)

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

    def archive_session(self, agent_id: str, session_data: Dict) -> str:
        """
        Archives a raw session for an agent.
        `session_data` should contain the raw session history/log.
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
        self._write_json(session_file, archive_data)

        # Update sessions index
        sessions_index = self._read_json(self.sessions_index_file) or {}
        if agent_id not in sessions_index:
            sessions_index[agent_id] = {"last_session_summary": None, "history": []}
        sessions_index[agent_id]["history"].append({
            "timestamp": timestamp,
            "file": str(session_file.relative_to(self.base_dir)),
            "status": "archived"
        })
        self._write_json(self.sessions_index_file, sessions_index)

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

        # Update sessions index
        sessions_index = self._read_json(self.sessions_index_file) or {}
        if agent_id not in sessions_index:
            sessions_index[agent_id] = {"last_session_summary": None, "history": []}
        sessions_index[agent_id]["last_session_summary"] = str(summary_file.relative_to(self.base_dir))
        
        self._write_json(self.sessions_index_file, sessions_index)

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
        if project_id:
            collab_dir = self.sessions_cross_dir / f"collab_{project_id}"
            joint_decisions_file = collab_dir / "joint_decisions.json"
            if joint_decisions_file.exists():
                shared_knowledge = self._read_json(joint_decisions_file)
                if shared_knowledge:
                    context_parts.append(f"## Shared Project Knowledge ({project_id})")
                    for decision in shared_knowledge:
                        context_parts.append(f"- **Decision:** {decision.get('decision', 'N/A')}")
                        context_parts.append(f"  - **By Agents:** {', '.join(decision.get('by_agents', []))}")
                        context_parts.append(f"  - **Reason:** {decision.get('reason', 'N/A')}\n")
        
        return "\n---\n".join(context_parts) if context_parts else ""

    # --- Collaborative Knowledge ---

    def add_joint_decision(self, project_id: str, decision: Dict) -> None:
        """
        Adds a decision made collaboratively by multiple agents.
        """
        collab_dir = self.sessions_cross_dir / f"collab_{project_id}"
        joint_decisions_file = collab_dir / "joint_decisions.json"
        joint_decisions_file.parent.mkdir(parents=True, exist_ok=True)

        decisions = self._read_json(joint_decisions_file) or []
        decisions.append(decision)
        self._write_json(joint_decisions_file, decisions)