"""
MCP Server for Nexus - Smart Memory System.

Exposes the Nexus memory manager as MCP tools so any MCP-compatible agent
(Cline, Claude, local LLM tool consumers, etc.) can store and retrieve knowledge.

Tools:
- search_knowledge: Search knowledge base
- add_note: Add a knowledge chunk
- archive_current_session: Archive raw session data
- get_context: Get context for a new session
- record_joint_decision: Record a collaborative decision
- run_ingestion: Run the ingestion pipeline on input_docs/raw
- get_ingestion_status: Report what the pipeline last processed
- generate_session_summary: Auto-summarize the latest archived session (Stage 3)
- collapse_session_history: Fold old archives into one retrospective summary
- end_session: One-call session save: archive + auto-summary (+ collapse) (Stage 3)
- semantic_search: Vector similarity search over the library (Stage 4)
- build_context_prompt: Ready-to-paste context block from top chunks (Stage 4)
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add project root to path so `core.nexus_core` is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.nexus_core import Nexus
from core.ingestion import IngestionPipeline
from core.summarizer import SessionSummarizer
from core.session_hook import SessionHook
from core.semantic import SemanticSearch
from core.project_digest import ProjectDigest
from core.watchkeeper import Watchkeeper
from core.ocr import extract_text_from_image, ocr_scan_directory, get_ocr_status
from core.web import fetch_web_page, save_to_raw, get_web_status
from core.orchestrator import Orchestrator, start_orchestrator_task, end_orchestrator_task, get_orchestrator_status, list_orchestrator_tasks
from core.autoclose import auto_close_stale_sessions
from core.config import config as nexus_config

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    print("MCP library not found. Please install it with: pip install mcp", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent
store_path = nexus_config.store_base_dir or PROJECT_ROOT / "nexus_store"
nm = Nexus(base_dir=store_path)
sem = SemanticSearch(nm)
wk = Watchkeeper(nexus=nm, interval=nexus_config.watchkeeper_interval)

server = MCPServer(
    name="nexus",
    description="Smart Memory System: shared knowledge, sessions and joint decisions for AI agents.",
    version="0.9.5",
    instructions=(
        "Nexus is the shared memory of AI agents. Use search_knowledge to retrieve "
        "information (keyword-based), semantic_search for meaning-based ranking, "
        "build_context_prompt to get a compact context block for a small-context LLM, "
        "add_note to store knowledge, get_context to load prior session context, "
        "end_session to finish a session in one call (archive + auto-summary), "
        "archive_current_session + generate_session_summary as the manual "
        "alternative, and record_joint_decision for collaborative decisions."
    ),
)

# --- Tools ---

@server.tool()
async def search_knowledge(
    query: str,
    tags: Optional[list[str]] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Search the Nexus knowledge base for relevant chunks.

    Args:
        query: The search term to look for.
        tags: Optional tags to filter by (e.g. ["#bug_fix"]).
        project_id: Optional project scope.
        agent_id: Optional agent scope (includes agent-specific notes).
    """
    results = nm.search(query=query, tags=tags, project_id=project_id, agent_id=agent_id)
    if not results:
        return "No results found."

    lines = [f"Found {len(results)} result(s):"]
    for res in results:
        snippet_path = nm.base_dir / res["source_file"]
        try:
            with open(snippet_path, "r", encoding="utf-8") as f:
                raw = f.read()
            # Strip YAML front matter (between the first two "---" lines)
            if raw.startswith("---"):
                parts = raw.split("---", 2)
                body = parts[2] if len(parts) >= 3 else raw
            else:
                body = raw
            preview = body.strip()[:400]
        except Exception:
            preview = "[Content could not be read]"
        lines.append(
            f"- ID: {res['id'][:8]} | Tags: {', '.join(res.get('tags', []))} | "
            f"Project: {res.get('project_id') or 'general'}\n  {preview}"
        )
    return "\n\n".join(lines)


@server.tool()
async def add_note(
    content: str,
    tags: list[str],
    project_id: Optional[str] = None,
    source: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Add a new knowledge chunk to the Nexus library.

    Args:
        content: The knowledge to store.
        tags: Tags to categorize it (e.g. ["#research"]).
        project_id: Optional project to attach the note to.
        source: Optional source (URL, doc name).
        agent_id: Optional agent that created the note.
    """
    chunk_id = nm.add_chunk(
        content=content,
        tags=tags,
        project_id=project_id,
        source=source,
        agent_id=agent_id,
    )
    return f"Note added successfully with ID: `{chunk_id[:8]}`"


@server.tool()
async def archive_current_session(
    agent_id: str,
    session_data: dict,
) -> str:
    """Archive raw session data for an agent so it can be reused in future sessions.

    Args:
        agent_id: The ID of the agent whose session is being archived.
        session_data: Dict with the session's raw data (chat history, actions...).
    """
    if not agent_id or not session_data:
        return "Error: agent_id and session_data are required."
    archived_path = nm.archive_session(agent_id=agent_id, session_data=session_data)
    return f"Session archived to: `{archived_path}`"


@server.tool()
async def get_context(
    agent_id: str,
    project_id: Optional[str] = None,
) -> str:
    """Retrieve context for an agent starting a new session.

    Combines the agent's personal session summary with shared project knowledge.

    Args:
        agent_id: The ID of the agent.
        project_id: Optional project to also load shared knowledge for.
    """
    if not agent_id:
        return "Error: agent_id is required."
    context = nm.get_context_for_agent(agent_id=agent_id, project_id=project_id)
    if not context.strip():
        return "No prior context found."
    return context


@server.tool()
async def project_digest(
    project_id: str,
    since: Optional[str] = None,
    exclude_agent: Optional[str] = None,
    agent_filter: Optional[str] = None,
) -> str:
    """Cross-agent project digest: what happened in the project.

    Aggregates ALL agents' project-tagged sessions, joint decisions and recent
    library chunks into one block. Call at session start with
    since=<your last session timestamp> to see changes since you were away.

    Args:
        project_id: Project to scope by.
        since: Lower bound, e.g. "20260911_0338" (session ts), ISO or plain date.
        exclude_agent: Hide sessions archived by this agent.
        agent_filter: Show sessions of this agent only.
    """
    try:
        return ProjectDigest(nm).get_digest(
            project_id=project_id,
            since=since,
            exclude_agent=exclude_agent,
            agent_filter=agent_filter,
        )
    except Exception as e:
        return f"Project digest error: {e}"


@server.tool()
async def record_joint_decision(
    project_id: str,
    decision: str,
    by_agents: list[str],
    reason: str,
) -> str:
    """Record a decision made collaboratively by multiple agents on a project.

    Args:
        project_id: The project ID.
        decision: The decision that was made.
        by_agents: List of agent IDs involved.
        reason: Why the decision was made.
    """
    if not all([project_id, decision, by_agents, reason]):
        return "Error: project_id, decision, by_agents and reason are all required."
    nm.add_joint_decision(
        project_id=project_id,
        decision={
            "decision": decision,
            "by_agents": by_agents,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        },
    )
    return f"Joint decision recorded for project `{project_id}`."


@server.tool()
async def run_ingestion(
    max_files: Optional[int] = None,
) -> str:
    """Run the Nexus ingestion pipeline.

    Scans input_docs/raw/, extracts text from supported formats (md, txt, json,
    jsonl, csv, html), normalizes to Markdown, splits into chunks, indexes them
    into the library, and moves processed files to input_docs/processed/.

    Args:
        max_files: Optional limit on how many files to process in one run
            (useful to avoid processing hundreds of files in one call).
    """
    pipeline = IngestionPipeline(nexus=nm)
    report = pipeline.run()
    if max_files:
        # Keep the global summary but only return details for the first N files.
        report["files"] = report["files"][:max_files]
        report["files_truncated"] = True
    # Compact human-readable summary
    lines = [
        f"Scanned: {report['scanned']} file(s)",
        f"Ingested: {report['ingested']}",
        f"Skipped: {report['skipped']}",
        f"Errors: {report['errors']}",
    ]
    for item in report["files"]:
        lines.append(
            f"- {item['path']}: {item['status']} "
            f"({item.get('chunks', 0)} chunks)"
            + (f", reason: {item['reason']}" if item.get("reason") else "")
        )
    return "\n".join(lines)


@server.tool()
async def get_ingestion_status() -> str:
    """Report recent ingestion history from the ingestion log.

    Returns the last 10 entries of nexus_store/_logs/ingestion.jsonl.
    """
    log_file = nm.base_dir / "_logs" / "ingestion.jsonl"
    if not log_file.exists():
        return "No ingestion has been run yet."
    import json as _json

    entries = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(_json.loads(line))
                    except _json.JSONDecodeError:
                        continue
    except OSError as e:
        return f"Cannot read ingestion log: {e}"

    if not entries:
        return "Ingestion log is empty."
    lines = [f"Last {len(entries[-10:])} ingestion log entries:"]
    for e in entries[-10:]:
        lines.append(f"[{e.get('ts')}] {e.get('level').upper()}: {e.get('msg')}")
    return "\n".join(lines)


@server.tool()
async def generate_session_summary(
    agent_id: str,
    session_id: Optional[str] = None,
) -> str:
    """Auto-generate a structured summary (Сделано / Решения / Дальше) from an
    archived session and store it for future get_context() calls. Heuristic,
    no LLM required. Without session_id the newest archive is used.

    Args:
        agent_id: The agent whose archived session should be summarized.
        session_id: Optional archive timestamp (e.g. "20260905_1859").
    """
    summarizer = SessionSummarizer(nm)
    info = summarizer.generate_session_summary(agent_id, session_id=session_id)
    if info.get("status") != "ok":
        return f"{info.get('status')}: {info.get('message', 'unknown error')}"
    stats = info.get("stats", {})
    stat_line = ", ".join(f"{k}={v}" for k, v in stats.items() if v)
    return (
        f"Summary generated for agent `{agent_id}` "
        f"(session {session_id or info.get('archive_file', '')}).\n"
        f"Archive: {info['archive_file']}\n"
        f"Summary: {info['summary_file']}\n"
        f"Extracted: {stat_line or 'nothing (empty archive)'}"
    )


@server.tool()
async def collapse_session_history(
    agent_id: str,
    keep_last: int = 1,
) -> str:
    """Fold old session archives (all but the newest keep_last) into ONE
    retrospective summary to save storage and context tokens. The newest
    session summary keeps serving get_context(); the collapsed history is
    stored separately as a fallback.

    Args:
        agent_id: The agent whose history should be collapsed.
        keep_last: How many of the newest archives to keep intact (default 1).
    """
    summarizer = SessionSummarizer(nm)
    info = summarizer.collapse_history(agent_id, keep_last=keep_last)
    if info.get("status") != "ok":
        return f"{info.get('status')}: {info.get('message', 'unknown error')}"
    return (
        f"Collapsed {info['collapsed']} archive(s) for agent `{agent_id}` "
        f"({info['from']} … {info['to']}) into:\n{info['summary_file']}"
    )


@server.tool()
async def compress_session(
    agent_id: Optional[str] = None,
    session_id: Optional[str] = None,
    raw_log: Optional[dict] = None,
    level: int = 1,
    max_chars: Optional[int] = None,
) -> str:
    """Compress a long session log into a compact, reusable structure that
    keeps facts/decisions/tasks while dropping details. The markdown block
    can be pasted into get_context(). Pass either `agent_id` (latest or
    `session_id`-dated archive) or `raw_log` (any JSON log, e.g. NeoKron).

    Args:
        agent_id: Compress the latest archived session of this agent.
        session_id: Optional explicit archive timestamp (YYYYMMDD_HHMM).
        raw_log: Compress this JSON-serializable log directly (no store).
        level: 1 = heuristic (default, local), 2 = optional LLM upgrade
            (falls back to heuristic when no LLM is configured).
        max_chars: Optional char budget for the markdown block.
    """
    summarizer = SessionSummarizer(nm)
    info = summarizer.compress_session(
        raw_log=raw_log,
        agent_id=agent_id,
        session_id=session_id,
        level=level,
        max_chars=max_chars,
    )
    if info.get("status") == "invalid_input":
        return f"invalid_input: {info.get('message', 'unknown error')}"
    if info.get("status") == "not_found":
        return f"not_found: {info.get('message', 'unknown error')}"
    lines = [
        f"Session compressed (level={info['level']}, source={info['source']}).",
        f"Chars: {info['stats']['in_chars']} → {info['stats']['out_chars']} "
        f"(ratio {info['stats']['ratio']}:1).",
    ]
    llm = info.get("llm")
    if llm:
        if llm.get("status") == "ok":
            lines.append(f"LLM summary: {llm['summary']}")
        else:
            lines.append(f"LLM note: {llm.get('message', llm.get('error'))}")
    lines.append("\n" + info["markdown"])
    return "\n".join(lines)


@server.tool()
async def semantic_search(
    query: str,
    top_k: int = 5,
    tags: Optional[list[str]] = None,
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Rank library chunks by MEANING similarity to the query (vector search,
    hybrid: cosine + keyword-hit bonus). Works for Russian and English,
    no external services required.

    Args:
        query: What to look for (a phrase works better than one word).
        top_k: How many results to return (default 5).
        tags: Optional tags to filter by (e.g. ["#bug_fix"]).
        project_id: Optional project scope.
        agent_id: Optional agent scope (includes agent-specific notes).
    """
    try:
        results = sem.search(query, top_k=top_k, tags=tags,
                             project_id=project_id, agent_id=agent_id)
    except Exception as e:
        return f"Semantic search error: {e}"
    if not results:
        return "No semantic matches found (try different wording or check filters)."

    lines = [f"Semantic results for «{query}» (top {len(results)}):"]
    for res in results:
        marker = " ✚keyword" if res.get("keyword_hit") else ""
        lines.append(
            f"- score={res['score']:.3f}{marker} | {res['id'][:8]} "
            f"| project: {res.get('project_id') or 'general'} "
            f"| tags: {', '.join(res.get('tags', [])) or '-'}\n"
            f"  {_read_preview(nm.base_dir / res['source_file'])}"
        )
    return "\n".join(lines)


def _read_preview(path, limit: int = 300) -> str:
    """One-line preview of a chunk body for search output."""
    from core.semantic import _read_chunk_body

    body = _read_chunk_body(path).replace("\n", " ")
    return body[:limit] + ("…" if len(body) > limit else "")


@server.tool()
async def build_context_prompt(
    query: str,
    project_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    max_chunks: int = 5,
    max_chars: int = 1800,
) -> str:
    """Build a compact, copy-ready CONTEXT BLOCK from the most relevant Nexus
    chunks (semantic ranking, trimmed to a character budget). Designed for
    local LLMs with small context: paste the returned block into the prompt
    instead of reading whole files.

    Args:
        query: The task/question the context should cover.
        project_id: Optional project scope.
        agent_id: Optional agent scope (includes agent-specific notes).
        max_chunks: Max number of chunks to include (default 5).
        max_chars: Total character budget for the block (default 1800).
    """
    try:
        info = sem.build_prompt_block(
            query, project_id=project_id, agent_id=agent_id,
            max_chunks=max_chunks, max_chars=max_chars,
        )
    except Exception as e:
        return f"Prompt build error: {e}"
    if not info["chunks"]:
        return info["text"]
    note = (
        f"\n\n[block: {info['chars']} chars, {len(info['chunks'])} chunk(s) "
        f"from Nexus v0.5.1]"
    )
    return info["text"] + note


@server.tool()
async def end_session(
    agent_id: str,
    session_data: dict,
    project_id: Optional[str] = None,
    collapse_after: bool = False,
    keep_last: int = 1,
) -> str:
    """Finish a session in ONE call: archive the raw session data, auto-generate
    the structured summary (Сделано / Решения / Дальше) and optionally collapse
    older archives. Replaces archive_current_session + generate_session_summary.

    Args:
        agent_id: The ID of the agent ending the session.
        session_data: Dict with the session's raw data. Use readable keys:
            actions / decisions / next_steps / chat_history (или по-русски:
            сделано / решения / дальше) — автосводка будет точнее.
        project_id: Optional project to tag the session with (appears in the
            cross-agent project digest).
        collapse_after: Also fold archives older than keep_last into one
            retrospective summary (default False).
        keep_last: How many of the newest archives to keep when collapsing.
    """
    hook = SessionHook(nm)
    info = hook.end_session(
        agent_id=agent_id,
        session_data=session_data,
        project_id=project_id,
        collapse_after=collapse_after,
        keep_last=keep_last,
    )
    if info.get("status") != "ok":
        return f"{info.get('status')}: {info.get('message', 'unknown error')}"
    lines = [
        f"Session saved for agent `{agent_id}`.",
        f"Archive: {info['archive_file']}",
        f"Summary: {info['summary_file']}",
    ]
    stat_line = ", ".join(f"{k}={v}" for k, v in info.get("stats", {}).items() if v)
    if stat_line:
        lines.append(f"Extracted: {stat_line}")
    coll = info.get("collapsed")
    if coll:
        lines.append(
            f"Collapsed: {coll.get('collapsed')} old archive(s) -> {coll.get('summary_file')}"
        )
    return "\n".join(lines)


@server.tool()
async def start_watchkeeper(
    interval: int = 300,
) -> str:
    """Start the automatic ingestion watchkeeper.

    Monitors input_docs/raw/ every N seconds and automatically runs
    the ingestion pipeline for new files.

    Args:
        interval: Scan interval in seconds (default 300 = 5 minutes).
    """
    if wk.is_running:
        return "Watchkeeper is already running."
    wk.interval = interval
    wk.start()
    return f"Watchkeeper started (interval={interval}s). Scanning for new files automatically."


@server.tool()
async def stop_watchkeeper() -> str:
    """Stop the automatic ingestion watchkeeper."""
    if not wk.is_running:
        return "Watchkeeper is not running."
    wk.stop()
    return "Watchkeeper stopped."


@server.tool()
async def watch_status() -> str:
    """Report the current status of the watchkeeper."""
    status = wk.status
    if not status["running"]:
        lines = [
            "Watchkeeper is not running.",
            f"Total scans: {status['scan_count']}",
            f"Total ingested: {status['total_ingested']}",
            f"Total errors: {status['total_errors']}",
        ]
        return "\n".join(lines)

    lines = [
        "Watchkeeper is running.",
        f"Interval: {status['interval_seconds']}s",
        f"Raw dir: {status['raw_dir']}",
        f"Scans: {status['scan_count']}",
        f"Ingested: {status['total_ingested']}",
        f"Errors: {status['total_errors']}",
        f"Started: {status['start_time']}",
    ]
    if status["last_report"]:
        r = status["last_report"]
        lines.append(
            f"Last scan: ingested={r.get('ingested', 0)}, "
            f"skipped={r.get('skipped', 0)}, errors={r.get('errors', 0)}"
        )
    return "\n".join(lines)


@server.tool()
async def ocr_scan_images(
    directory: str,
    engine: Optional[str] = None,
    languages: Optional[list[str]] = None,
) -> str:
    """Scan a directory for image files and extract text via OCR.

    Supports: .png, .jpg, .jpeg, .bmp, .tiff, .webp.
    Uses pytesseract (fast, free) or easyocr (higher quality) depending
    on what is installed.

    Args:
        directory: Path to directory containing image files.
        engine: OCR engine ("pytesseract" or "easyocr"). Auto-select if None.
        languages: Language codes (default: ["eng", "rus"]).
    """
    report = ocr_scan_directory(directory, engine=engine, languages=languages)
    if report["status"] == "error":
        return f"Error: {report['message']}"

    lines = [
        f"OCR scan complete: {report['scanned']} image(s) found",
        f"Success: {report['success']}, Failed: {report['failed']}",
    ]
    for r in report["results"]:
        status_icon = "✓" if r["status"] == "success" else "✗"
        preview = r.get("preview", "")[:150].replace("\n", " ")
        lines.append(f"  {status_icon} {r['file']}: {preview}...")
    return "\n".join(lines)


@server.tool()
async def ocr_extract_image(
    image_path: str,
    engine: Optional[str] = None,
    languages: Optional[list[str]] = None,
) -> str:
    """Extract text from a single image file using OCR.

    Args:
        image_path: Path to the image file.
        engine: OCR engine ("pytesseract" or "easyocr"). Auto-select if None.
        languages: Language codes (default: ["eng", "rus"]).
    """
    text = extract_text_from_image(image_path, engine=engine, languages=languages)
    if not text:
        return "OCR returned no text (check image quality or install an OCR engine: pytesseract / easyocr)."
    return f"Extracted text ({len(text)} chars):\n---\n{text}\n---"


@server.tool()
async def ocr_status() -> str:
    """Report available OCR engines and supported formats."""
    status = get_ocr_status()
    engines = []
    if status["pytesseract"]:
        engines.append("pytesseract ✓")
    if status["easyocr"]:
        engines.append("easyocr ✓")
    if not engines:
        engines.append("none (install pytesseract or easyocr)")

    lines = [
        f"OCR engines: {', '.join(engines)}",
        f"Best available: {status['best_available'] or 'none'}",
        f"Default languages: {', '.join(status['default_languages'])}",
        f"Supported formats: {', '.join(status['supported_formats'])}",
    ]
    return "\n".join(lines)


@server.tool()
async def config_get() -> str:
    """Get current Nexus configuration status and values."""
    status = nexus_config.get_status()
    lines = [
        f"Config loaded: {'yes' if status['loaded'] else 'no'}",
        f"Config path: {status['config_path']}",
        f"Version: {status['version']}",
        "",
        "Settings:",
        f"  store_base_dir: {status['settings']['store_base_dir'] or '(default: ./nexus_store)'}",
        f"  max_chunk_chars: {status['settings']['max_chunk_chars']}",
        f"  watchkeeper_interval: {status['settings']['watchkeeper_interval']}s",
        f"  watchkeeper_auto_start: {status['settings']['watchkeeper_auto_start']}",
        f"  ocr_languages: {', '.join(status['settings']['ocr_languages'])}",
        f"  ocr_engine: {status['settings']['ocr_engine'] or '(auto)'}",
        f"  use_sentence_transformers: {status['settings']['use_sentence_transformers']}",
    ]
    return "\n".join(lines)


@server.tool()
async def config_set(
    section: str,
    key: str,
    value: str,
) -> str:
    """Update a Nexus configuration value.

    Args:
        section: Config section (store, ingestion, watchkeeper, summarizer, semantic, ocr, prompt_templates).
        key: Configuration key within the section.
        value: New value (string, auto-converted to int/bool/None).
    """
    # Auto-convert value types
    if value.lower() in ("null", "none", "none"):
        converted = None
    elif value.lower() in ("true", "yes", "1"):
        converted = True
    elif value.lower() in ("false", "no", "0"):
        converted = False
    else:
        try:
            converted = int(value)
        except ValueError:
            try:
                converted = float(value)
            except ValueError:
                converted = value

    nexus_config.set(section, key, converted)
    nexus_config.save()
    return f"Config updated: {section}.{key} = {converted}"


@server.tool()
async def config_update(
    settings: dict,
) -> str:
    """Update multiple configuration values at once.

    Args:
        settings: Nested dict of section -> {key: value} pairs.
                  Example: {"watchkeeper": {"default_interval": 600}, "ocr": {"default_languages": ["eng"]}}
    """
    nexus_config.update(**settings)
    nexus_config.save()
    sections = list(settings.keys())
    return f"Config updated for sections: {', '.join(sections)}"


@server.tool()
async def web_fetch(
    url: str,
    extract_content: bool = True,
    timeout: int = 30,
) -> str:
    """Fetch a web page and return its content.

    Args:
        url: URL to fetch.
        extract_content: If True, extract main content (strip scripts/styles).
        timeout: Request timeout in seconds (default 30).
    """
    result = fetch_web_page(url, timeout=timeout, extract_content=extract_content)
    if not result:
        return f"Failed to fetch: {url}"

    lines = [
        f"Title: {result['title']}",
        f"URL: {result['url']}",
        f"Content length: {result['content_length']} chars",
        "",
        "---",
        result["content"][:5000],  # limit output
        "---",
    ]
    return "\n".join(lines)


@server.tool()
async def web_save(
    url: str,
    project_id: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """Fetch a web page and save it to input_docs/raw/ for ingestion.

    The file will be automatically processed by the ingestion pipeline
    (manually via run_ingestion or automatically via watchkeeper).

    Args:
        url: URL to fetch and save.
        project_id: Optional project ID for metadata.
        timeout: Request timeout in seconds (default 30).
    """
    result = save_to_raw(url, timeout=timeout, project_id=project_id)
    if not result:
        return f"Failed to save: {url}"

    lines = [
        f"Saved: {result['title']}",
        f"URL: {result['url']}",
        f"Path: {result['saved_path']}",
        f"Content: {result['content_length']} chars",
        "",
        "Run `run_ingestion()` to process this file into the knowledge base.",
    ]
    return "\n".join(lines)


@server.tool()
async def web_status() -> str:
    """Report web fetching capabilities."""
    status = get_web_status()
    lines = [
        f"requests: {'available' if status['requests_available'] else 'not installed (using urllib)'}",
        f"urllib: available (stdlib)",
        f"Timeout: {status['timeout']}s",
        f"Max page size: {status['max_page_size_mb']}MB",
    ]
    return "\n".join(lines)


@server.tool()
async def orch_start_task(
    task_id: str,
    agent_id: str,
    project_id: str,
    description: str = "",
    model: Optional[str] = None,
    variant: Optional[str] = None,
) -> str:
    """Start a task in the Nexus orchestrator.

    Loads context from Nexus for the agent and project,
    then tracks the task with its status.

    Args:
        task_id: Unique task identifier.
        agent_id: Agent executing the task (e.g., "cline", "Gea").
        project_id: Project the task belongs to.
        description: Human-readable task description.
        model: Optional model name for the agent.
        variant: Optional reasoning variant.
    """
    return start_orchestrator_task(
        task_id=task_id,
        agent_id=agent_id,
        project_id=project_id,
        description=description,
        model=model,
        variant=variant,
        base_dir=nm.base_dir,
    )


@server.tool()
async def orch_end_task(
    task_id: str,
    agent_id: str,
    session_data: Optional[dict] = None,
) -> str:
    """End a task and save session data to Nexus.

    Args:
        task_id: Task to end.
        agent_id: Agent that ran the task.
        session_data: Session data for Nexus archiving.
    """
    return end_orchestrator_task(
        task_id=task_id,
        agent_id=agent_id,
        session_data=session_data,
        base_dir=nm.base_dir,
    )


@server.tool()
async def orch_status() -> str:
    """Get orchestrator status: tasks, agents, projects."""
    return get_orchestrator_status(base_dir=nm.base_dir)


@server.tool()
async def orch_list_tasks(
    agent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
) -> str:
    """List tasks with optional filters.

    Args:
        agent_id: Filter by agent.
        project_id: Filter by project.
        status: Filter by status (running/completed/cancelled).
    """
    return list_orchestrator_tasks(
        agent_id=agent_id,
        project_id=project_id,
        status=status,
        base_dir=nm.base_dir,
    )


@server.tool()
async def session_autoclose(
    timeout_min: int = 360,
) -> str:
    """Close (summarize) stale not-yet-summarized sessions for ALL agents.

    Finds, for every agent, archives older than `timeout_min` minutes that
    never got a summary (index status == 'archived') and backfills summaries
    for them. Use when an agent disappeared mid-session so the project digest
    stays complete. The daemon also runs this as a background job.

    Args:
        timeout_min: How old an unsummarized archive must be to close (default 360).
    """
    report = auto_close_stale_sessions(nm, timeout_min=timeout_min)
    return json.dumps(report, ensure_ascii=False)


# --- Daemon mode (Phase 2): thin client over the Nexus HTTP daemon ---

def _install_daemon_proxy() -> Optional[str]:
    """Reroute every tool through the Nexus HTTP daemon (server.mode='daemon').

    Each registered tool's function is replaced by a thin HTTP proxy that
    POSTs the tool arguments to /mcp/<tool_name> on the daemon, so the MCP
    process never touches the store directly (single owner, no file locking).
    Returns the daemon URL on success, None when not in daemon mode.
    """
    if nexus_config.server_mode != "daemon":
        return None
    from core.server_client import client_from_config

    client = client_from_config(nexus_config)

    def _make_proxy(tool_name: str):
        async def proxy_fn(**kwargs):
            try:
                result = client.call(tool_name, **kwargs)
            except Exception as e:  # daemon down / unauthorized / unknown route
                return json.dumps(
                    {"status": "error", "message": f"Daemon call failed: {e}"},
                    ensure_ascii=False,
                )
            return json.dumps(result, ensure_ascii=False, default=str)
        proxy_fn.__name__ = tool_name
        return proxy_fn

    for name, tool in list(server._tool_manager._tools.items()):
        if getattr(tool.fn, "_nexus_daemon_proxy", False):
            continue  # already proxied (idempotent re-entry)
        proxy = _make_proxy(name)
        proxy._nexus_daemon_proxy = True
        tool.fn = proxy
        tool.is_async = True
    return client.base_url


# --- Entry Point ---

async def main() -> None:
    daemon_url = _install_daemon_proxy()
    if daemon_url:
        print(f"[nexus-mcp] daemon mode: proxying tools to {daemon_url}",
              file=sys.stderr)
    await server.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())