"""Runtime configuration.

Values come from sensible defaults, can be overridden by environment
variables, and a subset (the live "settings") is persisted to the SQLite
DB so the GUI can change them at runtime without a restart.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = os.environ.get("FIVERR_DB", str(BASE_DIR / "fiverr.db"))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    """Mutable settings editable from the GUI and persisted in the DB."""

    # Search queries to poll for new listings (one scrape run per query).
    queries: list[str] = field(
        default_factory=lambda: ["logo design", "wordpress", "video editing"]
    )
    # Seconds between automatic polling cycles.
    interval_seconds: int = 180
    # Max gigs to read per query per run.
    max_per_query: int = 48
    # When True, generate realistic demo data instead of hitting the live
    # site. Useful for previewing the GUI or when the site blocks the IP.
    demo_mode: bool = _env_bool("FIVERR_DEMO", True)
    # Optional outbound proxy, e.g. "http://user:pass@host:port".
    proxy: str = os.environ.get("FIVERR_PROXY", "")
    # Whether the background scheduler starts automatically on launch.
    autostart: bool = _env_bool("FIVERR_AUTOSTART", False)

    def sanitized(self) -> "Settings":
        """Clamp values into safe ranges to keep the scraper stable."""
        self.interval_seconds = max(30, min(int(self.interval_seconds), 86_400))
        self.max_per_query = max(1, min(int(self.max_per_query), 200))
        self.queries = [q.strip() for q in self.queries if q and q.strip()][:25]
        if not self.queries:
            self.queries = ["logo design"]
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        known = {f for f in cls().to_dict()}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**clean).sanitized()


@dataclass(frozen=True)
class AppConfig:
    """Static configuration resolved at process start."""

    db_path: str = DEFAULT_DB_PATH
    host: str = os.environ.get("FIVERR_HOST", "127.0.0.1")
    port: int = int(os.environ.get("FIVERR_PORT", "8000"))
    request_timeout: float = float(os.environ.get("FIVERR_TIMEOUT", "20"))
    max_retries: int = int(os.environ.get("FIVERR_RETRIES", "6"))


CONFIG = AppConfig()
