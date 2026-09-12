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
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.nexus_core import Nexus
from core.config import config

# --- Tunables (overridable via nexus_config.json → summarizer.*) ---
MAX_ITEMS_PER_BUCKET = config.summarizer.get("max_items_per_bucket", 10)
MAX_ITEM_CHARS = config.summarizer.get("max_item_chars", 300)
MAX_DIALOG_MESSAGES = config.summarizer.get("max_dialog_messages", 5)

# --- Tunables (overridable via nexus_config.json → compressor.*) ---
# Level 1 = heuristic (local, no LLM); level 2 = optional LLM upgrade.
COMPRESSOR_DEFAULTS = {
    "default_level": 1,
    "max_done": 8,
    "max_decisions": 6,
    "max_next": 6,
    "max_other": 4,
    "max_dialog_messages": 6,
    "max_dialog_chars": 220,
    "max_chars": 2400,
}
_compressor_cfg = getattr(config, "compressor", None) or {}
COMPRESSOR_CFG = {**COMPRESSOR_DEFAULTS, **{k: v for k, v in _compressor_cfg.items()}}

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

        # Restore pointer + mark the archive as summarized — one locked
        # read-modify-write so concurrent agents never lose entries (Phase 1).
        def _mutate_index(index: Any) -> Any:
            index = index or {}
            if agent_id in index:
                if prior_pointer:
                    index[agent_id]["last_session_summary"] = prior_pointer
            for entry in (index.get(agent_id) or {}).get("history", []):
                if entry.get("file") == relative:
                    entry["status"] = "summarized"
                    entry["summary"] = summary_path
            return index

        self.nm._update_json(self.nm.sessions_index_file, _mutate_index, empty={})

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

        def _mutate_index(index: Any) -> Any:
            index = index or {}
            if agent_id in index:
                if prior_pointer:
                    index[agent_id]["last_session_summary"] = prior_pointer
                index[agent_id]["collapsed_summary"] = collapsed_path
                collapsed_files = {str(e.get("file")) for e in to_collapse}
                for entry in index[agent_id].get("history", []):
                    if entry.get("file") in collapsed_files:
                        entry["status"] = "collapsed"
            return index

        self.nm._update_json(self.nm.sessions_index_file, _mutate_index, empty={})

        return {
            "status": "ok",
            "agent_id": agent_id,
            "collapsed": len(to_collapse),
            "from": first_ts,
            "to": last_ts,
            "summary_file": collapsed_path,
        }

    # --- Compression / compacting long sessions (roadmap chunk c7464edb) ---

    def compress_session(
        self,
        raw_log: Any = None,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        level: int = 1,
        max_chars: Optional[int] = None,
        llm_call: Optional[Callable[[str], str]] = None,
    ) -> Dict[str, Any]:
        """Compress a raw session log into a compact, reusable structure.

        Goal (Nexus roadmap chunk ``c7464edb``): graceful loss of DETAILS
        while keeping all FACTS / DECISIONS / TASKS, so the compacted block
        can be pasted into ``get_context()`` and the next "step" does not
        lose the thread.

        Sources (mutually exclusive):
          - ``raw_log``: any JSON-serializable session dict to compress
            directly (usable outside Nexus — e.g. NeoKron / Мнемозина logs).
          - ``agent_id``: compress the latest archived session (or the one
            given by ``session_id``).

        Levels:
          1 — heuristic (default, local, no LLM): reuses ``extract_structure``.
          2 — optional LLM upgrade: calls ``llm_call(prompt) -> str`` and
              stores the free-form summary under ``llm``. Falls back to
              level 1 when no callable is provided (with a note).

        Returns a dict: status, level, source, timestamp, compact
        (buckets + dialog), markdown (get_context-ready block), llm, stats.
        """
        level = 2 if int(level or 1) >= 2 else 1
        max_chars = max_chars or COMPRESSOR_CFG["max_chars"]

        ts = datetime.now().strftime("%Y%m%d_%H%M")
        source = "raw_log"

        if raw_log is None:
            if not agent_id:
                return {"status": "invalid_input",
                        "message": "raw_log или agent_id обязателен."}
            archive_path, archive_ts = self._find_archive_path(agent_id, session_id)
            if archive_path is None:
                return {"status": "not_found", "agent_id": agent_id,
                        "message": "Архив сессии не найден (сначала archive_session)."}
            with open(archive_path, "r", encoding="utf-8") as f:
                archive = json.load(f)
            raw_log = archive.get("session_data")
            ts = archive.get("timestamp") or archive_ts or ts
            source = f"archive:{agent_id}:{ts}"
            if raw_log is None:
                raw_log = archive  # tolerate archives without session_data

        buckets = self.extract_structure(raw_log)
        compact: Dict[str, Any] = {
            "agent_id": agent_id,
            "timestamp": ts,
            "done": (buckets.get("done") or [])[: COMPRESSOR_CFG["max_done"]],
            "decisions": (buckets.get("decisions") or [])[: COMPRESSOR_CFG["max_decisions"]],
            "next": (buckets.get("next") or [])[: COMPRESSOR_CFG["max_next"]],
            "other": (buckets.get("other") or [])[: COMPRESSOR_CFG["max_other"]],
            "dialog": (buckets.get("dialog") or [])[: COMPRESSOR_CFG["max_dialog_messages"]],
        }

        llm: Optional[Dict[str, Any]] = None
        level_used = level
        if level >= 2:
            if llm_call is None:
                level_used = 1
                llm = {"status": "skipped",
                       "message": "level=2 запрошен, но llm_call не передан — "
                                  "использована эвристика (level 1)."}
            else:
                try:
                    text = llm_call(self._build_llm_prompt(compact))
                    llm = {"status": "ok", "summary": (text or "").strip()}
                except Exception as e:  # LLM не должен ронять сжатие
                    level_used = 1
                    llm = {"status": "error", "error": str(e),
                           "message": "LLM-апгрейд не удался — использована эвристика."}

        md = self._render_compact_markdown(compact, level=level_used)
        md = self._fit_compact_markdown(compact, max_chars, level_used, md=md)

        in_chars = len(json.dumps(raw_log, ensure_ascii=False))
        out_chars = len(md)
        ratio = round(in_chars / out_chars, 1) if out_chars else 0.0

        return {
            "status": "ok",
            "level": level_used,
            "source": source,
            "timestamp": ts,
            "compact": compact,
            "llm": llm,
            "markdown": md,
            "stats": {"in_chars": in_chars, "out_chars": out_chars,
                      "ratio": ratio},
        }

    @staticmethod
    def _build_llm_prompt(compact: Dict[str, Any]) -> str:
        """Build a prompt for the optional LLM upgrade (level 2)."""
        sections = [
            ("Сделано", compact.get("done", [])),
            ("Решения", compact.get("decisions", [])),
            ("Дальше", compact.get("next", [])),
            ("Прочее", compact.get("other", [])),
        ]
        lines = [
            "Сожми сессию агента в компактную сводку на русском языке.",
            "Сохрани ВСЕ факты, решения и оставшиеся задачи. Убери повторы",
            "и несущественные детали. Формат ответа:",
            "СДЕЛАНО: ...\nРЕШЕНИЯ: ...\nДАЛЬШЕ: ...",
            "",
            "Исходные данные:",
        ]
        for title, items in sections:
            if items:
                lines.append(f"\n{title}: " + "; ".join(items))
        return "\n".join(lines)

    def _render_compact_markdown(
        self, compact: Dict[str, Any], level: int = 1
    ) -> str:
        """Render the compact structure as a get_context-ready markdown."""
        sections = [
            ("Сделано", compact.get("done", [])),
            ("Решения", compact.get("decisions", [])),
            ("Дальше", compact.get("next", [])),
            ("Прочее", compact.get("other", [])),
            ("Диалог", compact.get("dialog", [])),
        ]
        who = f" ({compact.get('agent_id')})" if compact.get("agent_id") else ""
        lines = [f"# Сжатая сессия{who}",
                 f"**Время:** {compact.get('timestamp', '?')} · "
                 f"**Уровень сжатия:** {level}"]
        added = False
        for title, items in sections:
            if not items:
                continue
            added = True
            lines.append(f"\n## {title}")
            for item in items:
                lines.append(f"- {_truncate(item, MAX_ITEM_CHARS)}")
        if not added:
            lines.append("\nСессия не содержит структурированных данных.")
        return "\n".join(lines)

    def _fit_compact_markdown(
        self,
        compact: Dict[str, Any],
        max_chars: int,
        level: int,
        md: Optional[str] = None,
    ) -> str:
        """Shrink the compact block until it fits the char budget.

        Order of sacrifice: dialog first (least important context), then
        per-item truncation, then a hard tail truncation as last resort.
        """
        if max_chars <= 0:
            return md if md is not None else self._render_compact_markdown(compact, level)
        if md is None:
            md = self._render_compact_markdown(compact, level)
        if len(md) <= max_chars:
            return md
        slim = dict(compact)
        slim["dialog"] = []
        md = self._render_compact_markdown(slim, level)
        if len(md) <= max_chars:
            return md
        limit = max(40, int(MAX_ITEM_CHARS * 0.6))
        for key in ("done", "decisions", "next", "other"):
            slim[key] = [_truncate(i, limit) for i in slim.get(key, [])]
        md = self._render_compact_markdown(slim, level)
        if len(md) > max_chars:
            md = md[: max_chars - 1].rstrip() + "…"
        return md


def compress_session(
    raw_log: Any = None,
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    level: int = 1,
    max_chars: Optional[int] = None,
    llm_call: Optional[Callable[[str], str]] = None,
    nexus: Optional[Nexus] = None,
) -> Dict[str, Any]:
    """Standalone convenience wrapper for ``SessionSummarizer.compress_session``.

    Usable outside Nexus (NeoKron, Мнемозина): pass any JSON-serializable
    log as ``raw_log`` — no store is touched.

        from core.summarizer import compress_session
        result = compress_session({"actions": [...], "decisions": [...]})
        print(result["markdown"])
    """
    ss = SessionSummarizer(nexus or Nexus())
    return ss.compress_session(
        raw_log=raw_log,
        agent_id=agent_id,
        session_id=session_id,
        level=level,
        max_chars=max_chars,
        llm_call=llm_call,
    )


