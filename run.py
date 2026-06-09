#!/usr/bin/env python3
"""Entry point: launch the Fiverr Radar dashboard.

    python run.py            # start the web GUI on http://127.0.0.1:8000
    FIVERR_PORT=9000 python run.py
    FIVERR_DEMO=0 python run.py   # use live scraping (needs a working proxy)
"""

from __future__ import annotations

import uvicorn

from fiverr_parser.config import CONFIG


def main() -> None:
    print("=" * 56)
    print("  🛰️  Fiverr Radar — new-listings parser")
    print(f"  Dashboard:  http://{CONFIG.host}:{CONFIG.port}")
    print(f"  Database:   {CONFIG.db_path}")
    print("=" * 56)
    uvicorn.run("fiverr_parser.app:app", host=CONFIG.host, port=CONFIG.port, log_level="warning")


if __name__ == "__main__":
    main()
