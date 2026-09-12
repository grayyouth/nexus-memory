"""
Tests for core.autoclose (Phase 2 automation: closing stale sessions).

The `now` seam lets tests fast-forward time without sleeping: passing
`now = datetime.now() + 10h` makes every freshly archived session stale.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.autoclose import auto_close_stale_sessions
from core.summarizer import SessionSummarizer


def test_autoclose_empty_store(nm):
    report = auto_close_stale_sessions(nm, timeout_min=60)
    assert report["status"] == "ok"
    assert report["closed"] == 0
    assert report["details"] == []


def test_autoclose_closes_stale_session(nm):
    nm.archive_session("ghost", {"chat_history": ["hello", "world"]})

    # 10 hours later the archive is older than timeout_min and still
    # has no summary -> must be closed (backfilled).
    future_now = datetime.now() + timedelta(hours=10)
    report = auto_close_stale_sessions(nm, timeout_min=360, now=future_now)

    assert report["closed"] == 1
    detail = report["details"][0]
    assert detail["agent_id"] == "ghost"

    index = nm._read_json(nm.sessions_index_file)
    entry = index["ghost"]["history"][0]
    assert detail["session_id"] == entry["timestamp"]
    assert entry["status"] == "summarized"
    # the summary file was actually written
    assert (nm.base_dir / entry["summary"]).exists()


def test_autoclose_skips_fresh_session(nm):
    nm.archive_session("fresh", {"chat_history": ["hi"]})
    report = auto_close_stale_sessions(nm, timeout_min=360)
    assert report["closed"] == 0
    entry = nm._read_json(nm.sessions_index_file)["fresh"]["history"][0]
    assert entry["status"] == "archived"


def test_autoclose_skips_already_summarized(nm):
    nm.archive_session("diligent", {"chat_history": ["hi"]})
    res = SessionSummarizer(nm).generate_session_summary("diligent")
    assert res["status"] == "ok"

    future_now = datetime.now() + timedelta(hours=10)
    report = auto_close_stale_sessions(nm, timeout_min=360, now=future_now)
    assert report["closed"] == 0


def test_autoclose_multiple_agents(nm):
    for agent in ("a1", "a2", "a3"):
        nm.archive_session(agent, {"chat_history": [f"msg {agent}"]})
    future_now = datetime.now() + timedelta(hours=10)
    report = auto_close_stale_sessions(nm, timeout_min=360, now=future_now)
    assert report["closed"] == 3
    assert report["errors"] == []
    agents = {d["agent_id"] for d in report["details"]}
    assert agents == {"a1", "a2", "a3"}


def test_autoclose_only_closes_older_than_timeout(nm):
    nm.archive_session("young", {"chat_history": ["m"]})
    # 5 minutes later with a 1-hour timeout: too fresh, nothing to close
    slightly_later = datetime.now() + timedelta(minutes=5)
    report = auto_close_stale_sessions(nm, timeout_min=60, now=slightly_later)
    assert report["closed"] == 0