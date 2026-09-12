"""
Tests for core/watchkeeper.py - Watchkeeper class.
"""
import pytest
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.watchkeeper import Watchkeeper
from core.nexus_core import Nexus
from core.config import DEFAULTS


@pytest.fixture
def default_config(monkeypatch):
    """Isolate tests from the real nexus_config.json (watchkeeper.default_interval
    may have been raised there): force the global singleton to pure defaults."""
    import copy
    from core.config import config as nexus_config
    monkeypatch.setattr(nexus_config, "_data", copy.deepcopy(DEFAULTS))


class TestWatchkeeperInit:
    """Test Watchkeeper initialization."""

    def test_default_init(self, tmp_path, default_config):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        assert not wk.is_running
        assert wk.interval == 300
        assert wk.raw_dir == store / "input_docs" / "raw"
        assert wk._scan_count == 0
        assert wk._total_ingested == 0
        assert wk._total_errors == 0

    def test_custom_interval(self, tmp_path):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store), interval=60)
        assert wk.interval == 60

    def test_custom_raw_dir(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = tmp_path / "custom_raw"
        raw.mkdir()
        wk = Watchkeeper(nexus=Nexus(base_dir=store), raw_dir=raw)
        assert wk.raw_dir == raw


class TestWatchkeeperStatus:
    """Test Watchkeeper status property."""

    def test_status_not_running(self, tmp_path, default_config):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        status = wk.status
        assert status["running"] is False
        assert status["interval_seconds"] == 300
        assert status["scan_count"] == 0
        assert status["total_ingested"] == 0
        assert status["total_errors"] == 0
        assert status["start_time"] is None
        assert status["last_report"] is None

    def test_status_after_run_once(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        # Add a test file
        test_file = raw / "test.md"
        test_file.write_text("# Test\n\nHello world", encoding="utf-8")
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        wk.run_once()
        
        status = wk.status
        assert status["scan_count"] == 1
        assert status["total_ingested"] == 1  # test.md should be ingested
        assert status["last_report"] is not None


class TestWatchkeeperRunOnce:
    """Test Watchkeeper.run_once() method."""

    def test_run_once_empty_raw(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        report = wk.run_once()
        
        assert report.get("ingested", 0) == 0

    def test_run_once_with_markdown(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        test_file = raw / "test.md"
        test_file.write_text("# Test\n\nHello world", encoding="utf-8")
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        report = wk.run_once()
        
        assert report["ingested"] >= 1
        assert report["scanned"] == 1
        assert wk._scan_count == 1

    def test_run_once_with_txt(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        test_file = raw / "test.txt"
        test_file.write_text("Plain text content", encoding="utf-8")
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        report = wk.run_once()
        
        assert report["ingested"] >= 1

    def test_run_once_with_unsupported_format(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        test_file = raw / "test.gif"
        test_file.write_bytes(b"\x47\x49\x46\x38\x39\x61")
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        report = wk.run_once()
        
        assert report["skipped"] >= 1

    def test_run_once_multiple_files(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        (raw / "file1.md").write_text("# File 1", encoding="utf-8")
        (raw / "file2.txt").write_text("File 2", encoding="utf-8")
        (raw / "file3.json").write_text('{"key": "val"}', encoding="utf-8")
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        report = wk.run_once()
        
        assert report["scanned"] == 3
        assert report["ingested"] == 3

    def test_run_once_nonexistent_raw_dir(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw_nonexistent"
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store), raw_dir=raw)
        report = wk.run_once()
        
        assert report["status"] == "skipped"


class TestWatchkeeperLifecycle:
    """Test Watchkeeper start/stop lifecycle."""

    def test_start_stop(self, tmp_path):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store), interval=1)
        
        assert not wk.is_running
        wk.start()
        assert wk.is_running
        
        time.sleep(0.2)  # Let it start running
        wk.stop()
        assert not wk.is_running

    def test_toggle_start(self, tmp_path):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store), interval=1)
        
        msg = wk.toggle()
        assert wk.is_running
        assert "started" in msg.lower()
        
        wk.stop()

    def test_toggle_stop(self, tmp_path):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store), interval=1)
        
        wk.start()
        assert wk.is_running
        
        msg = wk.toggle()
        assert not wk.is_running
        assert "stopped" in msg.lower()

    def test_start_already_running(self, tmp_path):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store), interval=1)
        
        wk.start()
        # Second start should be a no-op
        wk.start()
        assert wk.is_running
        wk.stop()

    def test_stop_not_running(self, tmp_path):
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        
        # Stop when not running should be a no-op
        wk.stop()
        assert not wk.is_running


class TestWatchkeeperLogging:
    """Test Watchkeeper logging."""

    def test_log_file_created(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        wk.run_once()
        
        log_file = store / "_logs" / "watchkeeper.jsonl"
        assert log_file.exists()
        
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) > 0
        
        import json
        entry = json.loads(lines[-1])
        assert "ts" in entry
        assert "level" in entry
        assert "msg" in entry

    def test_log_info_on_ingest(self, tmp_path):
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        (raw / "test.md").write_text("# Test", encoding="utf-8")
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        wk.run_once()
        
        log_file = store / "_logs" / "watchkeeper.jsonl"
        content = log_file.read_text(encoding="utf-8")
        assert "ingested" in content


class TestWatchkeeperThreadSafety:
    """Test Watchkeeper thread safety."""

    def test_run_once_thread_safe(self, tmp_path):
        """Multiple run_once calls should not corrupt state."""
        store = tmp_path / "nexus_store"
        raw = store / "input_docs" / "raw"
        raw.mkdir(parents=True)
        
        wk = Watchkeeper(nexus=Nexus(base_dir=store))
        
        # Run multiple times
        for _ in range(5):
            (raw / f"file_{_}.md").write_text(f"# File {_}", encoding="utf-8")
            wk.run_once()
        
        assert wk._scan_count == 5
        assert wk._total_ingested == 5

    def test_start_stop_thread_safety(self, tmp_path):
        """Start/stop should not raise exceptions."""
        store = tmp_path / "nexus_store"
        wk = Watchkeeper(nexus=Nexus(base_dir=store), interval=1)
        
        wk.start()
        time.sleep(0.1)
        wk.stop()
        
        # Should not raise
        assert not wk.is_running
