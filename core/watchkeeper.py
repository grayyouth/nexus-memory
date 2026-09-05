"""
Nexus Watchkeeper — автоматический мониторинг input_docs/raw/.

Сканирует директорию `input_docs/raw/` каждые N секунд и автоматически
запускает IngestionPipeline для новых файлов. Работает в фоновом режиме
и может управляться через MCP-инструменты или CLI.

Использование:
    # Python API
    from core.watchkeeper import Watchkeeper
    wk = Watchkeeper()
    wk.start()          # запустить мониторинг (блокирует)
    wk.stop()           # остановить
    wk.run_once()       # один скан (не блокирует)

    # CLI
    python nexus_cli.py watch --interval 300

    # MCP
    start_watchkeeper(interval=300)
    stop_watchkeeper()
    watch_status()
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.nexus_core import Nexus
from core.ingestion import IngestionPipeline
from core.config import config

logger = logging.getLogger(__name__)


class Watchkeeper:
    """Periodically scans input_docs/raw/ and runs IngestionPipeline."""

    def __init__(
        self,
        nexus: Optional[Nexus] = None,
        interval: Optional[int] = None,
        raw_dir: Optional[Path] = None,
    ) -> None:
        self.nm = nexus or Nexus()
        self.interval = interval if interval is not None else config.watchkeeper_interval
        self.raw_dir = Path(raw_dir) if raw_dir else self.nm.input_raw_dir
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._scan_count = 0
        self._total_ingested = 0
        self._total_errors = 0
        self._last_report: Optional[Dict[str, Any]] = None
        self._start_time: Optional[str] = None
        self._log_file = self.nm.base_dir / "_logs" / "watchkeeper.jsonl"

    # --- State ---

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "interval_seconds": self.interval,
            "raw_dir": str(self.raw_dir),
            "scan_count": self._scan_count,
            "total_ingested": self._total_ingested,
            "total_errors": self._total_errors,
            "start_time": self._start_time,
            "last_report": self._last_report,
        }

    # --- Logging ---

    def _log(self, level: str, msg: str, **ctx) -> None:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "msg": msg,
            **ctx,
        }
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # --- Core scan ---

    def run_once(self) -> Dict[str, Any]:
        """Run a single scan of raw_dir and ingest new files.

        Returns a report dict with summary statistics.
        """
        if not self.raw_dir.exists():
            self._log("warning", "raw_dir does not exist", path=str(self.raw_dir))
            return {"status": "skipped", "reason": "raw_dir not found"}

        pipeline = IngestionPipeline(nexus=self.nm, raw_dir=self.raw_dir)
        report = pipeline.run()

        self._scan_count += 1
        ingested = report.get("ingested", 0)
        errors = report.get("errors", 0)
        self._total_ingested += ingested
        self._total_errors += errors
        self._last_report = report

        if ingested:
            self._log("info", f"ingested {ingested} files in scan #{self._scan_count}",
                       ingested=ingested, errors=errors)
        if errors:
            self._log("error", f"{errors} errors in scan #{self._scan_count}")
        if not ingested and not errors:
            self._log("debug", f"scan #{self._scan_count}: nothing new")

        return report

    # --- Lifecycle ---

    def start(self) -> None:
        """Start the periodic monitoring loop (runs in a background thread)."""
        with self._lock:
            if self._running:
                self._log("warning", "watchkeeper already running")
                return
            self._running = True
            self._start_time = datetime.now().isoformat()

        def _loop() -> None:
            while self._running:
                try:
                    self.run_once()
                except Exception as e:
                    self._log("error", f"scan error: {e}")
                # Sleep in small increments so stop() can interrupt quickly.
                for _ in range(self.interval * 10):
                    if not self._running:
                        break
                    time.sleep(0.1)

        self._thread = threading.Thread(target=_loop, daemon=True, name="nexus-watchkeeper")
        self._thread.start()
        self._log("info", f"watchkeeper started (interval={self.interval}s)")

    def stop(self) -> None:
        """Stop the periodic monitoring loop."""
        with self._lock:
            if not self._running:
                return
            self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._log("info", "watchkeeper stopped")

    def toggle(self) -> str:
        """Start if stopped, stop if running. Returns status message."""
        if self._running:
            self.stop()
            return "Watchkeeper stopped."
        else:
            self.start()
            return f"Watchkeeper started (interval={self.interval}s)."

    # --- CLI helper ---

    def run_forever(self) -> None:
        """Run the monitoring loop in the main thread (blocks).

        Handles SIGINT/SIGTERM for clean shutdown.
        """
        self._start_time = datetime.now().isoformat()
        self._running = True
        self._log("info", f"watchkeeper running in foreground (interval={self.interval}s)")

        def _signal_handler(signum: int, _frame: Any) -> None:
            self._running = False

        if sys.platform != "win32":
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)

        try:
            while self._running:
                try:
                    self.run_once()
                except Exception as e:
                    self._log("error", f"scan error: {e}")
                for _ in range(self.interval * 10):
                    if not self._running:
                        break
                    time.sleep(0.1)
        except KeyboardInterrupt:
            self._running = False

        self.stop()
        print(f"\nWatchkeeper stopped. Total: {self._scan_count} scans, "
              f"{self._total_ingested} ingested, {self._total_errors} errors.")
