"""
Nexus HTTP Client — тонкий клиент для демона Nexus (Фаза 2 роадмапа).

В режиме «daemon» MCP-сервер и внешние потребители не трогают хранилище
напрямую — все операции уходят на FastAPI-демон по HTTP (localhost + токен).
Это даёт: вытеснение гонок записи в один процесс, тёплые кэши, watchkeeper /
OCR / автозакрытие сессий как фоновая служба.

Клиент использует только stdlib (urllib.request) — без новых зависимостей,
по аналогии с fallback в core/web.py.

Usage:
    from core.server_client import NexusClient, client_from_config

    client = client_from_config()          # из nexus_config.json → server.*
    client.health()                        # -> dict
    client.call("add_note", content="...", tags=["#x"])   # generic
    client.search("как настроить", project_id="P")         # typed wrapper
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from core.config import config as nexus_config

logger = logging.getLogger(__name__)

__all__ = ["NexusClient", "NexusClientError", "client_from_config"]


class NexusClientError(RuntimeError):
    """Raised when the daemon is unreachable or rejects the request."""


class NexusClient:
    """Minimal HTTP client for the Nexus daemon (/mcp/* endpoints)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or nexus_config.server_url).rstrip("/")
        self.token = token if token is not None else nexus_config.server_token
        self.timeout = timeout

    # --- low level ---

    def _request(self, path: str, payload: Optional[dict] = None,
                 timeout: Optional[float] = None) -> Dict[str, Any]:
        """POST JSON to /mcp/<path> and return the parsed JSON body."""
        url = f"{self.base_url}/mcp/{path}"
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:
                body = {"message": str(e)}
            raise NexusClientError(
                f"HTTP {e.code} from {url}: {body.get('message', body)}"
            ) from e
        except (urllib.error.URLError, OSError) as e:
            raise NexusClientError(f"Daemon unreachable at {url}: {e}") from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise NexusClientError(f"Invalid JSON from {url}: {raw[:200]}") from e

    def health(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Ping the public /healthz endpoint (no auth needed)."""
        url = f"{self.base_url}/healthz"
        try:
            with urllib.request.urlopen(url, timeout=timeout or (self.timeout * 0.5)) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise NexusClientError(f"Daemon not running at {self.base_url}: {e}") from e

    # --- generic + typed calls ---

    def call(self, name: str, **kwargs: Any) -> Dict[str, Any]:
        """Generic proxy: POST /mcp/<name> with kwargs as JSON body.

        Returns the raw result dict the daemon returned.
        """
        return self._request(name, dict(kwargs))

    def add_note(self, content: str, tags: List[str],
                 project_id: Optional[str] = None, source: Optional[str] = None,
                 agent_id: Optional[str] = None) -> Dict[str, Any]:
        return self.call("add_note", content=content, tags=tags,
                         project_id=project_id, source=source, agent_id=agent_id)

    def search(self, query: str, tags: Optional[List[str]] = None,
               project_id: Optional[str] = None, agent_id: Optional[str] = None):
        return self.call("search_knowledge", query=query, tags=tags,
                         project_id=project_id, agent_id=agent_id)

    def semantic_search(self, query: str, top_k: int = 5,
                        tags: Optional[List[str]] = None,
                        project_id: Optional[str] = None,
                        agent_id: Optional[str] = None):
        return self.call("semantic_search", query=query, top_k=top_k, tags=tags,
                         project_id=project_id, agent_id=agent_id)

    def get_context(self, agent_id: str, project_id: Optional[str] = None):
        return self.call("get_context", agent_id=agent_id, project_id=project_id)

    def build_context_prompt(self, query: str, project_id: Optional[str] = None,
                             agent_id: Optional[str] = None, max_chunks: int = 5,
                             max_chars: int = 1800):
        return self.call("build_context_prompt", query=query, project_id=project_id,
                         agent_id=agent_id, max_chunks=max_chunks, max_chars=max_chars)

    def run_ingestion(self, max_files: Optional[int] = None):
        return self.call("run_ingestion", max_files=max_files)

    def archive_session(self, agent_id: str, session_data: Any,
                        project_id: Optional[str] = None):
        return self.call("archive_session", agent_id=agent_id,
                         session_data=session_data, project_id=project_id)

    def end_session(self, agent_id: str, session_data: dict,
                    project_id: Optional[str] = None, collapse_after: bool = False,
                    keep_last: int = 1):
        return self.call("end_session", agent_id=agent_id, session_data=session_data,
                         project_id=project_id, collapse_after=collapse_after,
                         keep_last=keep_last)

    def generate_session_summary(self, agent_id: str, session_id: Optional[str] = None):
        return self.call("generate_session_summary", agent_id=agent_id,
                         session_id=session_id)

    def collapse_session_history(self, agent_id: str, keep_last: int = 1):
        return self.call("collapse_session_history", agent_id=agent_id,
                         keep_last=keep_last)

    def compress_session(self, agent_id: Optional[str] = None,
                         session_id: Optional[str] = None,
                         raw_log: Optional[dict] = None, level: int = 1,
                         max_chars: Optional[int] = None):
        return self.call("compress_session", agent_id=agent_id, session_id=session_id,
                         raw_log=raw_log, level=level, max_chars=max_chars)

    def project_digest(self, project_id: str, since: Any = None,
                       exclude_agent: Optional[str] = None,
                       agent_filter: Optional[str] = None):
        return self.call("project_digest", project_id=project_id, since=since,
                         exclude_agent=exclude_agent, agent_filter=agent_filter)

    def record_joint_decision(self, project_id: str, decision: str,
                              by_agents: List[str], reason: str = ""):
        return self.call("record_joint_decision", project_id=project_id,
                         decision=decision, by_agents=by_agents, reason=reason)

    def watch_status(self):
        return self.call("watch_status")

    def start_watchkeeper(self, interval: int = 300):
        return self.call("start_watchkeeper", interval=interval)

    def stop_watchkeeper(self):
        return self.call("stop_watchkeeper")

    def autoclose(self, timeout_min: int = 360):
        return self.call("session_autoclose", timeout_min=timeout_min)

    def config_get(self):
        return self.call("config_get")


def client_from_config(cfg=None) -> NexusClient:
    """Build a NexusClient from config (server.host/port/token) or a passed cfg."""
    cfg = cfg or nexus_config
    return NexusClient(
        base_url=f"http://{cfg.server_host}:{cfg.server_port}",
        token=cfg.server_token,
    )