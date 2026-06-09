"""Fiverr new-listings parser with a beautiful web GUI.

Package layout::

    config     - runtime configuration (env + persisted settings)
    models     - lightweight dataclasses for Seller / Gig
    db         - SQLite storage layer (WAL, dedup, upserts, stats)
    fetcher    - resilient HTTP fetcher (browser headers, retries, proxy)
    parser     - extracts gigs from Fiverr HTML / embedded JSON
    mock       - realistic demo data generator (offline mode)
    scraper    - orchestrates fetch -> parse -> store
    scheduler  - background polling loop
    app        - FastAPI application + JSON API + dashboard
"""

__version__ = "1.0.0"
