"""
Nexus Auto-Close of stale sessions (автоматизация, пункт 2 роадмапа).

Проблема: агент заархивировал сессию (archive_session / end_session), но не
довёл ритуал до конца — не сделал сводку. Если потом агент «исчез» (долго
нет активности), проектный дайджест остаётся неполным.

Решение (без дисциплины агента): демон периодически вызывает
``auto_close_stale_sessions`` — находит у каждого агента самый свежий
не-суммаризированный архив старше ``timeout_min`` и проставляет ему сводку
через ``SessionSummarizer.generate_session_summary(session_id=...)``.
Сводки остаются «бэкфиллом»: указатель ``last_session_summary`` не трогается.

Usage:
    from core.autoclose import auto_close_stale_sessions
    report = auto_close_stale_sessions(nm, timeout_min=360)
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.nexus_core import Nexus
from core.summarizer import SessionSummarizer
from core.project_digest import parse_time

logger = logging.getLogger(__name__)


def _stale_entries(nm: Nexus, timeout_min: int, now: Optional[datetime] = None):
    """Yield (agent_id, history_entry) for stale not-yet-summarized archives.

    An archive is stale when its own timestamp is older than ``timeout_min``
    AND its index entry still has ``status == "archived"`` (no summary done).
    """
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=max(1, int(timeout_min)))
    index = nm._read_json(nm.sessions_index_file) or {}
    for agent_id, info in index.items():
        history = (info or {}).get("history") or []
        for entry in history:
            if entry.get("status") != "archived":
                continue
            ts = parse_time(entry.get("timestamp"))
            if ts is None or ts < cutoff:
                yield agent_id, entry


def auto_close_stale_sessions(
    nexus: Optional[Nexus] = None,
    timeout_min: int = 360,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Close (summarize) stale sessions for ALL agents.

    Args:
        nexus: Nexus instance (or None → default store).
        timeout_min: How old an uns-summarized archive must be to close.
        now: Test seam — override the "current" time.

    Returns a report dict with per-agent details.
    """
    nm = nexus or Nexus()
    summarizer = SessionSummarizer(nm)
    stale = list(_stale_entries(nm, timeout_min, now=now))
    if not stale:
        return {"status": "ok", "closed": 0, "details": []}

    closed: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for agent_id, entry in stale:
        session_id = entry.get("timestamp")
        try:
            info = summarizer.generate_session_summary(agent_id, session_id=session_id)
            if info.get("status") == "ok":
                closed.append({
                    "agent_id": agent_id,
                    "session_id": session_id,
                    "summary_file": info.get("summary_file"),
                })
            else:
                errors.append({"agent_id": agent_id, "session_id": session_id,
                               "message": info.get("message", info.get("status"))})
        except Exception as e:  # одна сессия не должна ронять весь цикл
            logger.exception("Autoclose failed for %s %s", agent_id, session_id)
            errors.append({"agent_id": agent_id, "session_id": session_id,
                           "message": repr(e)})

    return {
        "status": "ok",
        "checked": len(stale),
        "closed": len(closed),
        "errors": errors,
        "details": closed,
        "message": f"Авто-закрыто {len(closed)} сессий"
                   + (f", ошибок {len(errors)}" if errors else ""),
    }