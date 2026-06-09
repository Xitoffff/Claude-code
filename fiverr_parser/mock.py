"""Realistic demo-data generator.

Used when ``demo_mode`` is on (default) or as a graceful fallback when the
live site blocks the request. It produces gigs and sellers with a stable
identity per username/gig-id, so the deduplication logic exercises exactly
the same code path as live data: repeated polls re-surface known sellers
and only occasionally introduce a brand-new one.
"""

from __future__ import annotations

import hashlib
import random
from typing import List

from .models import Gig, Seller

_ADJ = ["professional", "modern", "premium", "creative", "stunning", "custom",
        "expert", "minimalist", "unique", "high-converting", "responsive", "viral"]
_NOUN = {
    "logo design": ["logo", "brand identity", "mascot logo", "wordmark"],
    "wordpress": ["WordPress website", "WooCommerce store", "Elementor page", "WP plugin fix"],
    "video editing": ["video edit", "YouTube intro", "reels edit", "color grade"],
    "seo": ["SEO audit", "backlink campaign", "keyword research", "on-page SEO"],
    "illustration": ["illustration", "character art", "book cover", "vector art"],
}
_DEFAULT_NOUNS = ["service", "design", "project", "delivery"]
_LEVELS = ["New Seller", "Level 1", "Level 2", "Top Rated"]
_COUNTRIES = ["US", "GB", "IN", "PK", "UA", "DE", "BR", "PH", "NG", "CA"]
_FNAMES = ["alex", "maria", "john", "sara", "dmitry", "leo", "nina", "omar",
           "kate", "victor", "lena", "sam", "priya", "diego", "anna"]


def _seed(text: str) -> random.Random:
    h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    return random.Random(h)


def _seller_for(idx: int) -> Seller:
    """A stable seller identity for pool index ``idx`` (enables dedup)."""
    r = _seed(f"seller-{idx}")
    name = r.choice(_FNAMES) + str(r.randint(2, 99))
    level = r.choice(_LEVELS)
    return Seller(
        username=name,
        display_name=name.capitalize(),
        level=level,
        country=r.choice(_COUNTRIES),
        avatar_url="",
        profile_url=f"https://www.fiverr.com/{name}",
        is_pro=r.random() < 0.15,
        rating=round(r.uniform(4.4, 5.0), 1) if level != "New Seller" else 0.0,
        reviews_count=0 if level == "New Seller" else r.randint(5, 1800),
    )


# A bounded pool of sellers; new gigs reuse existing sellers most of the
# time, so seller dedup is meaningfully exercised across polls.
_SELLER_POOL = 60


def generate(query: str, count: int) -> List[Gig]:
    r = random.Random()  # non-deterministic: fresh listings each poll
    nouns = _NOUN.get(query.lower(), _DEFAULT_NOUNS)
    gigs: List[Gig] = []
    for _ in range(count):
        seller = _seller_for(r.randint(0, _SELLER_POOL - 1))
        gid = str(r.randint(10_000_000, 999_999_999))
        title = f"I will create a {r.choice(_ADJ)} {r.choice(nouns)} for you"
        reviews = 0 if seller.reviews_count == 0 else r.randint(0, seller.reviews_count)
        gigs.append(Gig(
            gig_id=gid,
            title=title[:80],
            url=f"https://www.fiverr.com/{seller.username}/{gid}",
            slug=f"{seller.username}/{gid}",
            category=query.title(),
            query=query,
            price_cents=r.choice([500, 1000, 1500, 2500, 5000, 7500, 10000, 15000]),
            currency="USD",
            rating=round(r.uniform(4.6, 5.0), 1) if reviews else 0.0,
            reviews_count=reviews,
            image_url="",
            is_new=reviews == 0,
            seller=seller,
        ))
    return gigs
