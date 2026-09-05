"""
Nexus Session Summarizer (Stage 3 of the roadmap).

Turns archived session data into structured summaries automatically, without
requiring an LLM call:

- `generate_session_summary(agent_id)` — reads the latest (or a specific)
  archived session and produces a compact summary in the *Сделано / Решения /
  Дальше* format, then stores it via Nexus.store_session_summary().
- `collapse_history(agent_id, keep_last=1)` — folds old archives into a single
  retrospective summary (saves space and tokens for small-context local LLMs).

The extractor is heuristic: it recognizes common keys agents use when
archiving (English and Russian), works with any JSON-serializable data, and
truncates aggressively so summaries stay small (the target consumers are
local LLMs with tiny contexts).

Usage:
    from core.nexus_core import Nexus
    from core.summarizer import SessionSummarizer
    s = SessionSummarizer(Nexus())
    info = s.generate_session_summary("cline")
    print(info)
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.nexus_core import Nexus
from core.config import config

# --- Tunables (overridable via nexus_config.json → summarizer.*) ---
MAX_ITEMS_PER_BUCKET = config.summarizer.get("max_items_per_bucket", 10)
MAX_ITEM_CHARS = config.summarizer.get("max_item_chars", 300)
MAX_DIALOG_MESSAGES = config.summarizer.get("max_dialog_messages", 5)

# Session-data key -> summary bucket (English + Russian keys).
DIALOG_KEYS = {"chat_history", "messages", "history", "dialog", "диалог"}
DONE_KEYS = {"actions", "done", "completed", "what_was_done", "facts",
             "сделано", "действия", "шаги"}
DECISION_KEYS = {"decisions", "decision", "решения", "решение"}
NEXT_KEYS = {"next_steps", "todo", "followups", "follow_up", "plans",
             "what_next", "дальше", "планы", "следующие_шаги"}

# Keys inside list items that are likely to hold the item's text.
CONTENT_KEYS = ("content", "text", "decision", "summary", "note", "message",
                "item", "desc", "description", "result", "value", "текст")


def _norm_key(key: str) -> str:
    """Normalize a dict key: snake_case, lowercase, strip non-letters."""
    return re.sub(r"[^a-zа-я_]", "", str(key).lower().strip().replace(" ", "_"))


def _truncate(text: str, limit: int = MAX_ITEM_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _items_from_value(value: Any) -> List[str]:
    """Extract a flat list of readable text items from an arbitrary value."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for element in value:
            out.extend(_items_from_value(element))
        return out
    if isinstance(value, dict):
        # Prefer a well-known content key (e.g. {"role": "user", "content": "..."})
        text_part = None
        for key in CONTENT_KEYS:
            if key in value and isinstance(value[key], str) and value[key].strip():
                text_part = value[key].strip()
                break
        if text_part is not None:
            # Include role/author as a prefix when present (dialog readability).
            role = value.get("role") or value.get("author") or value.get("agent")
            if isinstance(role, str) and role.strip():
                return [f"[{role.strip()}] {text_part}"]
            return [text_part]
        # Generic dict: join its scalar fields.
        parts = [
            f"{k}: {v}"
            for k, v in value.items()
            if isinstance(v, (str, int, float, bool)) and str(v).strip()
        ]
        return ["; ".join(parts)] if parts else []
    return [str(value)]


class SessionSummarizer:
    """Generates session summaries from archives stored in the Nexus store."""

    def __init__(self, nexus: Nexus):
        self.nm = nexus

    # --- Listing / loading archives ---

    def list_archives(self, agent_id: str) -> List[Dict]:
        """All archived sessions of an agent, oldest first (index-based)."""
        index = self.nm._read_json(self.nm.sessions_index_file) or {}
        history = (index.get(agent_id) or {}).get("history") or []
        entries = [dict(e) for e in history if isinstance(e, dict)]
        entries.sort(key=lambda e: str(e.get("timestamp", "")))
        return entries

    def _find_archive_path(
        self, agent_id: str, session_id: Optional[str] = None
    ) -> Tuple[Optional[Path], Optional[str]]:
        """Resolve an archive file path: explicit session_id, else the newest."""
        archives_dir = self.nm.sessions_archived_dir / f"agent_{agent_id}"
        if session_id:
            candidate = archives_dir / f"session_{session_id}.json"
            return (candidate if candidate.exists() else None), session_id
        # Prefer the index (has statuses), fall back to a directory scan.
        candidates = [a for a in self.list_archives(agent_id) if a.get("status") != "collapsed"]
        if candidates:
            last = candidates[-1]
            path = self.nm.base_dir / str(last.get("file", ""))
            return (path if path.exists() else None), last.get("timestamp")
        if archives_dir.exists():
            files = sorted(archives_dir.glob("session_*.json"))
            if files:
                return files[-1], files[-1].stem.replace("session_", "")
        return None, None

    # --- Extraction ---

    def extract_structure(self, session_data: Any) -> Dict[str, List[str]]:
        """
        Map raw session_data onto summary buckets:
        done / decisions / next / dialog / other.
        Tolerant to any JSON-serializable input.
        """
        buckets: Dict[str, List[str]] = {
            "done": [], "decisions": [], "next": [], "dialog": [], "other": [],
        }
        limits = {
            "done": MAX_ITEMS_PER_BUCKET, "decisions": MAX_ITEMS_PER_BUCKET,
            "next": MAX_ITEMS_PER_BUCKET, "dialog": MAX_DIALOG_MESSAGES,
            "other": MAX_ITEMS_PER_BUCKET,
        }

        def add(bucket: str, items: List[str]) -> None:
            for item in items:
                if len(buckets[bucket]) >= limits[bucket]:
                    if buckets[bucket]:
                        buckets[bucket][-1] = _truncate(
                            buckets[bucket][-1] + " (обрезано: ещё есть данные в архиве)"
                        )
                    return
                clean = _truncate(item)
                if clean:
                    buckets[bucket].append(clean)

        if isinstance(session_data, str):
            add("done", [session_data])
            return buckets
        if isinstance(session_data, (list, tuple)):
            add("done", _items_from_value(session_data))
            return buckets

        for raw_key, value in (session_data or {}).items():
            key = _norm_key(raw_key)
            if key in DIALOG_KEYS:
                add("dialog", _items_from_value(value))
            elif key in DONE_KEYS:
                add("done", _items_from_value(value))
            elif key in DECISION_KEYS:
                add("decisions", _items_from_value(value))
            elif key in NEXT_KEYS:
                add("next", _items_from_value(value))
            else:
                # Unknown key: keep it visible, formatted as "key: value".
                for item in _items_from_value(value):
                    add("other", [f"{raw_key}: {item}"])

        return buckets

    def _build_summary_markdown(
        self, agent_id: str, timestamp: str, session_data: Any, archive_file: str
    ) -> str:
        """Render buckets into a compact *Сделано / Решения / Дальше* summary."""
        buckets = self.extract_structure(session_data)

        lines = [
            f"# Автосводка сессии ({agent_id})",
            f"**Сессия:** {timestamp} · **Источник:** {archive_file}",
            "**Сгенерировано:** SessionSummarizer (эвристика, без LLM)",
        ]

        sections = [
            ("Сделано", buckets["done"]),
            ("Решения", buckets["decisions"]),
            ("Дальше", buckets["next"]),
            ("Диалог (конец сессии)", buckets["dialog"]),
            ("Прочее", buckets["other"]),
        ]
        for title, items in sections:
            if not items:
                continue
            lines.append(f"\n## {title}")
            for item in items:
                lines.append(f"- {item}")

        if not any(items for _, items in sections):
            lines.append("\nСессия не содержит структурированных данных (архив пуст).")

        return "\n".join(lines)

    # --- Public operations ---

    def generate_session_summary(
        self, agent_id: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Read an archived session and store an auto-generated summary.

        Without session_id the newest not-yet-collapsed archive is used.
        Marks the archive entry as "summarized" in the sessions index.
        """
        archive_path, ts = self._find_archive_path(agent_id, session_id)
        if archive_path is None:
            return {"status": "not_found", "agent_id": agent_id,
                    "message": "Архив сессии не найден (сначала archive_session)."}

        with open(archive_path, "r", encoding="utf-8") as f:
            archive = json.load(f)
        session_data = archive.get("session_data")
        if session_id is None:
            ts = archive.get("timestamp") or ts

        relative = str(archive_path.relative_to(self.nm.base_dir))
        summary_md = self._build_summary_markdown(agent_id, ts or "?", session_data, relative)
        # Backfill semantics: summarizing an EXPLICIT old session must not
        # move the "latest session" pointer used by get_context().
        prior_pointer = None
        if session_id is not None:
            index = self.nm._read_json(self.nm.sessions_index_file) or {}
            if agent_id in index:
                prior_pointer = index[agent_id].get("last_session_summary")
        summary_path = self.nm.store_session_summary(agent_id, summary_md, session_id=ts)
        if prior_pointer:
            index = self.nm._read_json(self.nm.sessions_index_file) or {}
            index[agent_id]["last_session_summary"] = prior_pointer
            self.nm._write_json(self.nm.sessions_index_file, index)

        # Mark the archive as summarized in the index.
        index = self.nm._read_json(self.nm.sessions_index_file) or {}
        for entry in (index.get(agent_id) or {}).get("history", []):
            if entry.get("file") == relative:
                entry["status"] = "summarized"
                entry["summary"] = summary_path
        self.nm._write_json(self.nm.sessions_index_file, index)

        buckets = self.extract_structure(session_data)
        stats = {k: len(v) for k, v in buckets.items()}
        return {
            "status": "ok",
            "agent_id": agent_id,
            "archive_file": relative,
            "summary_file": summary_path,
            "stats": stats,
        }

    def collapse_history(self, agent_id: str, keep_last: int = 1) -> Dict[str, Any]:
        """
        Fold all but the newest `keep_last` archives into ONE retrospective
        summary ("collapsed"). Each old session becomes one line: its
        timestamp, first "done" item and counts. The collapsed summary is
        stored as a separate index field (`collapsed_summary`) so the latest
        session summary keeps serving get_context().
        """
        archives = self.list_archives(agent_id)
        usable = [a for a in archives if a.get("status") != "collapsed"]
        if len(usable) <= keep_last:
            return {
                "status": "nothing_to_collapse",
                "agent_id": agent_id,
                "archives": len(usable),
                "message": f"Архивов меньше или равно keep_last={keep_last}.",
            }

        to_collapse = usable[:-keep_last] if keep_last > 0 else usable

        session_lines: List[str] = []
        all_decisions: List[str] = []
        all_next: List[str] = []
        first_ts = to_collapse[0].get("timestamp")
        last_ts = to_collapse[-1].get("timestamp")

        for entry in to_collapse:
            ts = entry.get("timestamp") or "?"
            file_rel = str(entry.get("file") or "")
            path = self.nm.base_dir / file_rel
            try:
                with open(path, "r", encoding="utf-8") as f:
                    archive = json.load(f)
                buckets = self.extract_structure(archive.get("session_data"))
            except Exception as e:
                session_lines.append(f"- {ts}: [архив не прочитан: {e}]")
                continue

            first_done = buckets["done"][0] if buckets["done"] else "—"
            counts = ", ".join(f"{k}={len(v)}" for k, v in buckets.items() if v)
            session_lines.append(
                f"- **{ts}** — {first_done}"
                + (f" · ({counts})" if counts else "")
            )
            for d in buckets["decisions"]:
                if d not in all_decisions:
                    all_decisions.append(d)
            for n in buckets["next"]:
                if n not in all_next:
                    all_next.append(n)

        summary_md = "\n".join([
            f"# Свернутое резюме истории сессий ({agent_id})",
            f"**Сессии:** {first_ts} … {last_ts} · **Свернуто:** {len(to_collapse)}",
            "**Сгенерировано:** SessionSummarizer.collapse_history (эвристика).",
            "\n## Сессии (по одной строке)",
            *session_lines,
            f"\n## Решения (агрегировано: {len(all_decisions)})",
            *[f"- {d}" for d in all_decisions[:MAX_ITEMS_PER_BUCKET]],
            f"\n## Дальше (агрегировано: {len(all_next)})",
            *[f"- {n}" for n in all_next[:MAX_ITEMS_PER_BUCKET]],
        ])

        # Store, but keep the latest-session pointer untouched: collapsed
        # history is stored as `collapsed_summary`, not as the current context.
        prior_pointer = None
        index = self.nm._read_json(self.nm.sessions_index_file) or {}
        if agent_id in index:
            prior_pointer = index[agent_id].get("last_session_summary")
        collapsed_id = f"{first_ts}_{last_ts}_collapsed"
        collapsed_path = self.nm.store_session_summary(agent_id, summary_md, session_id=collapsed_id)

        index = self.nm._read_json(self.nm.sessions_index_file) or {}
        if agent_id in index:
            if prior_pointer:
                index[agent_id]["last_session_summary"] = prior_pointer
            index[agent_id]["collapsed_summary"] = collapsed_path
            collapsed_files = {str(e.get("file")) for e in to_collapse}
            for entry in index[agent_id].get("history", []):
                if entry.get("file") in collapsed_files:
                    entry["status"] = "collapsed"
        self.nm._write_json(self.nm.sessions_index_file, index)

        return {
            "status": "ok",
            "agent_id": agent_id,
            "collapsed": len(to_collapse),
            "from": first_ts,
            "to": last_ts,
            "summary_file": collapsed_path,
        }


