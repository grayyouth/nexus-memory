"""
Nexus Safe I/O — atomic writes + cross-process file locks.

Phase 1 of the "autonomous server" roadmap (Nexus chunk ``e5c24fc8``):
every MCP process used to perform read-modify-write on ``index.json`` with
NO locks, so two concurrently running agents could silently lose each
other's data. This module is the foundation fix and works independently of
any daemon:

- ``atomic_write_json`` / ``atomic_write_text`` — write to a temp file in
  the same directory, then ``os.replace()``. A crash or a concurrent reader
  never sees a half-written file: it sees either the old or the new content.
- ``file_lock`` — a cross-platform advisory lock (``msvcrt`` on Windows,
  ``fcntl`` on POSIX, lock-file fallback with stale-lock cleanup) so
  read-modify-write cycles from different processes serialize.
- ``update_json_file`` — the canonical safe "read → mutate → write" cycle
  used by ``Nexus._update_json()``.

All functions are standalone (no import of ``core.nexus_core``) so the
module can be reused by any component that persists JSON state.

Usage:
    from core.safe_io import atomic_write_json, update_json_file

    atomic_write_json(path, {"hello": "world"})

    def add_task(data):
        data = data or []
        data.append({"id": 1})
        return data

    update_json_file(path, add_task, empty=[])
"""

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    if os.name == "nt":
        import msvcrt as _msvcrt
        _LOCK_BACKEND = "msvcrt"
    else:
        import fcntl as _fcntl
        _LOCK_BACKEND = "fcntl"
except ImportError:  # pragma: no cover — exotic platform
    _msvcrt = None
    _fcntl = None
    _LOCK_BACKEND = "lockfile"


class LockTimeout(TimeoutError):
    """Raised when a file lock cannot be acquired within ``timeout`` seconds."""


# ---------------------------------------------------------------------------
# Atomic writes (temp + rename)
# ---------------------------------------------------------------------------

def atomic_write_text(path: Any, text: str) -> None:
    """Write ``text`` atomically: temp file in the same directory, then
    ``os.replace``. Guarantees readers never observe partial content."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Any, data: Any) -> None:
    """Write JSON-encodable ``data`` atomically (indent=2, UTF-8, LF)."""
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(path, text)


def read_json(path: Any) -> Any:
    """Read a JSON file; ``None`` when missing or corrupt (never raises)."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# File locks
# ---------------------------------------------------------------------------

def _lock_path(target: Path) -> Path:
    """Sidecar lock file next to the target (never touches the data file)."""
    target = Path(target)
    return target.parent / (target.name + ".lock")


# --- Process-local (thread) locks per lock file ---
# OS advisory locks (msvcrt/fcntl) are per-PROCESS: they serialize different
# processes but NOT threads of the same process. Since a process may run
# several workers (threads) against the same index, each lock file also has
# a threading.Lock so same-process threads serialize as well.
_LOCAL_LOCKS: dict = {}
_LOCAL_GUARD = threading.Lock()


def _local_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCAL_GUARD:
        if key not in _LOCAL_LOCKS:
            _LOCAL_LOCKS[key] = threading.Lock()
        return _LOCAL_LOCKS[key]


def _ensure_lock_file(lock_file: Path) -> None:
    """msvcrt cannot lock bytes of an empty file; seed it with one byte."""
    if not lock_file.exists() or lock_file.stat().st_size < 1:
        with open(lock_file, "wb") as f:
            f.write(b"\x00")


def _acquire_os_lock(lock_file: Path, deadline: float, poll: float) -> int:
    """Non-blocking OS advisory lock with a deadline (msvcrt / fcntl)."""
    while True:
        fd = os.open(lock_file, os.O_RDWR)
        try:
            if _LOCK_BACKEND == "msvcrt":
                os.lseek(fd, 0, os.SEEK_SET)
                _msvcrt.locking(fd, _msvcrt.LK_NBLCK, 1)
            else:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return fd
        except OSError:
            os.close(fd)
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Could not acquire lock on {lock_file} "
                    f"within the given timeout"
                )
            time.sleep(poll)


def _acquire_lockfile_lock(
    lock_file: Path, deadline: float, poll: float, stale_after: float
) -> int:
    """Fallback lock: O_CREAT|O_EXCL token file with stale-lock cleanup."""
    while True:
        try:
            return os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                if time.time() - lock_file.stat().st_mtime > stale_after:
                    try:
                        os.unlink(lock_file)
                    except OSError:
                        pass
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise LockTimeout(
                    f"Could not acquire lock on {lock_file} "
                    f"within the given timeout"
                )
            time.sleep(poll)


def _release_lock(fd: int, lock_file: Path, backend: str) -> None:
    """Release an acquired lock (and clean up the token file for fallback)."""
    try:
        if backend == "msvcrt":
            os.lseek(fd, 0, os.SEEK_SET)
            _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        elif backend == "fcntl":
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        else:
            os.unlink(lock_file)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextmanager
def file_lock(
    target: Any,
    timeout: float = 10.0,
    poll: float = 0.05,
    stale_after: float = 120.0,
) -> Iterator[None]:
    """Cross-platform advisory lock for ``target`` (a file path).

    The lock is advisory and process-scoped: it serializes writers that use
    this API and is automatically released if the holding process dies.

    Args:
        target: Any file path whose writes need serializing.
        timeout: Seconds to wait before raising LockTimeout.
        poll: Retry interval while waiting for the lock.
        stale_after: Seconds after which a stale fallback lock token is
            considered dead and reclaimed (ignored by msvcrt/fcntl).
    """
    lock_file = _lock_path(Path(target))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, timeout)

    # 1) Process-local guard: serializes threads of THIS process (OS locks
    #    are per-process, so they would not stop same-process races).
    local = _local_lock(lock_file)
    if not local.acquire(timeout=max(0.0, timeout)):
        raise LockTimeout(
            f"Could not acquire lock on {lock_file} "
            f"within the given timeout"
        )
    try:
        # 2) OS-level lock: serializes DIFFERENT processes.
        _ensure_lock_file(lock_file)
        if _LOCK_BACKEND == "lockfile":
            fd = _acquire_lockfile_lock(lock_file, deadline, poll, stale_after)
            backend = "lockfile"
        else:
            fd = _acquire_os_lock(lock_file, deadline, poll)
            backend = _LOCK_BACKEND

        try:
            yield
        finally:
            _release_lock(fd, lock_file, backend)
    finally:
        local.release()


# ---------------------------------------------------------------------------
# Locked read-modify-write cycle
# ---------------------------------------------------------------------------

def update_json_file(
    path: Any,
    mutator: Callable[[Any], Any],
    empty: Any = None,
    timeout: float = 10.0,
) -> Any:
    """Safe "read → mutate → write" JSON cycle under a file lock.

    Reads the current content (or ``empty`` when the file is missing or
    corrupt), applies ``mutator(data)`` and atomically persists the result.
    Returns the new data. Raises ``LockTimeout`` when the lock cannot be
    acquired within ``timeout`` seconds.
    """
    path = Path(path)
    with file_lock(path, timeout=timeout):
        data = read_json(path)
        if data is None:
            data = empty
        new_data = mutator(data)
        atomic_write_json(path, new_data)
        return new_data