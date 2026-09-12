"""
Tests for the Nexus HTTP daemon (nexus_http_server.py, Phase 2).

Verifies: public /healthz, token middleware on /mcp/*, and that endpoints
operate on the store passed to create_app() (not the real project store).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import nexus_http_server as srv

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def daemon_client(nm):
    """TestClient pointed at a tmp store, token auth enabled."""
    originals = (srv.nm, srv.sem, srv.wk)
    srv.create_app(nexus=nm, token="test-token")
    yield TestClient(srv.app)
    # restore the real project store + no-token state
    srv.create_app(nexus=originals[0], semantic=originals[1],
                   watcher=originals[2], token=None)


def test_healthz_is_public(daemon_client):
    # liveness checks use both GET (CLI, NexusClient) and POST
    r = daemon_client.get("/healthz")
    assert r.status_code == 200
    assert daemon_client.post("/healthz").status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "pid" in body and "version" in body


def test_mcp_endpoints_require_bearer_token(daemon_client):
    assert daemon_client.post("/mcp/health").status_code == 401
    assert daemon_client.post("/mcp/health",
                              headers={"Authorization": "Bearer wrong"}
                              ).status_code == 401
    assert daemon_client.post("/mcp/health", headers=AUTH).status_code == 200


def test_all_mcp_routes_blocked_without_token(daemon_client):
    for path in ("/mcp/add_note", "/mcp/search_knowledge", "/mcp/end_session",
                 "/mcp/config_get", "/mcp/orch_status", "/mcp/get_ingestion_status",
                 "/mcp/session_autoclose"):
        assert daemon_client.post(path).status_code == 401, path


def test_add_note_writes_to_the_passed_store(daemon_client, nm):
    r = daemon_client.post("/mcp/add_note", headers=AUTH,
                           json={"content": "hello nexus daemon",
                                 "tags": ["#test"], "agent_id": "pytest"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    hits = nm.search("hello nexus daemon")
    assert hits, "chunk must land in the tmp store"


def test_get_ingestion_status_endpoint(daemon_client):
    r = daemon_client.post("/mcp/get_ingestion_status", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["entries"] == []  # tmp store: nothing ingested yet


def test_session_autoclose_endpoint(daemon_client):
    r = daemon_client.post("/mcp/session_autoclose", headers=AUTH, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["info"]["status"] == "ok"
    assert body["info"]["closed"] == 0


def test_create_app_is_idempotent_for_globals(nm):
    srv.create_app(nexus=nm, token="t1")
    assert srv.app.state.token == "t1"
    srv.create_app(token="t2")
    assert srv.app.state.token == "t2"
    srv.create_app(nexus=None)  # no args -> globals untouched
    assert srv.nm is nm