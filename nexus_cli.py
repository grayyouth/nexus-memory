#!/usr/bin/env python3
"""
Nexus CLI — командная строка для работы с Nexus памятью.

Использование:
    python nexus_cli.py add "текст" --tags "#bug" --project "TinyVika"
    python nexus_cli.py search "как настроить Flet"
    python nexus_cli.py ingest --max 10
    python nexus_cli.py context --agent cline
    python nexus_cli.py summary --agent cline
    python nexus_cli.py decisions --project "TinyVika"
    python nexus_cli.py search --semantic "векторный поиск"
    python nexus_cli.py context-prompt --agent cline --query "как работать с Flet"
    python nexus_cli.py status                      # общий статус стора + демона
    python nexus_cli.py server start                # запустить HTTP-демон (Фаза 2)
    python nexus_cli.py server stop                 # остановить демон
    python nexus_cli.py server status               # статус демона
    python nexus_cli.py server autostart enable     # автозапуск демона при входе в систему

Запуск из корня проекта:
    python nexus_cli.py <command> [options]
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# Force UTF-8 encoding for Windows console (only when run as main script)
if sys.platform == "win32" and __name__ == "__main__":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    if hasattr(sys.stdout, "buffer"):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        import io
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.nexus_core import Nexus
from core.ingestion import IngestionPipeline
from core.summarizer import SessionSummarizer
from core.semantic import SemanticSearch
from core.watchkeeper import Watchkeeper
from core.config import config as nexus_config
from core.web import fetch_web_page, save_to_raw, get_web_status
from core.orchestrator import Orchestrator, get_orchestrator_status, list_orchestrator_tasks


def cmd_add(args: argparse.Namespace) -> None:
    """Add a knowledge chunk to the Nexus library."""
    nm = Nexus(base_dir=args.store)
    tags = args.tags if args.tags else ["#cli"]
    agent_id = args.agent or "cli"
    
    chunk_id = nm.add_chunk(
        content=args.content,
        tags=tags,
        project_id=args.project,
        source=args.source,
        agent_id=agent_id,
    )
    
    print(f"Added chunk: {chunk_id[:8]}")
    print(f"  Tags: {', '.join(tags)}")
    if args.project:
        print(f"  Project: {args.project}")
    if args.source:
        print(f"  Source: {args.source}")


def cmd_search(args: argparse.Namespace) -> None:
    """Search the knowledge base."""
    nm = Nexus(base_dir=args.store)
    
    if args.semantic:
        # Semantic search
        sem = SemanticSearch(nm)
        results = sem.search(
            query=args.query,
            top_k=args.top_k,
            tags=args.tags,
            project_id=args.project,
            agent_id=args.agent,
        )
        if not results:
            print("No semantic matches found.")
            return
        
        print(f"Semantic results for «{args.query}» (top {len(results)}):")
        for res in results:
            marker = " [keyword]" if res.get("keyword_hit") else ""
            print(f"  score={res['score']:.3f}{marker} | {res['id'][:8]}")
            print(f"    project: {res.get('project_id') or 'general'}")
            print(f"    tags: {', '.join(res.get('tags', [])) or '-'}")
            # Preview
            from core.semantic import _read_chunk_body
            body = _read_chunk_body(nm.base_dir / res['source_file']).replace("\n", " ")
            print(f"    {body[:200]}...")
    else:
        # Keyword search
        results = nm.search(
            query=args.query,
            tags=args.tags,
            project_id=args.project,
            agent_id=args.agent,
        )
        if not results:
            print("No results found.")
            return
        
        print(f"Found {len(results)} result(s):")
        for res in results:
            print(f"  ID: {res['id'][:8]} | Tags: {', '.join(res.get('tags', []))}")
            if res.get('project_id'):
                print(f"    Project: {res['project_id']}")


def cmd_ingest(args: argparse.Namespace) -> None:
    """Run the ingestion pipeline."""
    nm = Nexus(base_dir=args.store)
    pipeline = IngestionPipeline(nm)
    report = pipeline.run()
    
    print(f"Scanned: {report['scanned']} file(s)")
    print(f"Ingested: {report['ingested']}")
    print(f"Skipped: {report['skipped']}")
    print(f"Errors: {report['errors']}")
    
    for item in report.get("files", []):
        status = item.get("status", "?")
        path = item.get("path", "")
        chunks = item.get("chunks", 0)
        if status == "ingested":
            print(f"  ✓ {path}: {chunks} chunks")
        elif status == "skipped":
            reason = item.get("reason", "")
            print(f"  - {path}: skipped ({reason})")
        elif status == "error":
            reason = item.get("reason", "")
            print(f"  ✗ {path}: error ({reason})")


def cmd_context(args: argparse.Namespace) -> None:
    """Get context for an agent."""
    nm = Nexus(base_dir=args.store)
    context = nm.get_context_for_agent(
        agent_id=args.agent,
        project_id=args.project,
    )
    
    if not context:
        print("No prior context found.")
        return
    
    print(f"Context for agent '{args.agent}':")
    print("---")
    print(context)
    print("---")


def cmd_context_prompt(args: argparse.Namespace) -> None:
    """Get a compact context prompt block for local LLMs."""
    nm = Nexus(base_dir=args.store)
    sem = SemanticSearch(nm)
    
    info = sem.build_prompt_block(
        query=args.query,
        project_id=args.project,
        agent_id=args.agent,
        max_chunks=args.max_chunks,
        max_chars=args.max_chars,
    )
    
    print(f"[block: {info['chars']} chars, {len(info['chunks'])} chunk(s)]")
    print("---")
    print(info["text"])
    print("---")


def cmd_digest(args: argparse.Namespace) -> None:
    """Project digest: cross-agent activity overview."""
    from core.project_digest import ProjectDigest

    nm = Nexus(base_dir=args.store)
    since = args.since
    if args.days is not None and not since:
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(days=args.days)).isoformat()

    block = ProjectDigest(nm).get_digest(
        project_id=args.project,
        since=since,
        exclude_agent=args.exclude_agent,
        agent_filter=args.from_agent,
    )
    print(block)


def cmd_summary(args: argparse.Namespace) -> None:
    """Generate a session summary."""
    nm = Nexus(base_dir=args.store)
    summarizer = SessionSummarizer(nm)

    info = summarizer.generate_session_summary(args.agent)
    
    if info.get("status") != "ok":
        print(f"Error: {info.get('message', 'unknown')}")
        return
    
    print(f"Summary generated for agent '{args.agent}':")
    print(f"  Archive: {info['archive_file']}")
    print(f"  Summary: {info['summary_file']}")
    
    stats = info.get("stats", {})
    stat_line = ", ".join(f"{k}={v}" for k, v in stats.items() if v)
    if stat_line:
        print(f"  Extracted: {stat_line}")
    else:
        print("  Extracted: nothing (empty archive)")


def cmd_compress(args: argparse.Namespace) -> None:
    """Compress a long session log into a compact, reusable block."""
    nm = Nexus(base_dir=args.store)

    raw_log = None
    if args.raw_file:
        raw_path = Path(args.raw_file)
        if not raw_path.exists():
            print(f"Error: file not found: {raw_path}")
            return
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_log = json.load(f)

    if raw_log is None and not args.agent:
        print("Error: укажите --agent (архив) или --raw-file (лог)")
        return

    summarizer = SessionSummarizer(nm)
    info = summarizer.compress_session(
        raw_log=raw_log,
        agent_id=args.agent,
        session_id=args.session_id,
        level=args.level,
        max_chars=args.max_chars,
    )

    if info.get("status") not in ("ok",):
        print(f"Error: {info.get('status')}: {info.get('message', 'unknown')}")
        return

    print(f"Сжато: level={info['level']} · источник: {info['source']}")
    print(f"Размер: {info['stats']['in_chars']} → {info['stats']['out_chars']} "
          f"chars (ratio {info['stats']['ratio']}:1)")
    llm = info.get("llm")
    if llm:
        if llm.get("status") == "ok":
            print(f"LLM: {llm['summary']}")
        else:
            print(f"LLM: {llm.get('message', llm.get('error'))}")
    print()
    print(info["markdown"])


def cmd_decisions(args: argparse.Namespace) -> None:
    """List joint decisions for a project."""
    nm = Nexus(base_dir=args.store)
    
    collab_dir = nm.sessions_cross_dir / f"collab_{args.project}"
    decisions_file = collab_dir / "joint_decisions.json"
    
    if not decisions_file.exists():
        print(f"No decisions found for project '{args.project}'.")
        return
    
    decisions = nm._read_json(decisions_file) or []
    if not decisions:
        print(f"No decisions found for project '{args.project}'.")
        return
    
    print(f"Joint decisions for project '{args.project}':")
    for i, d in enumerate(decisions, 1):
        print(f"\n  {i}. {d.get('decision', 'N/A')}")
        print(f"     By: {', '.join(d.get('by_agents', []))}")
        print(f"     Reason: {d.get('reason', 'N/A')}")


def cmd_watch(args: argparse.Namespace) -> None:
    """Start the watchkeeper (auto-ingestion)."""
    wk = Watchkeeper(
        nexus=Nexus(base_dir=args.store),
        interval=args.interval,
    )
    
    if args.status:
        # Just print status and exit
        status = wk.status
        if not status["running"]:
            print(f"Watchkeeper is not running.")
            print(f"Total scans: {status['scan_count']}")
            print(f"Total ingested: {status['total_ingested']}")
            print(f"Total errors: {status['total_errors']}")
            return
        
        print(f"Watchkeeper is running.")
        print(f"Interval: {status['interval_seconds']}s")
        print(f"Scans: {status['scan_count']}")
        print(f"Ingested: {status['total_ingested']}")
        print(f"Errors: {status['total_errors']}")
        return
    
    if args.toggle:
        msg = wk.toggle()
        print(msg)
        return
    
    # Run in foreground (blocks until Ctrl+C)
    print(f"Watchkeeper starting (interval={args.interval}s). Press Ctrl+C to stop.")
    wk.run_forever()


def cmd_config(args: argparse.Namespace) -> None:
    """Manage Nexus configuration."""
    if args.config_action == "get":
        status = nexus_config.get_status()
        print(f"Config loaded: {'yes' if status['loaded'] else 'no'}")
        print(f"Config path: {status['config_path']}")
        print(f"Version: {status['version']}")
        print()
        print("Settings:")
        for key, value in status['settings'].items():
            print(f"  {key}: {value}")
    
    elif args.config_action == "set":
        if not args.key or not args.value:
            print("Error: --key and --value are required for 'set' action")
            return
        
        section = args.section
        key = args.key
        val = args.value
        
        # Auto-convert value type
        if val.lower() in ("null", "none"):
            converted = None
        elif val.lower() in ("true", "yes", "1"):
            converted = True
        elif val.lower() in ("false", "no", "0"):
            converted = False
        else:
            try:
                converted = int(val)
            except ValueError:
                try:
                    converted = float(val)
                except ValueError:
                    converted = val
        
        nexus_config.set(section, key, converted)
        nexus_config.save()
        print(f"Config updated: {section}.{key} = {converted}")
    
    elif args.config_action == "show":
        # Show raw config JSON
        import json
        print(json.dumps(nexus_config.to_dict(), indent=2, ensure_ascii=False))


def cmd_web(args: argparse.Namespace) -> None:
    """Fetch and save web pages for Nexus ingestion."""
    if args.web_action == "fetch":
        result = fetch_web_page(args.url, timeout=args.timeout, extract_content=not args.raw)
        if not result:
            print(f"Failed to fetch: {args.url}")
            return
        print(f"Title: {result['title']}")
        print(f"URL: {result['url']}")
        print(f"Content: {result['content_length']} chars")
        print("---")
        print(result["content"][:3000])
    
    elif args.web_action == "save":
        result = save_to_raw(args.url, raw_dir=args.store, timeout=args.timeout, project_id=args.project)
        if not result:
            print(f"Failed to save: {args.url}")
            return
        print(f"Saved: {result['title']}")
        print(f"Path: {result['saved_path']}")
        print(f"Content: {result['content_length']} chars")
        print("\nRun 'nexus ingest' to process this file.")
    
    elif args.web_action == "status":
        status = get_web_status()
        print(f"requests: {'available' if status['requests_available'] else 'not installed (using urllib)'}")
        print(f"urllib: available (stdlib)")
        print(f"Timeout: {status['timeout']}s")
        print(f"Max page size: {status['max_page_size_mb']}MB")


def cmd_orch(args: argparse.Namespace) -> None:
    """Manage orchestrator tasks."""
    orch = Orchestrator()
    orch.load_tasks()

    if args.orch_action == "start":
        if not args.task_id or not args.agent_id or not args.project:
            print("Error: --task-id, --agent, and --project are required for 'start' action")
            return
        task = orch.start_task(
            task_id=args.task_id,
            agent_id=args.agent_id,
            project_id=args.project,
            description=args.description or "",
        )
        ctx = "loaded" if task.get("context_loaded") else "none"
        print(f"Task started: {args.task_id}")
        print(f"Agent: {args.agent_id} | Project: {args.project}")
        print(f"Context: {ctx}")
        if task.get("description"):
            print(f"Description: {task['description']}")

    elif args.orch_action == "end":
        if not args.task_id or not args.agent_id:
            print("Error: --task-id and --agent are required for 'end' action")
            return
        result = orch.end_task(task_id=args.task_id, agent_id=args.agent_id)
        print(f"Task ended: {args.task_id} (status={result.get('status')})")

    elif args.orch_action == "status":
        print(get_orchestrator_status())

    elif args.orch_action == "list":
        print(list_orchestrator_tasks(
            agent_id=args.agent,
            project_id=args.project,
            status=args.status,
        ))

    elif args.orch_action == "clear":
        count = orch.clear_tasks()
        print(f"Cleared {count} tasks.")


# --- Phase 2: daemon control (server mode) ---

def _daemon_base_url() -> str:
    return f"http://{nexus_config.server_host}:{nexus_config.server_port}"


def _daemon_healthz(timeout: float = 2.0) -> Optional[dict]:
    """Ping the daemon's public /healthz endpoint. None when not running."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{_daemon_base_url()}/healthz",
                                    timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _daemon_pid_file() -> Path:
    store = nexus_config.store_base_dir or (PROJECT_ROOT / "nexus_store")
    return Path(store) / "_logs" / "server.pid"


def cmd_status(args: argparse.Namespace) -> None:
    """Show overall Nexus status: store, sessions, server mode, daemon."""
    nm = Nexus(base_dir=args.store)

    chunks_index = nm._read_json(nm.chunks_index_file) or []
    if isinstance(chunks_index, dict):
        chunks = list(chunks_index.values())
    elif isinstance(chunks_index, list):
        chunks = chunks_index
    else:
        chunks = []

    projects: dict = {}
    agents: dict = {}
    for c in chunks:
        if not isinstance(c, dict):
            continue
        p = c.get("project_id") or "general"
        projects[p] = projects.get(p, 0) + 1
        a = c.get("agent_id") or "-"
        agents[a] = agents.get(a, 0) + 1

    print("=== Nexus status ===")
    print(f"Store: {nm.base_dir}")
    print(f"Chunks: {len(chunks)} in {len(projects)} project(s)")
    for p, n in sorted(projects.items(), key=lambda kv: -kv[1]):
        print(f"  {p}: {n} chunk(s)")
    print(f"Chunk authors: {', '.join(sorted(agents)) or '-'}")

    sessions = nm._read_json(nm.sessions_index_file) or {}
    print(f"Agents with sessions: {len(sessions)}")
    for agent_id, info in sorted(sessions.items()):
        history = (info or {}).get("history") or []
        done = sum(1 for e in history if e.get("status") == "summarized")
        last = history[-1].get("timestamp") if history else "-"
        print(f"  {agent_id}: {len(history)} archive(s), "
              f"summarized {done}, last={last}")

    print(f"Server mode: {nexus_config.server_mode}")
    print(f"Daemon URL: {_daemon_base_url()}")
    info = _daemon_healthz()
    if info:
        print(f"Daemon: RUNNING (pid={info.get('pid')}, v{info.get('version')})")
    else:
        print("Daemon: not running")
    print(f"Watchkeeper: auto_start={nexus_config.watchkeeper_auto_start}, "
          f"interval={nexus_config.watchkeeper_interval}s")
    print(f"Autoclose: interval={nexus_config.autoclose_interval_min} min, "
          f"timeout={nexus_config.autoclose_minutes} min")
    print(f"Server autostart: {'on' if nexus_config.server_auto_start else 'off'}")


def _cmd_autostart(sub: str) -> None:
    """Manage the boot entry that starts the daemon at login (Phase 3)."""
    from core import autostart

    if sub == "enable":
        rep = autostart.enable(PROJECT_ROOT)
        if rep.get("ok"):
            nexus_config.set("server", "auto_start", True)
            nexus_config.save()
            print(f"Autostart enabled (backend: {rep.get('backend')}).")
            print("  The daemon will be spawned at login via "
                  "`nexus server start` (idempotent).")
        else:
            print(f"Autostart enable FAILED (backend: {rep.get('backend')}): "
                  f"{rep.get('detail')}")
        if rep.get("detail"):
            print(f"  {rep['detail']}")
        return

    if sub == "disable":
        rep = autostart.disable()
        if rep.get("ok"):
            nexus_config.set("server", "auto_start", False)
            nexus_config.save()
            print(f"Autostart disabled (backend: {rep.get('backend')}).")
        else:
            print(f"Autostart disable FAILED (backend: {rep.get('backend')}): "
                  f"{rep.get('detail')}")
        if rep.get("detail"):
            print(f"  {rep['detail']}")
        return

    # status
    rep = autostart.status()
    state = "enabled" if rep.get("enabled") else "disabled"
    print(f"Autostart boot entry: {state} "
          f"(backend: {rep.get('backend') or 'n/a'})")
    if rep.get("detail"):
        print(f"  {rep['detail']}")
    print(f"Config server.auto_start: {nexus_config.server_auto_start}")


def cmd_server(args: argparse.Namespace) -> None:
    """Control the Nexus HTTP daemon (Phase 2 server mode)."""
    action = args.server_action
    if args.host:
        nexus_config.set("server", "host", args.host)
    if args.port:
        nexus_config.set("server", "port", args.port)

    if action == "status":
        info = _daemon_healthz()
        if info:
            print(f"Daemon: RUNNING at {_daemon_base_url()} "
                  f"(pid={info.get('pid')}, v{info.get('version')})")
            print(f"Mode: {nexus_config.server_mode}, "
                  f"token: {'set' if nexus_config.server_token else 'NOT SET'}")
        else:
            print(f"Daemon: NOT RUNNING at {_daemon_base_url()}")
            pid_file = _daemon_pid_file()
            if pid_file.exists():
                stale = pid_file.read_text(encoding="utf-8").strip()
                print(f"Stale pid file: {pid_file} (pid={stale})")
        return

    if action == "autostart":
        _cmd_autostart(getattr(args, "autostart_action", None) or "status")
        return

    if action == "start":
        info = _daemon_healthz()
        if info:
            print(f"Daemon already running at {_daemon_base_url()} "
                  f"(pid={info.get('pid')})")
            return
        token = nexus_config.server_token
        if not token:
            import secrets
            token = secrets.token_hex(16)
            nexus_config.set("server", "token", token)
            nexus_config.save()
            print("Generated a new server token (saved to config).")
        pid_file = _daemon_pid_file()
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        log_file = pid_file.parent / "server.log"
        popen_kwargs = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                             | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            popen_kwargs["start_new_session"] = True
        cmd = [sys.executable, str(PROJECT_ROOT / "nexus_http_server.py"),
               "--host", str(nexus_config.server_host),
               "--port", str(nexus_config.server_port),
               "--token", token,
               "--pid-file", str(pid_file)]
        with open(log_file, "a", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=log,
                                    cwd=str(PROJECT_ROOT), **popen_kwargs)
        for _ in range(30):  # up to 15s, then point the user to the log
            time.sleep(0.5)
            if _daemon_healthz():
                print(f"Daemon started: pid={proc.pid}, url={_daemon_base_url()}")
                print(f"  pid file: {pid_file}")
                print(f"  log file: {log_file}")
                return
        print(f"Daemon process spawned (pid={proc.pid}) but did not answer yet.")
        print(f"  Check the log: {log_file}")
        return

    if action == "stop":
        pid_file = _daemon_pid_file()
        if not pid_file.exists():
            print("Daemon: not running (no pid file).")
            return
        raw = pid_file.read_text(encoding="utf-8").strip()
        try:
            pid = int(raw)
        except ValueError:
            print(f"Corrupt pid file: {pid_file} ({raw!r})")
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"Daemon stopped (pid={pid}).")
        except Exception as e:
            print(f"Failed to stop daemon (pid={pid}): {e}")
        finally:
            pid_file.unlink(missing_ok=True)
        return

    if action == "token":
        token = nexus_config.server_token
        print(f"Token: {token or '(not set — generated on first `server start`)'}")
        print(f"Config file: {nexus_config._config_path}")
        return


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nexus",
        description="Nexus CLI — Universal memory for AI agents",
    )
    parser.add_argument(
        "--store",
        type=str,
        default=str(PROJECT_ROOT / "nexus_store"),
        help="Path to nexus_store directory (default: ./nexus_store)",
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # add
    p_add = subparsers.add_parser("add", help="Add a knowledge chunk")
    p_add.add_argument("content", help="Content to store")
    p_add.add_argument("--tags", nargs="+", default=["#cli"], help="Tags (e.g. #bug #feature)")
    p_add.add_argument("--project", help="Project ID")
    p_add.add_argument("--source", help="Source URL or doc name")
    p_add.add_argument("--agent", default="cli", help="Agent ID (default: cli)")
    
    # search
    p_search = subparsers.add_parser("search", help="Search knowledge base")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--tags", nargs="+", help="Filter by tags")
    p_search.add_argument("--project", help="Filter by project")
    p_search.add_argument("--agent", help="Filter by agent")
    p_search.add_argument("--semantic", action="store_true", help="Use semantic search")
    p_search.add_argument("--top-k", type=int, default=5, help="Top K results (semantic)")
    
    # ingest
    p_ingest = subparsers.add_parser("ingest", help="Run ingestion pipeline")
    p_ingest.add_argument("--max", type=int, help="Limit files to process")
    
    # context
    p_context = subparsers.add_parser("context", help="Get context for an agent")
    p_context.add_argument("--agent", required=True, help="Agent ID")
    p_context.add_argument("--project", help="Project ID")
    
    # context-prompt
    p_prompt = subparsers.add_parser("context-prompt", help="Get compact context prompt block")
    p_prompt.add_argument("--query", required=True, help="Query for context")
    p_prompt.add_argument("--agent", help="Agent ID")
    p_prompt.add_argument("--project", help="Project ID")
    p_prompt.add_argument("--max-chunks", type=int, default=5, help="Max chunks")
    p_prompt.add_argument("--max-chars", type=int, default=1800, help="Max chars")
    
    # summary
    p_summary = subparsers.add_parser("summary", help="Generate session summary")
    p_summary.add_argument("--agent", required=True, help="Agent ID")

    # compress
    p_compress = subparsers.add_parser(
        "compress", help="Compress a long session log into a compact block"
    )
    p_compress.add_argument("--agent", help="Agent ID (latest archived session)")
    p_compress.add_argument("--session-id", help="Explicit session timestamp (YYYYMMDD_HHMM)")
    p_compress.add_argument("--raw-file", help="Path to a JSON log file to compress (no store)")
    p_compress.add_argument("--level", type=int, default=1,
                            help="1 = heuristic (default), 2 = LLM upgrade")
    p_compress.add_argument("--max-chars", type=int,
                            help="Char budget for the markdown block")
    
    # decisions
    p_decisions = subparsers.add_parser("decisions", help="List joint decisions")
    p_decisions.add_argument("--project", required=True, help="Project ID")

    # digest
    p_digest = subparsers.add_parser("digest", help="Project digest: cross-agent changes")
    p_digest.add_argument("--project", required=True, help="Project ID")
    p_digest.add_argument("--since", help="Only changes after this time (session ts YYYYMMDD_HHMM, ISO or date)")
    p_digest.add_argument("--days", type=int, help="Only changes within the last N days")
    p_digest.add_argument("--from-agent", help="Show sessions of this agent only")
    p_digest.add_argument("--exclude-agent", help="Hide sessions of this agent")
    
    # watch
    p_watch = subparsers.add_parser("watch", help="Start watchkeeper (auto-ingestion)")
    p_watch.add_argument("--interval", type=int, default=300, help="Scan interval in seconds (default: 300)")
    p_watch.add_argument("--status", action="store_true", help="Show watchkeeper status and exit")
    p_watch.add_argument("--toggle", action="store_true", help="Toggle watchkeeper (start/stop)")
    
    # config
    p_config = subparsers.add_parser("config", help="Manage Nexus configuration")
    p_config.add_argument("config_action", choices=["get", "set", "show"], help="Action: get/set/show config")
    p_config.add_argument("--section", default="general", help="Config section (for 'set' action)")
    p_config.add_argument("--key", default=None, help="Config key (for 'set' action)")
    p_config.add_argument("--value", default=None, help="Config value (for 'set' action)")
    
    # web
    p_web = subparsers.add_parser("web", help="Fetch and save web pages")
    p_web.add_argument("web_action", choices=["fetch", "save", "status"], help="Action: fetch/save/status")
    p_web.add_argument("url", nargs="?", help="URL to fetch/save")
    p_web.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    p_web.add_argument("--project", help="Project ID (for 'save' action)")
    p_web.add_argument("--raw", action="store_true", help="Return raw HTML (for 'fetch' action)")
    
    # orchestrator
    p_orch = subparsers.add_parser("orch", help="Manage orchestrator tasks")
    p_orch.add_argument("orch_action", choices=["start", "end", "status", "list", "clear"], help="Action")
    p_orch.add_argument("--task-id", help="Task ID (for start/end)")
    p_orch.add_argument("--agent", help="Agent ID (for start/end/list)")
    p_orch.add_argument("--project", help="Project ID (for start/list)")
    p_orch.add_argument("--description", help="Task description (for start)")
    p_orch.add_argument("--status", help="Filter by status (for list)")
    
    # server (Phase 2: daemon control)
    p_server = subparsers.add_parser("server", help="Control the Nexus HTTP daemon")
    p_server.add_argument("server_action",
                          choices=["start", "stop", "status", "token",
                                   "autostart"],
                          help="Action: start/stop daemon, show status or "
                               "token, manage boot autostart")
    p_server.add_argument("autostart_action", nargs="?",
                          choices=["status", "enable", "disable"],
                          default="status",
                          help="Sub-action for 'autostart' (default: status)")
    p_server.add_argument("--host", help="Override bind host (persisted to config)")
    p_server.add_argument("--port", type=int, help="Override bind port (persisted to config)")
    
    # status
    subparsers.add_parser("status", help="Show overall Nexus status")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    commands = {
        "add": cmd_add,
        "search": cmd_search,
        "ingest": cmd_ingest,
        "context": cmd_context,
        "context-prompt": cmd_context_prompt,
        "summary": cmd_summary,
        "compress": cmd_compress,
        "digest": cmd_digest,
        "decisions": cmd_decisions,
        "watch": cmd_watch,
        "config": cmd_config,
        "web": cmd_web,
        "orch": cmd_orch,
        "server": cmd_server,
        "status": cmd_status,
    }
    
    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
