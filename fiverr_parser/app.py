"""FastAPI application: JSON API + the dashboard GUI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import CONFIG, Settings
from .db import Database
from .scheduler import SchedulerController

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).resolve().parent / "static"

db = Database(CONFIG.db_path)

# Load persisted settings (falling back to defaults), then build controller.
_persisted = db.load_setting("settings")
settings = Settings.from_dict(_persisted) if _persisted else Settings().sanitized()
scheduler = SchedulerController(db, settings)

app = FastAPI(title="Fiverr New-Listings Parser", version="1.0.0")


class SettingsIn(BaseModel):
    queries: list[str] | None = None
    interval_seconds: int | None = None
    max_per_query: int | None = None
    demo_mode: bool | None = None
    proxy: str | None = None
    autostart: bool | None = None
    concurrency: int | None = None
    pages_per_query: int | None = None


@app.on_event("startup")
def _startup() -> None:
    if settings.autostart:
        scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.stop()


# -- API -------------------------------------------------------------------

@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    return db.stats()


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    return scheduler.status()


@app.get("/api/logs")
def api_logs(limit: int = Query(100, ge=1, le=300)) -> dict[str, Any]:
    return {"logs": scheduler.logs(limit)}


@app.get("/api/gigs")
def api_gigs(
    limit: int = Query(100, ge=1, le=500),
    query: str = "",
    search: str = "",
    only_new: bool = False,
    no_reviews: bool = False,
    new_seller: bool = False,
) -> dict[str, Any]:
    gigs = db.recent_gigs(
        limit=limit, query=query, only_new=only_new, search=search,
        no_reviews=no_reviews, new_seller=new_seller,
    )
    return {"gigs": gigs, "count": len(gigs)}


@app.get("/api/export.txt")
def api_export(
    limit: int = Query(500, ge=1, le=500),
    query: str = "",
    search: str = "",
    only_new: bool = False,
    no_reviews: bool = False,
    new_seller: bool = False,
) -> PlainTextResponse:
    """Export the filtered gig URLs as a plain-text file, one URL per line."""
    gigs = db.recent_gigs(
        limit=limit, query=query, only_new=only_new, search=search,
        no_reviews=no_reviews, new_seller=new_seller,
    )
    urls = "\n".join(g["url"] for g in gigs if g.get("url"))
    return PlainTextResponse(
        urls + ("\n" if urls else ""),
        headers={"Content-Disposition": 'attachment; filename="fiverr_urls.txt"'},
    )


@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return settings.to_dict()


@app.post("/api/settings")
def api_set_settings(payload: SettingsIn) -> dict[str, Any]:
    data = settings.to_dict()
    for key, value in payload.model_dump(exclude_none=True).items():
        data[key] = value
    new = Settings.from_dict(data)
    # mutate in place so the scheduler (holding a reference) sees changes
    for field_name, value in new.to_dict().items():
        setattr(settings, field_name, value)
    db.save_setting("settings", settings.to_dict())
    scheduler._wake.set()  # apply interval/query changes promptly
    return {"ok": True, "settings": settings.to_dict()}


@app.post("/api/scheduler/start")
def api_start() -> dict[str, Any]:
    scheduler.start()
    return scheduler.status()


@app.post("/api/scheduler/stop")
def api_stop() -> dict[str, Any]:
    scheduler.stop()
    return scheduler.status()


@app.post("/api/scheduler/run-now")
def api_run_now() -> dict[str, Any]:
    scheduler.run_now()
    return {"ok": True}


# -- Dashboard -------------------------------------------------------------

@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": app.version})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
