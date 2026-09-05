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

Запуск из корня проекта:
    python nexus_cli.py <command> [options]
"""

import argparse
import json
import os
import sys
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
    
    # decisions
    p_decisions = subparsers.add_parser("decisions", help="List joint decisions")
    p_decisions.add_argument("--project", required=True, help="Project ID")
    
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
        "decisions": cmd_decisions,
        "watch": cmd_watch,
        "config": cmd_config,
        "web": cmd_web,
        "orch": cmd_orch,
    }
    
    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
