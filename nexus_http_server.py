# -*- coding: utf-8 -*-
"""Nexus HTTP Server - REST API daemon for Nexus memory system (Phase 2).

Серверный режим роадмапа: один резидентный процесс владеет хранилищем,
а MCP-сервер и прочие потребители ходят к нему по HTTP (localhost + токен).
Демон также крутит фоновые службы: watchkeeper (auto-ingestion) и
авто-закрытие протухших сессий (auto_close_stale_sessions).
"""

import argparse
import json
import logging
import os
import secrets
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.nexus_core import Nexus
from core.ingestion import IngestionPipeline
from core.summarizer import SessionSummarizer
from core.session_hook import SessionHook
from core.semantic import SemanticSearch
from core.watchkeeper import Watchkeeper
from core.autoclose import auto_close_stale_sessions
from core.ocr import extract_text_from_image, ocr_scan_directory, get_ocr_status
from core.web import fetch_web_page, save_to_raw, get_web_status
from core.orchestrator import (start_orchestrator_task, end_orchestrator_task,
                               get_orchestrator_status, list_orchestrator_tasks)
from core.config import config as nexus_config
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("nexus.server")

VERSION = "0.9.8"

PROJECT_ROOT = Path(__file__).resolve().parent
store_path = nexus_config.store_base_dir or PROJECT_ROOT / "nexus_store"
nm = Nexus(base_dir=store_path)
sem = SemanticSearch(nm)
wk = Watchkeeper(nexus=nm, interval=nexus_config.watchkeeper_interval)

app = FastAPI(title="Nexus HTTP API", version=VERSION)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """Token auth: all routes require 'Authorization: Bearer <token>' while
    app.state.token is set. /healthz stays public for liveness checks."""
    if request.url.path == "/healthz":
        return await call_next(request)
    token = getattr(request.app.state, "token", "") or ""
    if token:
        if request.headers.get("Authorization") != f"Bearer {token}":
            return JSONResponse({"status": "error", "message": "unauthorized"},
                                status_code=401)
    return await call_next(request)


def create_app(nexus: Optional[Nexus] = None,
               watcher: Optional[Watchkeeper] = None,
               semantic: Optional[SemanticSearch] = None,
               token: Optional[str] = None) -> FastAPI:
    """Point every /mcp/* endpoint at a concrete store / services.

    Production: main() calls create_app(token=...) with the default globals.
    Tests: create_app(nexus=tmp_nexus) reuses the SAME app but all endpoints
    then work against the temporary store (module globals are reassigned).
    """
    global nm, sem, wk
    if nexus is not None:
        nm = nexus
    if semantic is not None:
        sem = semantic
    if watcher is not None:
        wk = watcher
    app.state.nm = nm
    app.state.sem = sem
    app.state.wk = wk
    app.state.token = token if token is not None else nexus_config.server_token
    return app


async def get_json_body(req: Request) -> dict:
    """Get JSON body from request."""
    body = await req.body()
    return json.loads(body.decode("utf-8"))


@app.api_route("/healthz", methods=["GET", "POST"])
async def healthz():
    """Public liveness endpoint for `nexus server status` (no auth)."""
    return {"status": "ok", "service": "Nexus HTTP API", "version": VERSION,
            "pid": os.getpid()}


@app.post("/mcp/health")
async def health():
    return {"status": "ok", "service": "Nexus HTTP API", "version": VERSION}


@app.post("/mcp/add_note")
async def add_note(req: Request):
    data = await get_json_body(req)
    chunk_id = nm.add_chunk(
        content=data.get("content", ""),
        tags=data.get("tags", []),
        project_id=data.get("project_id"),
        source=data.get("source"),
        agent_id=data.get("agent_id"),
    )
    return {"status": "ok", "id": chunk_id[:8]}


@app.post("/mcp/search_knowledge")
async def search_knowledge(req: Request):
    data = await get_json_body(req)
    results = nm.search(
        query=data.get("query", ""),
        tags=data.get("tags"),
        project_id=data.get("project_id"),
        agent_id=data.get("agent_id"),
    )
    if not results:
        return {"status": "ok", "results": []}
    return {"status": "ok", "results": results}


@app.post("/mcp/get_context")
async def get_context(req: Request):
    data = await get_json_body(req)
    agent_id = data.get("agent_id", "")
    if not agent_id:
        return {"status": "error", "message": "agent_id required"}
    context = nm.get_context_for_agent(
        agent_id=agent_id, project_id=data.get("project_id")
    )
    if not context.strip():
        return {"status": "ok", "context": ""}
    return {"status": "ok", "context": context}


@app.post("/mcp/semantic_search")
async def semantic_search(req: Request):
    data = await get_json_body(req)
    try:
        results = sem.search(
            data.get("query", ""),
            top_k=data.get("top_k", 5),
            tags=data.get("tags"),
            project_id=data.get("project_id"),
            agent_id=data.get("agent_id"),
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "results": results}


@app.post("/mcp/build_context_prompt")
async def build_context_prompt(req: Request):
    data = await get_json_body(req)
    try:
        info = sem.build_prompt_block(
            data.get("query", ""),
            project_id=data.get("project_id"),
            agent_id=data.get("agent_id"),
            max_chunks=data.get("max_chunks", 5),
            max_chars=data.get("max_chars", 1800),
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {
        "status": "ok",
        "text": info["text"],
        "chunks": len(info["chunks"]),
        "chars": info["chars"],
    }


@app.post("/mcp/run_ingestion")
async def run_ingestion(req: Request):
    data = await get_json_body(req)
    pipeline = IngestionPipeline()
    report = pipeline.run()
    max_files = data.get("max_files")
    if max_files:
        report["files"] = report["files"][:max_files]
    return {"status": "ok", "report": report}


@app.post("/mcp/get_ingestion_status")
async def get_ingestion_status():
    """Report recent ingestion history from the ingestion log."""
    log_file = nm.base_dir / "_logs" / "ingestion.jsonl"
    if not log_file.exists():
        return {"status": "ok", "entries": [],
                "message": "No ingestion has been run yet."}
    entries = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError as e:
        return {"status": "error", "message": f"Cannot read ingestion log: {e}"}
    return {"status": "ok", "entries": entries[-10:]}


@app.post("/mcp/start_watchkeeper")
async def start_watchkeeper(req: Request):
    data = await get_json_body(req)
    if wk.is_running:
        return {"status": "ok", "message": "Already running"}
    wk.interval = data.get("interval", 300)
    wk.start()
    return {"status": "ok", "message": f"Started (interval={wk.interval}s)"}


@app.post("/mcp/stop_watchkeeper")
async def stop_watchkeeper():
    if not wk.is_running:
        return {"status": "ok", "message": "Not running"}
    wk.stop()
    return {"status": "ok", "message": "Stopped"}


@app.post("/mcp/watch_status")
async def watch_status():
    return {"status": "ok", "data": wk.status}


@app.post("/mcp/ocr_scan_images")
async def ocr_scan_images(req: Request):
    data = await get_json_body(req)
    report = ocr_scan_directory(
        data.get("directory", ""),
        engine=data.get("engine"),
        languages=data.get("languages"),
    )
    return {"status": "ok", "report": report}


@app.post("/mcp/ocr_extract_image")
async def ocr_extract_image(req: Request):
    data = await get_json_body(req)
    text = extract_text_from_image(
        data.get("image_path", ""),
        engine=data.get("engine"),
        languages=data.get("languages"),
    )
    if not text:
        return {"status": "ok", "text": ""}
    return {"status": "ok", "text": text, "chars": len(text)}


@app.post("/mcp/ocr_status")
async def ocr_status():
    return {"status": "ok", "data": get_ocr_status()}


@app.post("/mcp/config_get")
async def config_get():
    return {"status": "ok", "data": nexus_config.get_status()}


@app.post("/mcp/config_set")
async def config_set(req: Request):
    data = await get_json_body(req)
    section = data.get("section", "")
    key = data.get("key", "")
    value = data.get("value", "")
    if value.lower() in ("null", "none"):
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
    return {"status": "ok", "updated": f"{section}.{key} = {converted}"}


@app.post("/mcp/config_update")
async def config_update(req: Request):
    data = await get_json_body(req)
    nexus_config.update(**data.get("settings", {}))
    nexus_config.save()
    return {"status": "ok", "sections": list(data.get("settings", {}).keys())}


@app.post("/mcp/web_fetch")
async def web_fetch(req: Request):
    data = await get_json_body(req)
    result = fetch_web_page(
        data.get("url", ""),
        timeout=data.get("timeout", 30),
        extract_content=data.get("extract_content", True),
    )
    if not result:
        return {"status": "error", "message": f"Failed: {data.get('url')}"}
    return {"status": "ok", "data": result}


@app.post("/mcp/web_save")
async def web_save(req: Request):
    data = await get_json_body(req)
    result = save_to_raw(
        data.get("url", ""),
        timeout=data.get("timeout", 30),
        project_id=data.get("project_id"),
    )
    if not result:
        return {"status": "error", "message": f"Failed: {data.get('url')}"}
    return {"status": "ok", "data": result}


@app.post("/mcp/web_status")
async def web_status():
    return {"status": "ok", "data": get_web_status()}


@app.post("/mcp/orch_start_task")
async def orch_start_task(req: Request):
    data = await get_json_body(req)
    return {
        "status": "ok",
        "data": start_orchestrator_task(
            task_id=data.get("task_id", ""),
            agent_id=data.get("agent_id", ""),
            project_id=data.get("project_id", ""),
            description=data.get("description", ""),
            model=data.get("model"),
            variant=data.get("variant"),
        ),
    }


@app.post("/mcp/orch_end_task")
async def orch_end_task(req: Request):
    data = await get_json_body(req)
    return {
        "status": "ok",
        "data": end_orchestrator_task(
            task_id=data.get("task_id", ""),
            agent_id=data.get("agent_id", ""),
            session_data=data.get("session_data"),
        ),
    }


@app.post("/mcp/orch_status")
async def orch_status():
    return {"status": "ok", "data": get_orchestrator_status()}


@app.post("/mcp/orch_list_tasks")
async def orch_list_tasks(req: Request):
    data = await get_json_body(req)
    return {
        "status": "ok",
        "data": list_orchestrator_tasks(
            agent_id=data.get("agent_id"),
            project_id=data.get("project_id"),
            status=data.get("status"),
        ),
    }


@app.post("/mcp/archive_current_session")
async def archive_current_session(req: Request):
    data = await get_json_body(req)
    agent_id = data.get("agent_id", "")
    session_data = data.get("session_data", {})
    if not agent_id or not session_data:
        return {"status": "error", "message": "agent_id and session_data required"}
    archived_path = nm.archive_session(
        agent_id=agent_id, session_data=session_data,
        project_id=data.get("project_id"),
    )
    return {"status": "ok", "path": str(archived_path)}


@app.post("/mcp/generate_session_summary")
async def generate_session_summary(req: Request):
    data = await get_json_body(req)
    summarizer = SessionSummarizer(nm)
    info = summarizer.generate_session_summary(
        agent_id=data.get("agent_id", ""),
        session_id=data.get("session_id"),
    )
    if info.get("status") != "ok":
        return {"status": "error", "message": info.get("message", "unknown")}
    return {
        "status": "ok",
        "summary": info.get("summary", ""),
        "archive_file": info.get("archive_file", ""),
    }


@app.post("/mcp/collapse_session_history")
async def collapse_session_history(req: Request):
    data = await get_json_body(req)
    summarizer = SessionSummarizer(nm)
    info = summarizer.collapse_history(
        data.get("agent_id", ""), keep_last=data.get("keep_last", 1)
    )
    if info.get("status") != "ok":
        return {"status": "error", "message": info.get("message", "unknown")}
    return {"status": "ok", "info": info}


@app.post("/mcp/compress_session")
async def compress_session(req: Request):
    data = await get_json_body(req)
    summarizer = SessionSummarizer(nm)
    info = summarizer.compress_session(
        raw_log=data.get("raw_log"),
        agent_id=data.get("agent_id"),
        session_id=data.get("session_id"),
        level=data.get("level", 1),
        max_chars=data.get("max_chars"),
    )
    if info.get("status") not in ("ok",):
        return {"status": "error", "info": info}
    return {"status": "ok", "info": info}


@app.post("/mcp/end_session")
async def end_session(req: Request):
    data = await get_json_body(req)
    hook = SessionHook(nm)
    info = hook.end_session(
        agent_id=data.get("agent_id", ""),
        session_data=data.get("session_data", {}),
        project_id=data.get("project_id"),
        collapse_after=data.get("collapse_after", False),
        keep_last=data.get("keep_last", 1),
    )
    if info.get("status") != "ok":
        return {"status": "error", "message": info.get("message", "unknown")}
    return {"status": "ok", "info": info}


@app.post("/mcp/project_digest")
async def project_digest(req: Request):
    from core.project_digest import ProjectDigest

    data = await get_json_body(req)
    try:
        block = ProjectDigest(nm).get_digest(
            project_id=data.get("project_id", ""),
            since=data.get("since"),
            exclude_agent=data.get("exclude_agent"),
            agent_filter=data.get("agent_filter"),
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "digest": block}


@app.post("/mcp/record_joint_decision")
async def record_joint_decision(req: Request):
    data = await get_json_body(req)
    project_id = data.get("project_id", "")
    decision = data.get("decision", "")
    by_agents = data.get("by_agents", [])
    reason = data.get("reason", "")
    if not all([project_id, decision, by_agents, reason]):
        return {"status": "error", "message": "All fields required"}
    nm.add_joint_decision(
        project_id=project_id,
        decision={
            "decision": decision,
            "by_agents": by_agents,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
        },
    )
    return {"status": "ok"}


@app.post("/mcp/session_autoclose")
async def session_autoclose(req: Request):
    """Run ONE auto-close pass over stale sessions (automation item 2)."""
    data = await get_json_body(req)
    try:
        report = auto_close_stale_sessions(
            nexus=nm, timeout_min=int(data.get("timeout_min", nexus_config.autoclose_minutes))
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "info": report}


def _autoclose_loop(nexus: Nexus, interval_min: int, timeout_min: int,
                    stop_event: threading.Event) -> None:
    """Background loop: periodically close stale sessions."""
    while not stop_event.is_set():
        try:
            report = auto_close_stale_sessions(nexus, timeout_min=timeout_min)
            if report.get("closed"):
                logger.info("autoclose: %s", report["message"])
        except Exception:
            logger.exception("autoclose pass failed")
        stop_event.wait(max(1, interval_min) * 60)


def main() -> None:
    """Run the Nexus daemon (used by `nexus server start`)."""
    parser = argparse.ArgumentParser(description="Nexus HTTP daemon (Phase 2)")
    parser.add_argument("--host", default=None, help="Bind host (default: config server.host)")
    parser.add_argument("--port", type=int, default=None, help="Bind port (default: config server.port)")
    parser.add_argument("--token", default=None,
                        help="Auth token; auto-generated and saved to config if empty")
    parser.add_argument("--pid-file", default=None, help="Where to write the PID")
    parser.add_argument("--log-file", default=None, help="Redirect daemon logs to a file")
    parser.add_argument("--no-autoclose", action="store_true",
                        help="Disable the background session auto-closer")
    args = parser.parse_args()

    host = args.host or nexus_config.server_host
    port = args.port or nexus_config.server_port
    token = args.token if args.token is not None else nexus_config.server_token
    if not token:  # daemon must always be protected in server mode
        token = secrets.token_hex(16)
        nexus_config.set("server", "token", token)
        nexus_config.save()
        logger.info("generated and saved a new server token to %s",
                    nexus_config._config_path)

    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(log_path, "a", encoding="utf-8")
        sys.stdout = sys.stderr

    pid_file = Path(args.pid_file) if args.pid_file else store_path / "_logs" / "server.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    logger.info("pid file: %s (pid=%s)", pid_file, os.getpid())

    create_app(token=token)

    # Фоновые службы
    if nexus_config.watchkeeper_auto_start:
        wk.start()
        logger.info("watchkeeper started (interval=%ss)", wk.interval)

    stop_event = threading.Event()
    if (not args.no_autoclose and nexus_config.autoclose_interval_min > 0):
        threading.Thread(
            target=_autoclose_loop, daemon=True,
            args=(nm, nexus_config.autoclose_interval_min,
                  nexus_config.autoclose_minutes, stop_event),
        ).start()
        logger.info("autoclose loop started (interval=%s min, timeout=%s min)",
                    nexus_config.autoclose_interval_min,
                    nexus_config.autoclose_minutes)

    logger.info("Nexus daemon up on %s:%s (pid=%s)", host, port, os.getpid())
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
