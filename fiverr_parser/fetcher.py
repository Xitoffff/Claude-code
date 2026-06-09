"""Resilient HTTP fetcher for Fiverr.

Fiverr fronts its site with an anti-bot layer (Cloudflare / PerimeterX),
so a naive request is usually answered with HTTP 403. This fetcher gives
the scraper the best realistic chance while staying polite and stable:

* a full set of modern Chrome headers (incl. sec-ch-ua / sec-fetch);
* a persistent client that keeps cookies between requests;
* exponential backoff with jitter on transient errors / 429 / 5xx;
* optional outbound proxy support.

When the live site blocks the datacenter IP, run the app in demo mode or
point ``proxy`` at a residential proxy / anti-bot solver.
"""

from __future__ import annotations

import random
import time
from typing import Optional
from urllib.parse import quote, urlunsplit

import httpx

from .config import CONFIG

_CHROME = "124.0.0.0"
_UA = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME} Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}


def normalize_proxy(raw: str) -> str:
    """Turn a user-supplied proxy string into a valid proxy URL.

    Rotating/residential proxies often carry rich credentials (country
    targeting, session ids) with characters that are illegal in a URL's
    userinfo section, e.g. ``user__cr.us,gb;sess.1:pass@host:port``. We
    percent-encode the username and password so the URL is well-formed.

    Accepted input shapes::

        http://user:pass@host:port      (already a URL)
        user:pass@host:port             (no scheme -> assume http)
        host:port:user:pass             (common proxy-list order)
        host:port                       (no auth)
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    scheme = "http"
    if "://" in raw:
        scheme, _, raw = raw.partition("://")
        scheme = scheme or "http"

    user = password = ""
    if "@" in raw:
        creds, _, hostport = raw.rpartition("@")
        user, _, password = creds.partition(":")
    else:
        parts = raw.split(":")
        if len(parts) == 4:  # host:port:user:pass
            host, port, user, password = parts
            hostport = f"{host}:{port}"
        else:
            hostport = raw

    netloc = hostport
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        netloc = f"{auth}@{hostport}"
    return urlunsplit((scheme, netloc, "", "", ""))


class FetchError(RuntimeError):
    """Raised when a URL could not be retrieved after all retries."""


class Fetcher:
    """Thin wrapper around :class:`httpx.Client` with retry/backoff."""

    def __init__(self, proxy: str = "", timeout: float = CONFIG.request_timeout,
                 max_retries: int = CONFIG.max_retries):
        self.proxy = normalize_proxy(proxy) or None
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.Client] = None

    def _client_obj(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers=BASE_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
                proxy=self.proxy,
                http2=False,
            )
        return self._client

    def get(self, url: str, *, referer: str = "https://www.fiverr.com/") -> str:
        """Fetch ``url`` returning response text, retrying transient failures."""
        headers = {"Referer": referer}
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client_obj().get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (403, 429, 503) or resp.status_code >= 500:
                    last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                else:
                    raise FetchError(f"HTTP {resp.status_code} for {url}")
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
            # Backoff: 1s, 2s, 4s ... with jitter, capped.
            if attempt < self.max_retries:
                delay = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.75)
                time.sleep(delay)
        raise FetchError(str(last_exc) if last_exc else f"failed to fetch {url}")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def build_search_url(query: str, page: int = 1) -> str:
    """Build a Fiverr gig-search URL for ``query`` sorted by newest."""
    from urllib.parse import quote_plus

    q = quote_plus(query.strip())
    # sort_by=recommended is default; "new_arrivals" surfaces fresh gigs.
    return (
        f"https://www.fiverr.com/search/gigs?query={q}"
        f"&source=top-bar&search_in=everywhere&page={page}"
    )
