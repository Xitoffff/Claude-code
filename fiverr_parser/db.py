"""SQLite storage layer.

Design goals (per the brief): speed, stability and seller deduplication.

* WAL journal mode + tuned PRAGMAs for fast, concurrent-friendly writes.
* Natural unique keys (``sellers.username``, ``gigs.gig_id``) backed by
  UNIQUE indexes so dedup happens in the database via UPSERT.
* A short-lived connection per operation guarded by a lock, which keeps
  the module safe to call from the API thread and the scheduler thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from .models import Gig, Seller


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS sellers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL,
    display_name  TEXT DEFAULT '',
    level         TEXT DEFAULT '',
    country       TEXT DEFAULT '',
    avatar_url    TEXT DEFAULT '',
    profile_url   TEXT DEFAULT '',
    is_pro        INTEGER DEFAULT 0,
    rating        REAL DEFAULT 0,
    reviews_count INTEGER DEFAULT 0,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    seen_count    INTEGER DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sellers_username ON sellers(username);

CREATE TABLE IF NOT EXISTS gigs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    gig_id        TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT DEFAULT '',
    slug          TEXT DEFAULT '',
    category      TEXT DEFAULT '',
    query         TEXT DEFAULT '',
    price_cents   INTEGER DEFAULT 0,
    currency      TEXT DEFAULT 'USD',
    rating        REAL DEFAULT 0,
    reviews_count INTEGER DEFAULT 0,
    image_url     TEXT DEFAULT '',
    is_new        INTEGER DEFAULT 0,
    seller_id     INTEGER REFERENCES sellers(id) ON DELETE SET NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_gigs_gigid ON gigs(gig_id);
CREATE INDEX IF NOT EXISTS idx_gigs_first_seen ON gigs(first_seen);
CREATE INDEX IF NOT EXISTS idx_gigs_query ON gigs(query);
CREATE INDEX IF NOT EXISTS idx_gigs_seller ON gigs(seller_id);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT DEFAULT 'running',
    found        INTEGER DEFAULT 0,
    new_gigs     INTEGER DEFAULT 0,
    new_sellers  INTEGER DEFAULT 0,
    error        TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON scrape_runs(started_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """Thread-safe SQLite wrapper used by the whole application."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        # Performance + stability pragmas.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA temp_store=MEMORY")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)

    # -- sellers -----------------------------------------------------------

    def upsert_seller(self, conn: sqlite3.Connection, seller: Seller) -> tuple[int, bool]:
        """Insert or update a seller. Returns ``(seller_id, is_new)``.

        Dedup is enforced by the UNIQUE index on ``username``; an
        ``ON CONFLICT`` upsert refreshes the mutable profile fields and
        bumps ``last_seen`` / ``seen_count`` without creating duplicates.
        """
        username = seller.normalized_username()
        now = _now()
        cur = conn.execute("SELECT id FROM sellers WHERE username = ?", (username,))
        row = cur.fetchone()
        is_new = row is None
        conn.execute(
            """
            INSERT INTO sellers
                (username, display_name, level, country, avatar_url,
                 profile_url, is_pro, rating, reviews_count,
                 first_seen, last_seen, seen_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(username) DO UPDATE SET
                display_name  = excluded.display_name,
                level         = excluded.level,
                country       = excluded.country,
                avatar_url    = excluded.avatar_url,
                profile_url   = excluded.profile_url,
                is_pro        = excluded.is_pro,
                rating        = excluded.rating,
                reviews_count = excluded.reviews_count,
                last_seen     = excluded.last_seen,
                seen_count    = sellers.seen_count + 1
            """,
            (
                username, seller.display_name, seller.level, seller.country,
                seller.avatar_url, seller.profile_url, int(seller.is_pro),
                seller.rating, seller.reviews_count, now, now,
            ),
        )
        sid = conn.execute(
            "SELECT id FROM sellers WHERE username = ?", (username,)
        ).fetchone()["id"]
        return sid, is_new

    # -- gigs --------------------------------------------------------------

    def upsert_gig(self, conn: sqlite3.Connection, gig: Gig, seller_id: Optional[int]) -> bool:
        """Insert or update a gig. Returns True if this gig is newly seen."""
        now = _now()
        existing = conn.execute(
            "SELECT id FROM gigs WHERE gig_id = ?", (gig.gig_id,)
        ).fetchone()
        is_new = existing is None
        conn.execute(
            """
            INSERT INTO gigs
                (gig_id, title, url, slug, category, query, price_cents,
                 currency, rating, reviews_count, image_url, is_new,
                 seller_id, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gig_id) DO UPDATE SET
                title         = excluded.title,
                price_cents   = excluded.price_cents,
                currency      = excluded.currency,
                rating        = excluded.rating,
                reviews_count = excluded.reviews_count,
                image_url     = excluded.image_url,
                seller_id     = excluded.seller_id,
                last_seen     = excluded.last_seen
            """,
            (
                gig.gig_id, gig.title, gig.url, gig.slug, gig.category,
                gig.query, gig.price_cents, gig.currency, gig.rating,
                gig.reviews_count, gig.image_url, int(is_new and gig.is_new or is_new),
                seller_id, now, now,
            ),
        )
        return is_new

    def store_gigs(self, gigs: Iterable[Gig], query: str = "") -> dict[str, int]:
        """Persist a batch of gigs (with their sellers) atomically.

        Returns counters: total, new_gigs, new_sellers.
        """
        new_gigs = new_sellers = total = 0
        with self._lock, self._connect() as conn:
            for gig in gigs:
                total += 1
                seller_id: Optional[int] = None
                if gig.seller and gig.seller.username:
                    seller_id, s_new = self.upsert_seller(conn, gig.seller)
                    new_sellers += int(s_new)
                if gig.query == "" and query:
                    gig.query = query
                if self.upsert_gig(conn, gig, seller_id):
                    new_gigs += 1
        return {"total": total, "new_gigs": new_gigs, "new_sellers": new_sellers}

    # -- scrape runs -------------------------------------------------------

    def start_run(self, query: str) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO scrape_runs (query, started_at, status) VALUES (?, ?, 'running')",
                (query, _now()),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, *, status: str, found: int = 0,
                   new_gigs: int = 0, new_sellers: int = 0, error: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE scrape_runs SET finished_at=?, status=?, found=?,
                   new_gigs=?, new_sellers=?, error=? WHERE id=?""",
                (_now(), status, found, new_gigs, new_sellers, error[:500], run_id),
            )

    # -- queries for the GUI ----------------------------------------------

    def recent_gigs(self, limit: int = 100, query: str = "",
                    only_new: bool = False, search: str = "",
                    no_reviews: bool = False, new_seller: bool = False) -> list[dict[str, Any]]:
        sql = [
            """SELECT g.*, s.username AS seller_username, s.display_name AS seller_name,
                      s.level AS seller_level, s.country AS seller_country,
                      s.is_pro AS seller_pro, s.avatar_url AS seller_avatar,
                      s.profile_url AS seller_profile
               FROM gigs g LEFT JOIN sellers s ON g.seller_id = s.id WHERE 1=1"""
        ]
        params: list[Any] = []
        if query:
            sql.append("AND g.query = ?")
            params.append(query)
        if only_new:
            sql.append("AND g.is_new = 1")
        if no_reviews:
            sql.append("AND g.reviews_count = 0")
        if new_seller:
            sql.append("AND s.level = 'New Seller'")
        if search:
            sql.append("AND (g.title LIKE ? OR s.username LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like])
        sql.append("ORDER BY g.first_seen DESC, g.id DESC LIMIT ?")
        params.append(max(1, min(limit, 500)))
        with self._lock, self._connect() as conn:
            rows = conn.execute(" ".join(sql), params).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            g = conn.execute("SELECT COUNT(*) c FROM gigs").fetchone()["c"]
            s = conn.execute("SELECT COUNT(*) c FROM sellers").fetchone()["c"]
            pro = conn.execute("SELECT COUNT(*) c FROM sellers WHERE is_pro=1").fetchone()["c"]
            new24 = conn.execute(
                "SELECT COUNT(*) c FROM gigs WHERE first_seen >= datetime('now','-1 day')"
            ).fetchone()["c"]
            last_run = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            runs = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 8"
            ).fetchall()
            by_query = conn.execute(
                "SELECT query, COUNT(*) c FROM gigs GROUP BY query ORDER BY c DESC"
            ).fetchall()
        return {
            "gigs": g,
            "sellers": s,
            "pro_sellers": pro,
            "new_24h": new24,
            "last_run": dict(last_run) if last_run else None,
            "recent_runs": [dict(r) for r in runs],
            "by_query": [dict(r) for r in by_query],
        }

    # -- settings persistence ---------------------------------------------

    def save_setting(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def load_setting(self, key: str, default: Any = None) -> Any:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (ValueError, TypeError):
            return default
