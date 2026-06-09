# 🛰️ Fiverr Radar — new-listings parser

A fast, stable parser for **new Fiverr.com gig listings** with a polished
web dashboard. Built around SQLite, with first-class **seller
deduplication** and a focus on speed and resilience.

![stack](https://img.shields.io/badge/python-3.11+-3776AB) ![db](https://img.shields.io/badge/storage-SQLite%20(WAL)-003B57) ![ui](https://img.shields.io/badge/GUI-web%20dashboard-1dbf73)

## Two GUIs

- **🖥️ Native desktop app (PySide6/Qt)** — `python run_gui.py`. Dark themed
  window with stat cards, a sortable listings table (double-click a row to
  open the gig), filters (search / query / New only / Без отзывов /
  New Seller), live log console, settings dialog and `.txt` export.
- **🌐 Web dashboard (FastAPI)** — `python run.py`, open `http://127.0.0.1:8000`.
  Same backend, browser UI.

## Highlights

- **Beautiful GUI** — dark, polished interfaces (desktop *and* web) with live
  stat cards, listings table/feed, seller badges, filters, a real-time log
  console and settings.
- **SQLite storage** — WAL journal + tuned PRAGMAs for fast writes; proper
  indexes; per-run history for observability.
- **Seller deduplication** — natural unique key on `sellers.username` enforced
  by a UNIQUE index and `INSERT … ON CONFLICT … DO UPDATE` upsert. Re-seeing a
  seller refreshes their profile and bumps `seen_count` — never a duplicate.
  Gigs are deduplicated the same way on `gigs.gig_id`.
- **Speed & stability** — short-lived pooled connections guarded by a lock
  (safe across the API + scheduler threads); batched atomic writes; every
  query is isolated so one failure can't stop the loop.
- **Anti-bot aware fetching** — the main cause of Fiverr 403s is TLS/JA3
  fingerprinting, so the fetcher uses **`curl_cffi` Chrome impersonation**
  (real browser TLS handshake) over a rotating proxy, with a fresh
  connection per retry to cycle exit IPs. Falls back to `httpx` if
  `curl_cffi` is absent.
- **Demo mode (default)** — generates realistic sample listings so the whole
  app (GUI, DB, dedup) works offline or when the live site blocks the IP.

## Quick start

```bash
pip install -r requirements.txt

python run_gui.py     # native desktop app  (Windows: double-click start_gui.bat)
# — or —
python run.py         # web dashboard at http://127.0.0.1:8000
```

Click **Run now** for a one-off cycle, or **Start** to poll on an interval.

## Live scraping

Fiverr is behind an anti-bot layer (Cloudflare/PerimeterX), so requests from a
datacenter IP are usually answered with HTTP 403. To scrape the real site you
need a **residential / rotating proxy**:

1. Open **⚙ Settings**, turn **Demo mode** off.
2. Paste your **Proxy** URL (rotating proxies change the exit IP per request,
   which is ideal for staying unblocked).
3. Adjust queries / interval and **Save**.

```bash
FIVERR_DEMO=0 FIVERR_PROXY="http://user:pass@host:port" python run.py
```

> **Never commit proxy credentials.** Pass them via the `FIVERR_PROXY`
> environment variable or enter them in the GUI — they are stored only in the
> local SQLite DB, which is git-ignored.

The parser reads Fiverr's embedded JSON state and recursively extracts gigs,
so it tolerates the site's frequent markup changes. If a page is blocked or
yields nothing, the run is recorded as `blocked` and the loop continues.

## Configuration

Settings are editable in the GUI and persisted in SQLite. Environment vars:

| Variable | Default | Meaning |
|---|---|---|
| `FIVERR_PORT` | `8000` | dashboard port |
| `FIVERR_HOST` | `127.0.0.1` | bind host |
| `FIVERR_DB` | `./fiverr.db` | database path |
| `FIVERR_DEMO` | `1` | `0` = live scraping |
| `FIVERR_PROXY` | — | outbound proxy (rotating recommended) |
| `FIVERR_AUTOSTART` | `0` | start scheduler on launch |

## Architecture

```
run_gui.py                  desktop GUI entry point (PySide6)
run.py                      web dashboard entry point (uvicorn)
fiverr_parser/
  config.py                 settings (env + persisted) and app config
  models.py                 Seller / Gig dataclasses
  db.py                     SQLite layer: WAL, dedup upserts, stats
  fetcher.py                curl_cffi (Chrome TLS) + httpx, retries, proxy
  parser.py                 extract gigs from Fiverr embedded JSON
  mock.py                   demo data generator (bounded seller pool)
  scraper.py                fetch -> parse -> store, per-run records
  scheduler.py              background polling controller
  gui.py                    PySide6 desktop application
  app.py                    FastAPI: JSON API + web dashboard
  static/                   index.html, styles.css, app.js
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stats` | totals, recent runs, per-query counts |
| GET | `/api/status` | scheduler state |
| GET | `/api/gigs` | feed (`limit`, `query`, `search`, `only_new`) |
| GET | `/api/logs` | recent log lines |
| GET/POST | `/api/settings` | read / update settings |
| POST | `/api/scheduler/{start,stop,run-now}` | control polling |

## Notes

Respect Fiverr's Terms of Service and `robots.txt`, and any applicable laws
when scraping. This project is provided for educational purposes; use polite
intervals and your own authorized proxies.
