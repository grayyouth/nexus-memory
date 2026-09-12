"""
Tests for core.server_client (Phase 2: the thin HTTP client for the daemon).

Uses a stdlib ThreadingHTTPServer mock so no real Nexus daemon is needed:
the mock checks the Bearer token, echoes tool name + args back as JSON.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.server_client import NexusClient, NexusClientError, client_from_config


class _MockDaemonHandler(BaseHTTPRequestHandler):
    TOKEN = "secret-token"

    def log_message(self, *args):  # keep test output clean
        pass

    def _reply(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/healthz":
            self._reply(200, {"status": "ok", "service": "mock-daemon"})
            return
        if not self.path.startswith("/mcp/"):
            self._reply(404, {"message": "no route"})
            return
        if self.headers.get("Authorization") != f"Bearer {self.TOKEN}":
            self._reply(401, {"message": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", 0))
        args = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        name = self.path[len("/mcp/"):]
        self._reply(200, {"status": "ok", "tool": name, "args": args})

    def do_GET(self):
        if self.path == "/healthz":  # health() pings via GET
            self._reply(200, {"status": "ok", "service": "mock-daemon"})
            return
        self._reply(404, {"message": "no route"})


@pytest.fixture
def mock_daemon_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockDaemonHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def dead_port():
    """A port that was briefly bound and released -> nothing listens there."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockDaemonHandler)
    port = server.server_address[1]
    server.server_close()
    return port


def test_health_ok(mock_daemon_url):
    client = NexusClient(base_url=mock_daemon_url, token="secret-token")
    assert client.health()["status"] == "ok"


def test_call_sends_token_and_body(mock_daemon_url):
    client = NexusClient(base_url=mock_daemon_url, token="secret-token")
    res = client.call("add_note", content="x", tags=["#t"])
    assert res["tool"] == "add_note"
    assert res["args"]["content"] == "x"
    assert res["args"]["tags"] == ["#t"]


def test_call_without_token_is_rejected(mock_daemon_url):
    client = NexusClient(base_url=mock_daemon_url, token=None)
    with pytest.raises(NexusClientError) as exc:
        client.call("add_note", content="x")
    assert "401" in str(exc.value)


def test_call_with_wrong_token_is_rejected(mock_daemon_url):
    client = NexusClient(base_url=mock_daemon_url, token="wrong")
    with pytest.raises(NexusClientError) as exc:
        client.search("q")
    assert "401" in str(exc.value)


def test_unreachable_daemon_raises(dead_port):
    client = NexusClient(base_url=f"http://127.0.0.1:{dead_port}",
                         token="t", timeout=0.5)
    with pytest.raises(NexusClientError) as exc:
        client.call("health")
    assert "unreachable" in str(exc.value).lower()


def test_typed_wrappers_map_to_endpoint_names(mock_daemon_url):
    client = NexusClient(base_url=mock_daemon_url, token="secret-token")
    assert client.search("q")["tool"] == "search_knowledge"
    assert client.semantic_search("q")["tool"] == "semantic_search"
    assert client.archive_session("a", {"x": 1})["tool"] == "archive_session"
    assert client.end_session("a", {})[ "tool"] == "end_session"
    assert client.generate_session_summary("a")["tool"] == "generate_session_summary"
    assert client.collapse_session_history("a")["tool"] == "collapse_session_history"
    assert client.run_ingestion()["tool"] == "run_ingestion"
    assert client.autoclose()["tool"] == "session_autoclose"
    assert client.project_digest("P")["tool"] == "project_digest"
    assert client.record_joint_decision("P", "d", ["a"])["tool"] == "record_joint_decision"
    assert client.watch_status()["tool"] == "watch_status"
    assert client.start_watchkeeper()["tool"] == "start_watchkeeper"
    assert client.stop_watchkeeper()["tool"] == "stop_watchkeeper"
    assert client.config_get()["tool"] == "config_get"


def test_client_from_config_reads_host_port_token(mock_daemon_url):
    parsed = urlparse(mock_daemon_url)

    class _Cfg:
        server_host = parsed.hostname
        server_port = parsed.port
        server_token = "secret-token"

    client = client_from_config(_Cfg())
    assert client.base_url == mock_daemon_url
    assert client.health()["status"] == "ok"