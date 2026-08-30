"""Разведка toco.id: снимает факты, которых не хватает для завершения этапа 0.

Скрипт не является частью бота. Его задача — за один прогон ответить на вопросы
из `docs/api.md`, раздел 2: есть ли внутренний JSON API, как устроена пагинация,
какие поля реально отдаются и подтверждается ли гипотеза про epoch-ms в slug.

Запуск::

    pip install httpx selectolax
    python tools/recon.py --query "iphone"

    # с перехватом XHR — покажет внутренний API, если он есть
    pip install playwright && playwright install chromium
    python tools/recon.py --query "iphone" --browser

Все запросы идут только к публичным страницам, с паузой между ними и обычным
User-Agent. Никакой авторизации, обхода капчи и приватных данных.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import httpx

BASE = "https://toco.id"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DUMP_DIR = Path("recon_dump")

#: Пауза между запросами, сек. Держим вежливый темп даже в разведке.
DELAY = 1.5

#: Признаки того, что страница отдаёт данные в виде встроенного JSON.
SSR_MARKERS = (
    "__NEXT_DATA__",
    "_next/data",
    "__NUXT__",
    "__INITIAL_STATE__",
    "window.__",
    "application/ld+json",
    "self.__next_f",
)

#: Строки, похожие на эндпоинты внутреннего API.
ENDPOINT_RE = re.compile(
    r"""["'`](?P<url>(?:https?://[\w.-]*toco\.id)?/(?:api|v\d|graphql|_next/data)[\w./?=&%{}$-]*)["'`]""",
    re.IGNORECASE,
)

#: 13-значный epoch-ms в конце slug (см. docs/api.md, п. 1.3).
SLUG_EPOCH_RE = re.compile(r"-(?P<ms>1\d{12})(?:-[0-9a-f]{4})?$")

#: Индонезийские относительные даты — проверяем, в каком виде сайт отдаёт дату.
REL_DATE_RE = re.compile(
    r"\b(\d+\s*(detik|menit|jam|hari|minggu|bulan|tahun)\s*(yang\s*)?lalu"
    r"|kemarin|baru\s*saja|hari\s*ini)\b",
    re.IGNORECASE,
)

#: Абсолютные даты вида «12 Juli 2026».
ABS_DATE_RE = re.compile(
    r"\b\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September"
    r"|Oktober|November|Desember)\s+\d{4}\b",
    re.IGNORECASE,
)

#: Поля профиля продавца, от наличия которых зависят фильтры 1 и 3.
SELLER_FIELD_RE = {
    "дата регистрации": re.compile(r"bergabung|joined|member\s*sejak", re.IGNORECASE),
    "отзывы": re.compile(r"ulasan|review|penilaian", re.IGNORECASE),
    "рейтинг": re.compile(r"rating|\b[0-5][.,]\d\s*(?:/\s*5|bintang)", re.IGNORECASE),
}


@dataclass
class Finding:
    """Один установленный факт с пометкой, чем он подтверждён."""

    topic: str
    detail: str
    evidence: str = ""


@dataclass
class Report:
    """Накопитель результатов разведки."""

    findings: list[Finding] = field(default_factory=list)
    endpoints: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def add(self, topic: str, detail: str, evidence: str = "") -> None:
        """Записать факт и сразу напечатать его, чтобы прогон было видно вживую."""
        self.findings.append(Finding(topic, detail, evidence))
        print(f"  [{topic}] {detail}" + (f"  ← {evidence[:120]}" if evidence else ""))

    def fail(self, msg: str) -> None:
        """Записать ошибку шага, не прерывая остальную разведку."""
        self.errors.append(msg)
        print(f"  [!] {msg}", file=sys.stderr)


def dump(name: str, text: str) -> Path:
    """Сохранить сырой ответ на диск для ручного разбора."""
    DUMP_DIR.mkdir(exist_ok=True)
    path = DUMP_DIR / name
    path.write_text(text, encoding="utf-8")
    return path


def find_endpoints(text: str) -> set[str]:
    """Выбрать из текста строки, похожие на API-эндпоинты."""
    return {m.group("url") for m in ENDPOINT_RE.finditer(text)}


def epoch_from_slug(url: str) -> datetime | None:
    """Достать дату публикации из epoch-ms в хвосте slug, если он там есть.

    Возвращает tz-aware UTC datetime либо ``None``, если суффикса нет.
    Это кандидат на дату публикации, а не подтверждённое значение — см. п. 1.3
    в docs/api.md.
    """
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    match = SLUG_EPOCH_RE.search(slug)
    if not match:
        return None
    return datetime.fromtimestamp(int(match.group("ms")) / 1000, tz=timezone.utc)


class Recon:
    """Последовательность проверок, закрывающих открытые пункты этапа 0."""

    def __init__(self, client: httpx.AsyncClient, report: Report) -> None:
        self.client = client
        self.report = report

    async def get(self, url: str) -> httpx.Response | None:
        """Выполнить вежливый GET: пауза, обычный UA, ошибки не бросаем."""
        await asyncio.sleep(DELAY)
        try:
            resp = await self.client.get(url)
        except httpx.HTTPError as exc:
            self.report.fail(f"{url}: {exc!r}")
            return None
        print(f"  GET {url} → {resp.status_code} ({len(resp.content)} B)")
        if resp.status_code >= 400:
            self.report.fail(f"{url}: HTTP {resp.status_code}")
            return None
        return resp

    async def robots(self) -> None:
        """Прочитать robots.txt целиком — блокирующий пункт этапа 0."""
        print("\n=== 1. robots.txt ===")
        resp = await self.get(f"{BASE}/robots.txt")
        if resp is None:
            self.report.fail("robots.txt недоступен — парсинг начинать НЕЛЬЗЯ")
            return
        body = resp.text
        print(body)
        dump("robots.txt", body)

        for line in body.splitlines():
            low = line.strip().lower()
            if low.startswith(("crawl-delay", "sitemap")):
                self.report.add("robots", line.strip())
        for path in ("/listing/", "/store/", "/p/", "/id/search", "/classified"):
            blocked = any(
                path.rstrip("/").startswith(rule.split(":", 1)[1].strip().rstrip("*/"))
                for rule in body.splitlines()
                if rule.strip().lower().startswith("disallow:")
                and rule.split(":", 1)[1].strip() not in ("", "/")
            )
            self.report.add("robots", f"{path}: {'ЗАПРЕЩЁН' if blocked else 'разрешён'}")

    async def page(self, url: str, label: str) -> str | None:
        """Скачать страницу, сохранить дамп и собрать с неё признаки API."""
        resp = await self.get(url)
        if resp is None:
            return None
        html = resp.text
        dump(f"{label}.html", html)

        for marker in SSR_MARKERS:
            if marker in html:
                self.report.add("SSR", f"{label}: найден маркер {marker}")

        found = find_endpoints(html)
        if found:
            self.report.endpoints |= found
            self.report.add("API", f"{label}: {len(found)} кандидатов в эндпоинты")

        # Заголовки ответа говорят о защите и кэшировании.
        for header in ("server", "cf-ray", "x-powered-by", "set-cookie"):
            if header in resp.headers:
                self.report.add("headers", f"{label}: {header}={resp.headers[header][:80]}")
        return html

    async def js_bundles(self, html: str) -> None:
        """Пройти по JS-бандлам страницы и поискать в них эндпоинты."""
        print("\n=== 5. JS-бандлы ===")
        srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)[:8]
        if not srcs:
            self.report.add("JS", "внешних бандлов не найдено (вероятно, инлайн)")
        for src in srcs:
            resp = await self.get(urljoin(BASE, src))
            if resp is None:
                continue
            found = find_endpoints(resp.text)
            if found:
                self.report.endpoints |= found
                self.report.add("API", f"{src.rsplit('/', 1)[-1]}: {sorted(found)[:5]}")

    async def pagination(self, search_url: str) -> None:
        """Проверить OpenCart-подобные параметры пагинации и сортировки."""
        print("\n=== 6. Пагинация и сортировка ===")
        base_resp = await self.get(search_url)
        if base_resp is None:
            return
        base_len = len(base_resp.text)

        for param in ("page=2", "limit=5", "sort=terbaru", "order=DESC", "offset=20"):
            sep = "&" if "?" in search_url else "?"
            resp = await self.get(f"{search_url}{sep}{param}")
            if resp is None:
                continue
            delta = abs(len(resp.text) - base_len)
            verdict = "выдача изменилась" if delta > 200 else "без эффекта"
            self.report.add("пагинация", f"{param}: {verdict} (Δ{delta} B)")

    async def listing_fields(self, html: str, url: str) -> None:
        """Сверить дату из slug с датой на карточке и оценить доступность полей."""
        print("\n=== 7. Поля карточки и дата публикации ===")
        from_slug = epoch_from_slug(url)
        if from_slug:
            self.report.add(
                "дата", f"из slug: {from_slug.isoformat()}", evidence=url
            )
        else:
            self.report.add("дата", "в slug нет epoch-ms — нужен дозапрос карточки", url)

        rel = REL_DATE_RE.search(html)
        abs_ = ABS_DATE_RE.search(html)
        if rel:
            self.report.add("дата", f"на странице ОТНОСИТЕЛЬНАЯ: {rel.group(0)!r}")
        if abs_:
            self.report.add("дата", f"на странице АБСОЛЮТНАЯ: {abs_.group(0)!r}")
        if not rel and not abs_:
            self.report.add("дата", "на странице дата НЕ НАЙДЕНА — риск для only_recent")

        if from_slug and abs_:
            self.report.add(
                "гипотеза 1.3",
                f"сверьте вручную: slug={from_slug:%d.%m.%Y} vs страница={abs_.group(0)}",
            )

    async def seller_fields(self, html: str) -> None:
        """Проверить наличие полей продавца, от которых зависят фильтры 1 и 3."""
        print("\n=== 8. Поля продавца ===")
        for label, pattern in SELLER_FIELD_RE.items():
            match = pattern.search(html)
            if match:
                start = max(0, match.start() - 60)
                self.report.add(
                    "продавец", f"{label}: НАЙДЕНО", html[start : match.end() + 60]
                )
            else:
                self.report.add("продавец", f"{label}: НЕ НАЙДЕНО ← блокер фильтра")


async def capture_xhr(query: str, report: Report) -> None:
    """Снять все XHR/fetch веб-версии через Playwright — это и есть внутренний API."""
    print("\n=== 9. Перехват XHR (Playwright) ===")
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        report.fail("playwright не установлен — режим --browser пропущен")
        return

    calls: list[dict[str, Any]] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await (await browser.new_context(user_agent=UA)).new_page()

        def on_request(req: Any) -> None:
            if req.resource_type in ("xhr", "fetch"):
                calls.append(
                    {
                        "method": req.method,
                        "url": req.url,
                        "headers": dict(req.headers),
                        "post_data": req.post_data,
                    }
                )

        page.on("request", on_request)
        for url in (
            f"{BASE}/id/search?q=product%2Fsearch&search={query}",
            f"{BASE}/classified/search",
        ):
            try:
                await page.goto(url, wait_until="networkidle", timeout=45_000)
                await page.mouse.wheel(0, 4000)  # догрузить ленивую подгрузку
                await page.wait_for_timeout(3000)
            except Exception as exc:  # noqa: BLE001 — разведка не должна падать
                report.fail(f"playwright {url}: {exc!r}")
        await browser.close()

    dump("xhr.json", json.dumps(calls, ensure_ascii=False, indent=2))
    for call in calls:
        report.endpoints.add(call["url"])
        report.add("XHR", f"{call['method']} {call['url'][:140]}")
    if not calls:
        report.add("XHR", "XHR не зафиксированы — вероятно, чистый SSR без клиентского API")


def print_summary(report: Report) -> None:
    """Напечатать итог в виде, пригодном для переноса в docs/api.md."""
    print("\n" + "=" * 70)
    print("ИТОГ РАЗВЕДКИ — перенести в docs/api.md, разделы 1 и 2")
    print("=" * 70)

    by_topic: dict[str, list[Finding]] = {}
    for finding in report.findings:
        by_topic.setdefault(finding.topic, []).append(finding)
    for topic, items in by_topic.items():
        print(f"\n## {topic}")
        for item in items:
            print(f"  - {item.detail}")

    if report.endpoints:
        print("\n## Кандидаты в эндпоинты")
        for url in sorted(report.endpoints):
            print(f"  - {url}")

    if report.errors:
        print("\n## Ошибки")
        for err in report.errors:
            print(f"  - {err}")

    print(f"\nСырые дампы: {DUMP_DIR.resolve()}")


async def main(args: argparse.Namespace) -> int:
    """Прогнать все проверки по порядку и напечатать сводку."""
    report = Report()
    search_url = f"{BASE}/id/search?q=product%2Fsearch&search={args.query}"

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True, timeout=30.0
    ) as client:
        recon = Recon(client, report)

        await recon.robots()

        print("\n=== 2-4. Публичные страницы ===")
        search_html = await recon.page(search_url, "search")
        await recon.page(f"{BASE}/classified", "classified")
        listing_html = await recon.page(f"{BASE}{args.listing}", "listing")
        store_html = await recon.page(f"{BASE}{args.store}", "store")

        if search_html:
            await recon.js_bundles(search_html)
        await recon.pagination(search_url)
        if listing_html:
            await recon.listing_fields(listing_html, f"{BASE}{args.listing}")
        if store_html:
            await recon.seller_fields(store_html)

    if args.browser:
        await capture_xhr(args.query, report)

    print_summary(report)
    return 1 if report.errors and not report.findings else 0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Разобрать аргументы командной строки."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="iphone", help="поисковый запрос")
    parser.add_argument(
        "--listing",
        default="/listing/vape-bekas-1768994453223-3c74",
        help="путь к карточке товара (со slug'ом, содержащим epoch-ms)",
    )
    parser.add_argument(
        "--store", default="/store/review-in", help="путь к витрине продавца"
    )
    parser.add_argument(
        "--browser", action="store_true", help="перехватить XHR через Playwright"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args())))
