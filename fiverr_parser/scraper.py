"""Orchestrates a single scrape cycle: fetch -> parse -> store.

Each query is recorded as a ``scrape_run`` for observability, and every
failure is contained so one bad query can never take down the loop.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
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

    def _fetch_query_pages(self, query: str, pages: int, settings: Settings):
        """Fetch and parse N result pages of a query in parallel.

        Each page uses its own Fetcher (curl_cffi sessions are per-thread),
        so a rotating proxy gives every page a fresh exit IP. Results are
        merged and de-duplicated by gig id.
        """
        from .models import Gig  # local import to avoid cycle at top

        def one_page(page: int) -> list[Gig]:
            url = build_search_url(query, page=page)
            with Fetcher(proxy=settings.proxy) as fetcher:
                html = fetcher.get(url, on_event=self._emit)
            return extract_gigs(html, query)

        if pages == 1:
            return one_page(1)

        merged: dict[str, Gig] = {}
        errors: list[Exception] = []
        workers = min(pages, max(1, settings.concurrency))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fut in [ex.submit(one_page, p) for p in range(1, pages + 1)]:
                try:
                    for g in fut.result():
                        merged.setdefault(g.gig_id, g)
                except Exception as exc:  # one bad page must not sink the rest
                    errors.append(exc)
        if errors and not merged:
            raise errors[0]
        return list(merged.values())

    def scrape_query(self, query: str, settings: Settings) -> CycleResult:
        run_id = self.db.start_run(query)
        result = CycleResult()
        try:
            if settings.demo_mode:
                gigs = mock.generate(query, settings.max_per_query)
                self._emit("info", f"[demo] '{query}': generated {len(gigs)} listings")
            else:
                pages = max(1, settings.pages_per_query)
                self._emit("info", f"'{query}': fetching live ({pages} page(s))…")
                gigs = self._fetch_query_pages(query, pages, settings)
                gigs = gigs[: settings.max_per_query]
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
        """Scrape every configured query once, running queries in parallel."""
        import time

        mode = "demo" if settings.demo_mode else "live"
        queries = settings.queries
        workers = max(1, min(settings.concurrency, len(queries)))
        self._emit(
            "info",
            f"Cycle started ({mode}): {len(queries)} queries, "
            f"{workers} parallel workers → {queries}",
        )
        started = time.monotonic()
        total = CycleResult()
        # Demo generation is CPU-trivial; run it inline. Live fetches are
        # I/O-bound and benefit from running concurrently.
        if workers == 1 or len(queries) == 1:
            results = [self.scrape_query(q, settings) for q in queries]
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                results = list(ex.map(lambda q: self.scrape_query(q, settings), queries))
        for r in results:
            total.found += r.found
            total.new_gigs += r.new_gigs
            total.new_sellers += r.new_sellers
            total.errors += r.errors
        elapsed = time.monotonic() - started
        self._emit(
            "info",
            f"Cycle done in {elapsed:.1f}s: {total.found} seen, "
            f"+{total.new_gigs} new gigs, +{total.new_sellers} new sellers, "
            f"{total.errors} errors",
        )
        return total
