"""
Tests for the MCP server daemon-mode proxy (Phase 2).

Verifies that in server.mode == 'daemon' every tool function is replaced by
a thin HTTP proxy (mocked here), and that nothing happens in 'direct' mode.
The real store/tool registry is restored after each test.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nexus_mcp_server as mcp
from core.config import config as nexus_config


class _FakeClient:
    """Stands in for NexusClient: records calls, returns JSON-able dicts."""

    def __init__(self):
        self.base_url = "http://127.0.0.1:1"
        self.calls = []

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return {"status": "ok", "tool": name, "args": kwargs}


def test_daemon_mode_proxies_all_tools(monkeypatch):
    fake = _FakeClient()
    import core.server_client as sc
    monkeypatch.setattr(sc, "client_from_config", lambda cfg=None: fake)

    tools = mcp.server._tool_manager._tools
    originals = {name: tool.fn for name, tool in tools.items()}
    try:
        nexus_config.set("server", "mode", "daemon")
        url = mcp._install_daemon_proxy()
        assert url == fake.base_url

        tool = tools["search_knowledge"]
        assert getattr(tool.fn, "_nexus_daemon_proxy", False) is True
        assert tool.is_async is True

        # a tool call goes over the fake HTTP client, not to the local store
        raw = asyncio.run(tool.fn(query="q", project_id="P"))
        assert json.loads(raw) == {"status": "ok", "tool": "search_knowledge",
                                   "args": {"query": "q", "project_id": "P"}}
        assert fake.calls[0][0] == "search_knowledge"

        # idempotent re-entry does not double-wrap
        assert mcp._install_daemon_proxy() == fake.base_url
        assert tools["search_knowledge"].fn is tool.fn
    finally:
        nexus_config.set("server", "mode", "direct")
        for name, fn in originals.items():
            tools[name].fn = fn
            tools[name].is_async = True


def test_direct_mode_does_not_touch_tools():
    nexus_config.set("server", "mode", "direct")
    tools = mcp.server._tool_manager._tools
    before = tools["add_note"].fn
    assert mcp._install_daemon_proxy() is None
    assert tools["add_note"].fn is before
    assert getattr(before, "_nexus_daemon_proxy", False) is False


def test_all_mcp_tools_have_matching_daemon_endpoints():
    """Every registered tool must have a /mcp/<name> route on the daemon."""
    import nexus_http_server as srv
    daemon_routes = {
        route.path for route in srv.app.routes
        if getattr(route, "methods", None) and "POST" in route.methods
    }
    for name in mcp.server._tool_manager._tools:
        assert f"/mcp/{name}" in daemon_routes, \
            f"daemon has no endpoint for tool '{name}'"