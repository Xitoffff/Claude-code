"""Orchestrates a single scrape cycle: fetch -> parse -> store.

Each query is recorded as a ``scrape_run`` for observability, and every
failure is contained so one bad query can never take down the loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from . import mock
from .config import Settings
from .db import Database
from .fetcher import Fetcher, FetchError, build_search_url
from .parser import extract_gigs

log = logging.getLogger("fiverr.scraper")

# Callback signature: (level, message) -> None  (used to stream logs to GUI)
LogSink = Callable[[str, str], None]


@dataclass
class CycleResult:
    found: int = 0
    new_gigs: int = 0
    new_sellers: int = 0
    errors: int = 0


class Scraper:
    def __init__(self, db: Database, log_sink: Optional[LogSink] = None):
        self.db = db
        self._log_sink = log_sink

    def _emit(self, level: str, message: str) -> None:
        log.log(getattr(logging, level.upper(), logging.INFO), message)
        if self._log_sink:
            try:
                self._log_sink(level, message)
            except Exception:  # never let logging break scraping
                pass

    def scrape_query(self, query: str, settings: Settings) -> CycleResult:
        run_id = self.db.start_run(query)
        result = CycleResult()
        try:
            if settings.demo_mode:
                gigs = mock.generate(query, settings.max_per_query)
                self._emit("info", f"[demo] '{query}': generated {len(gigs)} listings")
            else:
                url = build_search_url(query)
                self._emit("info", f"'{query}': fetching live…")
                with Fetcher(proxy=settings.proxy) as fetcher:
                    html = fetcher.get(url, on_event=self._emit)
                gigs = extract_gigs(html, query)[: settings.max_per_query]
                self._emit("info", f"'{query}': parsed {len(gigs)} listings")
                if not gigs:
                    self._emit("warn", f"'{query}': no listings parsed (site may have blocked the request)")

            counters = self.db.store_gigs(gigs, query=query)
            result.found = counters["total"]
            result.new_gigs = counters["new_gigs"]
            result.new_sellers = counters["new_sellers"]
            self.db.finish_run(
                run_id, status="ok", found=result.found,
                new_gigs=result.new_gigs, new_sellers=result.new_sellers,
            )
            if result.new_gigs or result.new_sellers:
                self._emit(
                    "info",
                    f"'{query}': +{result.new_gigs} new gigs, "
                    f"+{result.new_sellers} new sellers",
                )
        except FetchError as exc:
            result.errors = 1
            self.db.finish_run(run_id, status="blocked", error=str(exc))
            self._emit("error", f"'{query}': fetch blocked — {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            result.errors = 1
            self.db.finish_run(run_id, status="error", error=str(exc))
            self._emit("error", f"'{query}': unexpected error — {exc}")
        return result

    def run_cycle(self, settings: Settings) -> CycleResult:
        """Scrape every configured query once and aggregate the result."""
        mode = "demo" if settings.demo_mode else "live"
        self._emit("info", f"Cycle started ({mode}): {len(settings.queries)} queries → {settings.queries}")
        total = CycleResult()
        for query in settings.queries:
            r = self.scrape_query(query, settings)
            total.found += r.found
            total.new_gigs += r.new_gigs
            total.new_sellers += r.new_sellers
            total.errors += r.errors
        self._emit(
            "info",
            f"Cycle done: {total.found} seen, +{total.new_gigs} new gigs, "
            f"+{total.new_sellers} new sellers, {total.errors} errors",
        )
        return total
