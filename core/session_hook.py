"""
Nexus Session Hook (tail of Stage 3 of the roadmap).

One-call wrapper for the "end of session → save" ritual:

- `SessionHook.end_session(agent_id, session_data, collapse_after=False, keep_last=1)`
  archives the raw session data, auto-generates its structured summary
  (SessionSummarizer) and optionally collapses older archives into a
  retrospective summary — instead of calling archive_session +
  generate_session_summary (+ collapse_history) one by one.

The hook is intentionally a thin orchestrator: it composes existing Nexus
primitives and returns a structured report with file paths and stats.

Usage:
    from core.nexus_core import Nexus
    from core.session_hook import SessionHook
    hook = SessionHook(Nexus())
    info = hook.end_session("cline", {"actions": [...], "decisions": [...]})
    print(info["summary_file"])
"""

from typing import Any, Dict, Optional

from core.nexus_core import Nexus
from core.summarizer import SessionSummarizer


class SessionHook:
    """End-of-session saver: archive + auto-summary (+ optional collapse)."""

    def __init__(self, nexus: Nexus):
        self.nm = nexus

    def end_session(
        self,
        agent_id: str,
        session_data: Any,
        project_id: Optional[str] = None,
        collapse_after: bool = False,
        keep_last: int = 1,
    ) -> Dict[str, Any]:
        """
        Finish a session for `agent_id` in one call:

        1. Archive the raw `session_data` (Nexus.archive_session).
        2. Auto-generate and store its summary (SessionSummarizer).
        3. Optionally collapse archives older than `keep_last` into one
           retrospective summary (SessionSummarizer.collapse_history).

        `project_id` (optional) tags the archive with a project so the
        cross-agent project digest (ProjectDigest) can attribute it.

        Returns a structured report dict; `status` is one of:
        "ok" | "invalid_input".
        """
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return {"status": "invalid_input",
                    "message": "agent_id обязателен."}
        if not session_data or (
            isinstance(session_data, str) and not session_data.strip()
        ):
            return {"status": "invalid_input", "agent_id": agent_id,
                    "message": "session_data пуст — нечего сохранять."}

        summarizer = SessionSummarizer(self.nm)

        # 1) Raw archive (unique file name even within the same minute).
        archive_file = self.nm.archive_session(
            agent_id=agent_id, session_data=session_data, project_id=project_id
        )

        # 2) Auto-summary of the archive we just created (the newest one).
        info = summarizer.generate_session_summary(agent_id)
        summary_file = info.get("summary_file")
        stats = info.get("stats", {})

        # 3) Optional collapse of old archives.
        collapsed: Dict[str, Any] = None
        if collapse_after:
            coll = summarizer.collapse_history(agent_id, keep_last=keep_last)
            collapsed = coll if coll.get("status") == "ok" else {
                "status": coll.get("status"),
                "message": coll.get("message", ""),
            }

        return {
            "status": "ok",
            "agent_id": agent_id,
            "archive_file": archive_file,
            "summary_status": info.get("status"),
            "summary_file": summary_file,
            "stats": stats,
            "collapsed": collapsed,
            "message": "Сессия сохранена: архив + автосводка"
                       + (" + свернута история" if collapsed else ""),
        }
