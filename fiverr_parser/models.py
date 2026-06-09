"""Lightweight data models passed between the parser and storage layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Seller:
    """A Fiverr seller. ``username`` is the natural dedup key."""

    username: str
    display_name: str = ""
    level: str = ""
    country: str = ""
    avatar_url: str = ""
    profile_url: str = ""
    is_pro: bool = False
    rating: float = 0.0
    reviews_count: int = 0

    def normalized_username(self) -> str:
        return self.username.strip().lower()


@dataclass
class Gig:
    """A Fiverr gig / listing. ``gig_id`` is the natural dedup key."""

    gig_id: str
    title: str
    url: str = ""
    slug: str = ""
    category: str = ""
    query: str = ""
    price_cents: int = 0
    currency: str = "USD"
    rating: float = 0.0
    reviews_count: int = 0
    image_url: str = ""
    is_new: bool = False
    seller: Optional[Seller] = field(default=None)

    @property
    def price(self) -> float:
        return round(self.price_cents / 100.0, 2)
