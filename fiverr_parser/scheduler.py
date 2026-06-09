"""Background polling controller.

A single worker thread runs scrape cycles on the configured interval.
Designed for the GUI: start/stop/run-now are all thread-safe, the
interval can change between cycles, and an in-memory ring buffer keeps the
latest log lines for the dashboard to display.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from .config import Settings
from .db import Database
from .scraper import Scraper


class SchedulerController:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._logs: deque[dict[str, str]] = deque(maxlen=300)
        self._scraper = Scraper(db, log_sink=self._on_log)
        self._running = False
        self._last_cycle_at: str | None = None
        self._next_cycle_at: str | None = None
        self._busy = False

    # -- logging -----------------------------------------------------------

    def _on_log(self, level: str, message: str) -> None:
        with self._lock:
            self._logs.append({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "level": level,
                "message": message,
            })

    def logs(self, limit: int = 100) -> list[dict[str, str]]:
        with self._lock:
            return list(self._logs)[-limit:]

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "busy": self._busy,
            "interval_seconds": self.settings.interval_seconds,
            "last_cycle_at": self._last_cycle_at,
            "next_cycle_at": self._next_cycle_at,
            "demo_mode": self.settings.demo_mode,
            "queries": self.settings.queries,
        }

    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="fiverr-scheduler", daemon=True)
        self._thread.start()
        self._on_log("info", "Scheduler started")

    def stop(self) -> None:
        if not self._running:
            return
        self._stop.set()
        self._wake.set()
        self._running = False
        self._on_log("info", "Scheduler stopped")

    def run_now(self) -> None:
        """Trigger a cycle as soon as possible (out of band)."""
        if self._running:
            self._wake.set()
        else:
            # One-shot cycle in a throwaway thread when loop is idle.
            threading.Thread(target=self._run_once, daemon=True).start()

    # -- internals ---------------------------------------------------------

    def _run_once(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            self._scraper.run_cycle(self.settings)
            self._last_cycle_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        finally:
            self._busy = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._run_once()
            interval = max(30, int(self.settings.interval_seconds))
            self._next_cycle_at = datetime.fromtimestamp(
                time.time() + interval, tz=timezone.utc
            ).isoformat(timespec="seconds")
            # Sleep in a wake-able way so interval changes / run-now apply fast.
            self._wake.clear()
            self._wake.wait(timeout=interval)
        self._next_cycle_at = None
