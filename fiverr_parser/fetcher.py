"""Resilient HTTP fetcher for Fiverr.

Fiverr fronts its site with PerimeterX (HUMAN) + Cloudflare. The single
biggest reason a request gets HTTP 403 is **TLS fingerprinting**: plain
``httpx``/``requests`` present a non-browser JA3 fingerprint that the
anti-bot layer flags at the network level, before it even looks at the IP.

So the primary transport here is :mod:`curl_cffi`, which wraps
curl-impersonate to reproduce Chrome's exact TLS handshake and HTTP/2
profile — making the request indistinguishable from a real browser on the
wire. Combined with a rotating residential proxy this clears most 403s.
If ``curl_cffi`` is not installed we fall back to ``httpx``.

Other stability measures: realistic Chrome headers, fresh connection per
retry (so a rotating proxy hands out a new exit IP), and capped backoff.
"""

from __future__ import annotations

import random
import time
from typing import Optional
from urllib.parse import quote, urlunsplit

import httpx

from .config import CONFIG

try:  # optional but strongly recommended for live scraping
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except Exception:  # pragma: no cover - depends on install
    cffi_requests = None
    _HAS_CFFI = False

# curl_cffi impersonation target. "chrome" tracks the latest Chrome profile.
IMPERSONATE = "chrome"

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
    """HTTP fetcher with browser TLS impersonation, retry and IP rotation.

    Uses :mod:`curl_cffi` (Chrome impersonation) when available, otherwise
    :mod:`httpx`. The public surface is a single :meth:`get`.
    """

    def __init__(self, proxy: str = "", timeout: float = CONFIG.request_timeout,
                 max_retries: int = CONFIG.max_retries, impersonate: str = IMPERSONATE):
        self.proxy = normalize_proxy(proxy) or None
        self.timeout = timeout
        self.max_retries = max_retries
        self.impersonate = impersonate
        self.engine = "curl_cffi" if _HAS_CFFI else "httpx"
        self._client = None  # httpx.Client or curl_cffi Session

    # -- engine-specific session management --------------------------------

    def _session(self):
        if self._client is not None:
            return self._client
        if _HAS_CFFI:
            self._client = cffi_requests.Session(
                impersonate=self.impersonate,
                proxies={"http": self.proxy, "https": self.proxy} if self.proxy else None,
                timeout=self.timeout,
                verify=True,
            )
        else:
            self._client = httpx.Client(
                headers=BASE_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
                proxy=self.proxy,
                http2=True,
                limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
            )
        return self._client

    def _reset_connection(self) -> None:
        """Drop the session so the next request rotates the proxy exit IP."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def _do_get(self, url: str, headers: dict):
        sess = self._session()
        if _HAS_CFFI:
            return sess.get(url, headers=headers, allow_redirects=True)
        return sess.get(url, headers=headers)

    def get(self, url: str, *, referer: str = "https://www.fiverr.com/") -> str:
        """Fetch ``url`` returning response text.

        Blocked responses (403/429/503) are retried on a fresh connection,
        so a rotating proxy serves a different exit IP each attempt — which,
        together with the Chrome TLS fingerprint, usually clears the block.
        """
        headers = dict(BASE_HEADERS)
        headers["Referer"] = referer
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._do_get(url, headers)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (403, 429, 503) or resp.status_code >= 500:
                    last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                    self._reset_connection()  # rotate IP before next attempt
                else:
                    raise FetchError(f"HTTP {resp.status_code} for {url}")
            except FetchError:
                raise
            except Exception as exc:  # transport/timeout across both engines
                last_exc = exc
                self._reset_connection()
            if attempt < self.max_retries:
                delay = min(2 ** (attempt - 1), 4) + random.uniform(0, 0.6)
                time.sleep(delay)
        raise FetchError(str(last_exc) if last_exc else f"failed to fetch {url}")

    def close(self) -> None:
        self._reset_connection()

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
