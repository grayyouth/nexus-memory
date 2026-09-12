"""
Tests for core/safe_io.py — atomic writes + cross-process file locks
(Phase 1 of the Nexus autonomous-server roadmap).
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "Nexus"))

from core.safe_io import (
    LockTimeout,
    atomic_write_json,
    atomic_write_text,
    file_lock,
    read_json,
    update_json_file,
)

ROOT = str(Path(__file__).resolve().parent.parent / "Nexus")


class TestAtomicWrites:
    def test_atomic_write_json_roundtrip(self, tmp_path):
        p = tmp_path / "data.json"
        atomic_write_json(p, {"a": 1, "ru": "привет"})
        assert read_json(p) == {"a": 1, "ru": "привет"}

    def test_atomic_write_replaces_existing(self, tmp_path):
        p = tmp_path / "data.json"
        atomic_write_json(p, [1])
        atomic_write_json(p, [1, 2, 3])
        assert read_json(p) == [1, 2, 3]

    def test_atomic_write_text_no_temp_left(self, tmp_path):
        p = tmp_path / "file.txt"
        atomic_write_text(p, "hello\nмир")
        leftovers = [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]
        assert leftovers == []
        assert p.read_text(encoding="utf-8") == "hello\nмир"

    def test_write_makes_parent_dirs(self, tmp_path):
        p = tmp_path / "a" / "b" / "c.json"
        atomic_write_json(p, [1, 2])
        assert read_json(p) == [1, 2]

    def test_read_json_missing_returns_none(self, tmp_path):
        assert read_json(tmp_path / "nope.json") is None

    def test_read_json_corrupt_returns_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert read_json(p) is None


class TestFileLock:
    def test_acquire_release_twice(self, tmp_path):
        target = tmp_path / "index.json"
        with file_lock(target, timeout=2.0):
            pass
        with file_lock(target, timeout=2.0):
            pass

    def test_threads_serialize(self, tmp_path):
        """Threads of one process must serialize (process-local guard)."""
        target = tmp_path / "index.json"
        hold = 0.25

        def worker():
            with file_lock(target, timeout=10.0):
                time.sleep(hold)

        t0 = time.monotonic()
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Serialized: second worker waits for the first to release → >= 2*hold.
        assert time.monotonic() - t0 >= 2 * hold - 0.05

    def test_cross_process_lock_conflict(self, tmp_path):
        """A lock held by ANOTHER process must block the local process."""
        target = tmp_path / "index.json"
        code = (
            "import sys, time\n"
            f"sys.path.insert(0, {ROOT!r})\n"
            f"from core.safe_io import file_lock\n"
            f"with file_lock({str(target)!r}, timeout=10.0):\n"
            "    time.sleep(1.2)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            time.sleep(0.4)  # let the child acquire the lock
            with pytest.raises(LockTimeout):
                with file_lock(target, timeout=0.3, poll=0.02):
                    pass
        finally:
            proc.wait(timeout=10)
            # Lock is released when the child exits → we can acquire again.
            with file_lock(target, timeout=2.0):
                pass

    def test_lockfile_lock_is_created(self, tmp_path):
        target = tmp_path / "index.json"
        with file_lock(target, timeout=2.0):
            pass
        assert (tmp_path / "index.json.lock").exists()


class TestUpdateJsonFile:
    def test_append_default_empty_list(self, tmp_path):
        p = tmp_path / "list.json"

        def add_one(data):
            data = data or []
            data.append(1)
            return data

        update_json_file(p, add_one, empty=[])
        update_json_file(p, add_one, empty=[])
        assert read_json(p) == [1, 1]

    def test_dict_default(self, tmp_path):
        p = tmp_path / "map.json"

        def set_key(data):
            data = data or {}
            data["k"] = "v"
            return data

        update_json_file(p, set_key, empty={})
        assert read_json(p) == {"k": "v"}

    def test_returns_new_data(self, tmp_path):
        p = tmp_path / "list.json"
        updated = update_json_file(p, lambda d: (d or []) + [1], empty=[])
        assert updated == [1]

    def test_concurrent_threads_no_lost_updates(self, tmp_path):
        """6 concurrent increments must result in EXACTLY 6 (no lost data)."""
        p = tmp_path / "count.json"
        errors = []

        def worker():
            try:
                def inc(data):
                    return (data or 0) + 1

                update_json_file(p, inc, empty=0, timeout=15.0)
            except Exception as e:  # pragma: no cover
                errors.append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert read_json(p) == 6