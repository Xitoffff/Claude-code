"""Extract gig listings from a Fiverr search page.

Fiverr renders search results from a JSON state blob embedded in the
page (the "Perseus" initial props). Rather than hard-coding one fragile
DOM path, we parse every embedded JSON document and recursively look for
objects that *look like* a gig (they carry a gig id + title + seller).
This tolerates Fiverr's frequent markup/state changes.

A JSON-LD fallback is attempted when no state blob is found.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from selectolax.parser import HTMLParser

from .models import Gig, Seller

# Keys Fiverr has historically used for the same concept. We probe each.
_GIG_ID_KEYS = ("gig_id", "gigId", "id", "auction_id")
_TITLE_KEYS = ("gig_title", "title", "cached_slug_title", "displayTitle")
_SLUG_KEYS = ("cached_slug", "slug", "gig_url", "url")
_SELLER_NAME_KEYS = ("seller_name", "sellerName", "username", "seller_username")
_PRICE_KEYS = ("price", "package_price", "price_i", "buying_review_rating")


def _first(d: dict, keys: Iterable[str], default=None):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _iter_json_blobs(html: str) -> Iterable[Any]:
    """Yield parsed JSON objects found in <script> tags of the page."""
    tree = HTMLParser(html)
    for node in tree.css("script"):
        raw = (node.text() or "").strip()
        if not raw or "{" not in raw:
            continue
        # Direct JSON payloads (application/json, ld+json, perseus props).
        if raw[0] in "{[":
            try:
                yield json.loads(raw)
                continue
            except ValueError:
                pass
        # Inline assignments: window.__X = {...};
        for match in re.finditer(r"=\s*(\{.*?\})\s*;?\s*$", raw, re.DOTALL):
            try:
                yield json.loads(match.group(1))
                break
            except ValueError:
                continue


def _looks_like_gig(obj: dict) -> bool:
    has_id = any(k in obj for k in _GIG_ID_KEYS)
    has_title = any(k in obj for k in _TITLE_KEYS)
    has_seller = any(k in obj for k in _SELLER_NAME_KEYS) or "seller" in obj
    return has_id and has_title and has_seller


def _walk(obj: Any) -> Iterable[dict]:
    """Depth-first walk yielding dicts that look like gigs."""
    if isinstance(obj, dict):
        if _looks_like_gig(obj):
            yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _to_price_cents(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        # Fiverr sometimes stores cents, sometimes dollars.
        return int(value) if value > 1000 else int(round(float(value) * 100))
    m = re.search(r"(\d+(?:\.\d+)?)", str(value))
    return int(round(float(m.group(1)) * 100)) if m else 0


def _build_seller(obj: dict) -> Seller:
    nested = obj.get("seller") if isinstance(obj.get("seller"), dict) else {}
    username = _first(obj, _SELLER_NAME_KEYS) or _first(nested, _SELLER_NAME_KEYS) or ""
    profile = obj.get("seller_url") or nested.get("url") or (
        f"https://www.fiverr.com/{username}" if username else ""
    )
    level = (
        obj.get("seller_level") or nested.get("level")
        or obj.get("seller_level_name") or ""
    )
    return Seller(
        username=str(username),
        display_name=str(obj.get("seller_display_name") or nested.get("displayName") or username),
        level=str(level).replace("_", " ").title() if level else "",
        country=str(obj.get("seller_country") or nested.get("country") or ""),
        avatar_url=str(obj.get("seller_img") or nested.get("image") or ""),
        profile_url=str(profile),
        is_pro=bool(obj.get("is_pro") or obj.get("agency") or nested.get("isPro")),
        rating=float(obj.get("seller_rating") or nested.get("rating") or 0) or 0.0,
        reviews_count=int(obj.get("seller_reviews_count") or nested.get("ratingsCount") or 0),
    )


def _build_gig(obj: dict, query: str) -> Gig | None:
    gig_id = _first(obj, _GIG_ID_KEYS)
    title = _first(obj, _TITLE_KEYS)
    if not gig_id or not title:
        return None
    slug = str(_first(obj, _SLUG_KEYS) or "")
    url = slug if slug.startswith("http") else (
        f"https://www.fiverr.com/{slug}" if slug else ""
    )
    reviews = int(obj.get("buying_review_rating_count") or obj.get("reviews_count") or 0)
    return Gig(
        gig_id=str(gig_id),
        title=str(title),
        url=url,
        slug=slug,
        category=str(obj.get("category_name") or obj.get("sub_category") or ""),
        query=query,
        price_cents=_to_price_cents(_first(obj, _PRICE_KEYS)),
        currency=str(obj.get("currency") or "USD"),
        rating=float(obj.get("gig_rating") or obj.get("rating") or 0) or 0.0,
        reviews_count=reviews,
        image_url=str(obj.get("gig_img") or obj.get("image") or obj.get("cloudImgMpoUrl") or ""),
        is_new=reviews == 0,
        seller=_build_seller(obj),
    )


def extract_gigs(html: str, query: str = "") -> list[Gig]:
    """Parse ``html`` and return the list of unique gigs found."""
    seen: set[str] = set()
    gigs: list[Gig] = []
    for blob in _iter_json_blobs(html):
        for candidate in _walk(blob):
            gig = _build_gig(candidate, query)
            if gig and gig.gig_id not in seen:
                seen.add(gig.gig_id)
                gigs.append(gig)
    return gigs
