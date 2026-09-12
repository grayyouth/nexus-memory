"""
Nexus Project Digest — cross-agent "what changed in this project" block.

The per-agent session machinery (archive / summary) is individual by design:
each agent archives under ``agent_<id>`` and sees only its own summary via
``get_context``. This module is the missing cross-agent layer: it aggregates
ALL agents' project-tagged sessions, joint decisions and recent library
chunks into a single markdown block answering the question:
"What happened in this project while I was away?"

Design notes:
- Pure heuristics, no LLM — mirrors ``SessionSummarizer`` and reuses its
  ``extract_structure`` for the *Сделано / Решения / Дальше* buckets.
- Sessions are included ONLY if they were archived WITH a ``project_id``
  (``archive_session(..., project_id=...)`` / ``end_session(..., project_id=...)``).
  Legacy archives (no project tag) are intentionally not attributed to any
  project.
- ``since`` accepts a session timestamp (``20260911_0338``), ISO datetime or
  plain date; it is an inclusive lower bound.
- Tunables live in ``nexus_config.json`` under ``digest.*``.

Usage:
    from core.nexus_core import Nexus
    from core.project_digest import ProjectDigest
    block = ProjectDigest(Nexus()).get_digest("MyProject", since="20260901_0000")
"""

import json
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.nexus_core import Nexus
from core.config import config
from core.summarizer import SessionSummarizer

# --- Tunables (overridable via nexus_config.json → digest.*) ---
_cfg = getattr(config, "digest", None) or {}
MAX_SESSIONS_PER_AGENT = _cfg.get("max_sessions_per_agent", 6)
MAX_ITEMS_PER_BUCKET = _cfg.get("max_items_per_bucket", 4)
MAX_ITEM_CHARS = _cfg.get("max_item_chars", 300)
MAX_DECISIONS = _cfg.get("max_decisions", 8)
MAX_CHUNKS = _cfg.get("max_chunks", 8)
CHUNK_PREVIEW_CHARS = _cfg.get("chunk_preview_chars", 140)

_SESSION_TS_RE = re.compile(r"^(\d{8})_(\d{4})(?:_\d+)?$")


def parse_time(text: Any) -> Optional[datetime]:
    """Parse a session timestamp / ISO / date string into a datetime.

    Accepted formats:
      - session style: ``20260911_0338`` or ``20260911_0338_2``
      - ISO datetime:  ``2026-09-11T03:38:12``
      - spaced:        ``2026-09-11 03:38:12``
      - plain date:    ``2026-09-11``
    Returns None when nothing parseable.
    """
    if text is None:
        return None
    t = str(text).strip()
    if not t:
        return None
    match = _SESSION_TS_RE.match(t)
    if match:
        try:
            return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M")
        except ValueError:
            return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


def _truncate(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _bucket_line(buckets: Dict[str, List[str]], key: str) -> str:
    items = buckets.get(key) or []
    if not items:
        return ""
    return "; ".join(_truncate(i) for i in items[:MAX_ITEMS_PER_BUCKET])


def _chunk_preview(path) -> str:
    """First non-front-matter characters of a chunk file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:CHUNK_PREVIEW_CHARS] + ("…" if len(text) > CHUNK_PREVIEW_CHARS else "")


class ProjectDigest:
    """Builds a cross-agent digest block for one project."""

    def __init__(self, nexus: Nexus):
        self.nm = nexus
        self._summarizer = SessionSummarizer(nexus)

    # --- Collection ---

    def collect(
        self,
        project_id: str,
        since: Any = None,
        exclude_agent: Optional[str] = None,
        agent_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate project activity into a structured dict.

        Args:
            project_id: Project to scope by (exact match on archive/chunk tag).
            since: Lower bound; only sessions/decisions/chunks newer than it.
            exclude_agent: Do not include sessions archived by this agent.
            agent_filter: Show sessions ONLY for this agent.

        Returns:
            {"project_id", "agents": {agent: [sessions...]}, "decisions": [...],
             "chunks": [...], "stats": {...}}
        """
        since_dt = parse_time(since)
        result: Dict[str, Any] = {
            "project_id": project_id,
            "agents": {},
            "decisions": [],
            "chunks": [],
            "stats": {},
        }

        index = self.nm._read_json(self.nm.sessions_index_file) or {}
        agents: Dict[str, List[Dict]] = {}
        for agent_id, info in index.items():
            if agent_filter and agent_id != agent_filter:
                continue
            if exclude_agent and agent_id == exclude_agent:
                continue
            rows = self._agent_rows(agent_id, info, project_id, since_dt)
            if rows:
                agents[agent_id] = rows
        result["agents"] = agents

        decisions = self._project_decisions(project_id, since_dt)
        result["decisions"] = decisions

        chunks = self._project_chunks(project_id, since_dt)
        result["chunks"] = chunks

        n_sessions = sum(len(rows) for rows in agents.values())
        result["stats"] = {
            "agents": len(agents),
            "sessions": n_sessions,
            "decisions": len(decisions),
            "chunks": len(chunks),
        }
        return result

    def _agent_rows(
        self,
        agent_id: str,
        info: Dict[str, Any],
        project_id: str,
        since_dt: Optional[datetime],
    ) -> List[Dict[str, Any]]:
        """Project-tagged session rows for one agent, newest first."""
        rows: List[Dict[str, Any]] = []
        for entry in (info.get("history") or []):
            if entry.get("project_id") != project_id:
                continue
            if entry.get("status") == "collapsed":
                continue
            ts = entry.get("timestamp")
            ts_dt = parse_time(ts)
            if since_dt is not None and (ts_dt is None or ts_dt < since_dt):
                continue
            if not entry.get("file"):
                continue
            path = self.nm.base_dir / entry["file"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    archive = json.load(f)
            except (OSError, ValueError):
                continue
            buckets = self._summarizer.extract_structure(archive.get("session_data"))
            rows.append({
                "timestamp": ts,
                "done": _bucket_line(buckets, "done"),
                "decisions": _bucket_line(buckets, "decisions"),
                "next": _bucket_line(buckets, "next"),
                "other": _bucket_line(buckets, "other"),
            })
        rows.sort(key=lambda r: str(r.get("timestamp", "")), reverse=True)
        return rows[:MAX_SESSIONS_PER_AGENT]

    def _project_decisions(
        self, project_id: str, since_dt: Optional[datetime]
    ) -> List[Dict[str, Any]]:
        collab_file = self.nm.sessions_cross_dir / f"collab_{project_id}" / "joint_decisions.json"
        if not collab_file.exists():
            return []
        decisions = self.nm._read_json(collab_file) or []
        out: List[Dict[str, Any]] = []
        for dec in decisions:
            recorded = dec.get("recorded_at") or dec.get("timestamp")
            recorded_dt = parse_time(recorded)
            if since_dt is not None and (recorded_dt is None or recorded_dt < since_dt):
                continue
            out.append({
                "decision": dec.get("decision", ""),
                "by_agents": dec.get("by_agents", []),
                "reason": dec.get("reason", ""),
                "recorded_at": recorded,
            })
        out.sort(key=lambda d: str(d.get("recorded_at") or ""), reverse=True)
        return out[:MAX_DECISIONS]

    def _project_chunks(
        self, project_id: str, since_dt: Optional[datetime]
    ) -> List[Dict[str, Any]]:
        index = self.nm._read_json(self.nm.chunks_index_file) or []
        out: List[Dict[str, Any]] = []
        for entry in index:
            if entry.get("project_id") != project_id:
                continue
            created = entry.get("created_at") or ""
            created_dt = parse_time(created)
            if since_dt is not None and (created_dt is None or created_dt < since_dt):
                continue
            preview = _chunk_preview(self.nm.base_dir / entry.get("source_file", ""))
            if not preview:
                continue
            out.append({
                "id": entry.get("id", "")[:8],
                "created_at": created,
                "agent_id": entry.get("agent_id"),
                "preview": preview,
            })
        out.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
        return out[:MAX_CHUNKS]

    # --- Rendering ---

    def get_digest(
        self,
        project_id: str,
        since: Any = None,
        exclude_agent: Optional[str] = None,
        agent_filter: Optional[str] = None,
        empty_notice: bool = True,
    ) -> str:
        """Render the project digest as a markdown block.

        With ``empty_notice=False`` an empty digest returns "" (useful when
        embedding the digest into get_context — an empty project must not
        produce a "no data" notice there).
        """
        data = self.collect(
            project_id, since=since,
            exclude_agent=exclude_agent, agent_filter=agent_filter,
        )
        stats = data["stats"]
        if not (stats["sessions"] or stats["decisions"] or stats["chunks"]):
            if not empty_notice:
                return ""
            return f"Нет данных по проекту `{project_id}`: нет сессий/решений/заметок за указанный период."

        lines = [
            f"# Проектный дайджест: {project_id}",
            "**Сессий:** {} · **Агентов:** {} · **Решений:** {} · **Заметок:** {}".format(
                stats["sessions"], stats["agents"], stats["decisions"], stats["chunks"]
            ),
            "**Сгенерировано:** ProjectDigest (эвристика, без LLM).",
        ]

        if data["agents"]:
            lines.append("\n## Сессии по агентам")
            for agent_id in sorted(data["agents"].keys()):
                rows = data["agents"][agent_id]
                lines.append(f"\n### {agent_id} (сессий: {len(rows)})")
                for row in rows[:MAX_SESSIONS_PER_AGENT]:
                    lines.append(f"- **{row['timestamp']}**")
                    if row["done"]:
                        lines.append(f"    - Сделано: {row['done']}")
                    if row["decisions"]:
                        lines.append(f"    - Решения: {row['decisions']}")
                    if row["next"]:
                        lines.append(f"    - Дальше: {row['next']}")
                    if row["other"]:
                        lines.append(f"    - Прочее: {row['other']}")

        if data["decisions"]:
            lines.append(f"\n## Совместные решения ({len(data['decisions'])})")
            for dec in data["decisions"]:
                when = (dec.get("recorded_at") or "?")[:10]
                by = ", ".join(dec.get("by_agents") or ["?"])
                lines.append(f"- {when} **{_truncate(dec.get('decision') or '')}** — агенты: {by}")
                if dec.get("reason"):
                    lines.append(f"    - Причина: {_truncate(dec.get('reason') or '', 200)}")

        if data["chunks"]:
            lines.append(f"\n## Свежие заметки проекта ({len(data['chunks'])})")
            for chunk in data["chunks"]:
                when = (chunk.get("created_at") or "")[:10]
                who = f" ({chunk['agent_id']})" if chunk.get("agent_id") else ""
                lines.append(f"- {when}{who}: {chunk['preview']}")

        return "\n".join(lines)