#!/usr/bin/env python3
"""
Category List Spider — 爬取 Amazon Bestsellers 类目列表页，发现 Top50/Top100 产品。

以 **Browse Node ID (codied)** 作为类目目录名，每次运行追加一条排名快照到
``rankings.jsonl``，不重复存储 product HTML（那是 ProductSpider 的职责）。

输出布局：
    {output_dir}/categories/{browse_node_id}/
    ├── category_001.html       (最新一次列表页 HTML)
    ├── meta.json               (类目元信息)
    └── rankings.jsonl          (每次运行追加一条排名快照)

Usage:
    python category_spider.py \\
        --category-url "https://www.amazon.com/gp/bestsellers/fashion/1040658/" \\
        --output-dir ./workspace
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import DynamicFetcher, StealthyFetcher

LOGGER = logging.getLogger("category_spider")

HREF_RE = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"']", re.IGNORECASE)
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE)
PRODUCT_HINT_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/", re.IGNORECASE)

BROWSE_NODE_RE = re.compile(
    r"/(?:gp/bestsellers|gp/bestsellers/[^/]+|zgbs(?:/[^/]+)*)/(\d+)(?:[/?]|$)",
    re.IGNORECASE,
)
CATEGORY_SLUG_RE = re.compile(
    r"/(?:gp/bestsellers|zgbs)/([^/]+)/\d+(?:[/?]|$)",
    re.IGNORECASE,
)

ANTI_BOT_MARKERS = (
    "captcha", "robot check",
    "type the characters you see in this image",
    "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "validatecaptcha",
)


@dataclass(frozen=True)
class CategoryCrawlConfig:
    category_url: str
    output_dir: Path
    max_category_pages: int = 1
    timeout_ms: int = 90000
    wait_ms: int = 2500
    delay_ms: int = 3500
    retries_per_fetcher: int = 2
    headless: bool = True
    prefer_stealth: bool = True
    solve_cloudflare: bool = False
    useragent: str | None = None
    proxy: str | None = None
    marketplace: str = "US"
    scroll_category: bool = True
    scroll_pause_ms: int = 1500


@dataclass(frozen=True)
class FetchResult:
    response: Response | None
    fetcher_name: str
    error: str | None
    blocked: bool


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:16]


def is_amazon_url(url: str) -> bool:
    return "amazon." in urlparse(url).netloc.lower()


def extract_asin(url: str) -> str | None:
    match = ASIN_RE.search(url)
    return match.group(1).upper() if match else None


def extract_browse_node_id(url: str) -> str | None:
    """Extract the Browse Node ID (codied) from an Amazon Bestsellers URL."""
    path = urlparse(url).path
    match = BROWSE_NODE_RE.search(path)
    if match:
        return match.group(1)
    # Fallback: find trailing digit run in the path
    digits = re.findall(r"/(\d{3,})(?:/|$)", path)
    return digits[-1] if digits else None


def extract_category_slug_from_url(url: str) -> str | None:
    """Extract the human-readable category slug (e.g. 'fashion')."""
    match = CATEGORY_SLUG_RE.search(urlparse(url).path)
    return match.group(1).lower() if match else None


def canonical_category_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.split("/ref=", 1)[0]
    query = parse_qs(parsed.query)
    query_parts: list[str] = []
    if "pg" in query and query["pg"]:
        query_parts.append(f"pg={query['pg'][0]}")
    clean_query = "&".join(query_parts)
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", clean_query, ""))


def canonical_product_url(url: str) -> str:
    parsed = urlparse(url)
    asin = extract_asin(url)
    if asin:
        return urlunparse((parsed.scheme or "https", parsed.netloc, f"/dp/{asin}", "", "", ""))
    path = parsed.path.split("/ref=", 1)[0]
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))


def iter_hrefs(base_url: str, html_content: str) -> list[str]:
    links: list[str] = []
    for match in HREF_RE.finditer(html_content):
        href = match.group("href").replace("&amp;", "&").strip()
        if not href or href.startswith("#") or href.startswith(("javascript:", "mailto:")):
            continue
        links.append(urljoin(base_url, href))
    return links


def extract_product_links(base_url: str, html_content: str) -> list[dict[str, str | None]]:
    """Extract product links in DOM order (approximating Bestsellers rank)."""
    results: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for absolute in iter_hrefs(base_url, html_content):
        if not is_amazon_url(absolute):
            continue
        if not PRODUCT_HINT_RE.search(urlparse(absolute).path):
            continue
        canonical = canonical_product_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        results.append({
            "raw_url": absolute,
            "canonical_url": canonical,
            "asin": extract_asin(canonical),
        })
    return results


def extract_category_page_links(
    base_url: str, html_content: str, target_category_path: str
) -> list[str]:
    """Find next-page links within the same Bestsellers category."""
    results: list[str] = []
    seen: set[str] = set()
    for absolute in iter_hrefs(base_url, html_content):
        if not is_amazon_url(absolute):
            continue
        canonical = canonical_category_url(absolute)
        parsed = urlparse(canonical)
        lower_path = parsed.path.lower()
        lower_url = canonical.lower()
        if "/dp/" in lower_path or "/gp/product/" in lower_path:
            continue
        if parsed.path != target_category_path:
            continue
        has_bestseller_hint = (
            "bestsellers" in lower_path
            or "zg_bs" in lower_url
            or "pg=" in parsed.query.lower()
        )
        if not has_bestseller_hint:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        results.append(canonical)
    return results


def is_probably_block_page(html_content: str) -> bool:
    content = html_content.lower()
    return any(marker in content for marker in ANTI_BOT_MARKERS)


class CategorySpider:
    """Crawl Amazon Bestsellers category pages. ASIN-agnostic, rank-tracking."""

    def __init__(self, config: CategoryCrawlConfig) -> None:
        self.config = config
        self.output_dir = self.config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.browse_node_id = extract_browse_node_id(config.category_url)
        if not self.browse_node_id:
            raise ValueError(
                f"Cannot extract Browse Node ID (codied) from URL: {config.category_url}. "
                "Expected something like /gp/bestsellers/fashion/1040658/"
            )

        self.category_slug_hint = extract_category_slug_from_url(config.category_url)

        # Category directory is keyed by Browse Node ID (codied), not by run timestamp.
        self.category_dir = self.output_dir / "categories" / self.browse_node_id
        self.category_dir.mkdir(parents=True, exist_ok=True)

        self.rankings_log_path = self.category_dir / "rankings.jsonl"
        self.current_ranking_path = self.category_dir / "current_ranking.json"
        self.meta_path = self.category_dir / "meta.json"

    def crawl_category_pages(self) -> dict[str, Any]:
        """Crawl bestseller category pages and discover product links + ranks."""
        category_queue: list[str] = [canonical_category_url(self.config.category_url)]
        visited_category: set[str] = set()
        seed_category_path = urlparse(category_queue[0]).path

        discovered_products: list[dict[str, Any]] = []
        seen_products: set[str] = set()
        category_html_paths: list[Path] = []

        category_success = 0
        category_failed = 0

        while category_queue and len(visited_category) < self.config.max_category_pages:
            category_url = category_queue.pop(0)
            if category_url in visited_category:
                continue
            visited_category.add(category_url)
            page_index = len(visited_category)
            LOGGER.info("[Category %s] %s", page_index, category_url)

            scroll_action = self._make_scroll_action() if self.config.scroll_category else None
            fetch_result = self._fetch_with_fallback(category_url, page_action=scroll_action)

            if fetch_result.response is None:
                category_failed += 1
                LOGGER.warning("Category fetch failed: %s", fetch_result.error)
                continue

            response = fetch_result.response
            html_filename = f"category_{page_index:03d}.html"
            html_path = self.category_dir / html_filename
            html_path.write_text(response.html_content, encoding="utf-8", errors="replace")
            category_html_paths.append(html_path)

            page_links = extract_product_links(response.url, response.html_content)
            for item in page_links:
                canonical = item["canonical_url"]
                if canonical in seen_products:
                    continue
                seen_products.add(canonical)
                discovered_products.append({
                    "rank": len(discovered_products) + 1,
                    "page": page_index,
                    **item,
                })

            next_pages = extract_category_page_links(
                response.url, response.html_content, seed_category_path,
            )
            for next_page in next_pages:
                if next_page in visited_category or next_page in category_queue:
                    continue
                if len(visited_category) + len(category_queue) >= self.config.max_category_pages:
                    break
                category_queue.append(next_page)

            category_success += 1
            if self.config.delay_ms > 0:
                time.sleep(self.config.delay_ms / 1000.0)

        # Persist rankings snapshot + update current_ranking.json + update meta.json
        self._write_rankings_snapshot(discovered_products)
        self._write_current_ranking(discovered_products)
        self._update_meta(discovered_products, category_success, category_failed)

        return {
            "browse_node_id": self.browse_node_id,
            "category_slug_hint": self.category_slug_hint,
            "discovered_products": discovered_products,
            "stats": {
                "category_success": category_success,
                "category_failed": category_failed,
                "products_discovered": len(discovered_products),
            },
            "paths": {
                "category_dir": str(self.category_dir),
                "rankings_jsonl": str(self.rankings_log_path),
                "meta_json": str(self.meta_path),
                "category_html_files": [str(p) for p in category_html_paths],
            },
        }

    def _write_rankings_snapshot(self, products: list[dict[str, Any]]) -> None:
        """Append a rankings snapshot to rankings.jsonl."""
        snapshot = {
            "run_at_utc": utc_now_iso(),
            "browse_node_id": self.browse_node_id,
            "product_count": len(products),
            "asins": [p.get("asin") for p in products if p.get("asin")],
            "ranks": {
                p["asin"]: p["rank"]
                for p in products if p.get("asin")
            },
        }
        with self.rankings_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

    def _write_current_ranking(self, products: list[dict[str, Any]]) -> None:
        """Write current_ranking.json — the authoritative current-rank snapshot.

        ASINs still in Top50 get their new rank; ASINs that dropped out get
        rank = -1.  This file is the single source of truth for downstream
        agents to decide which ASINs to analyze.
        """
        # Load previous current_ranking.json (if any) to detect dropped ASINs.
        prev_asins: dict[str, int] = {}
        if self.current_ranking_path.exists():
            try:
                prev_data = json.loads(self.current_ranking_path.read_text(encoding="utf-8"))
                prev_asins = prev_data.get("asins", {})
            except (OSError, json.JSONDecodeError):
                prev_asins = {}

        # Build new ranking map from this crawl.
        new_asins: dict[str, int] = {}
        for p in products:
            asin = p.get("asin")
            if asin:
                new_asins[asin] = p["rank"]

        # ASINs that were previously ranked but are no longer in the list → -1.
        for asin, rank in prev_asins.items():
            if asin not in new_asins and rank != -1:
                new_asins[asin] = -1

        current_ranking = {
            "browse_node_id": self.browse_node_id,
            "updated_at": utc_now_iso(),
            "asins": new_asins,
        }
        self.current_ranking_path.write_text(
            json.dumps(current_ranking, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _update_meta(
        self,
        products: list[dict[str, Any]],
        success: int,
        failed: int,
    ) -> None:
        """Create or update meta.json with category-level info."""
        existing: dict[str, Any] = {}
        if self.meta_path.exists():
            try:
                existing = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}

        now = utc_now_iso()
        meta = {
            "browse_node_id": self.browse_node_id,
            "category_slug_hint": self.category_slug_hint,
            "category_url": self.config.category_url,
            "first_discovered_at_utc": existing.get("first_discovered_at_utc", now),
            "last_run_at_utc": now,
            "run_count": existing.get("run_count", 0) + 1,
            "last_run_stats": {
                "category_success": success,
                "category_failed": failed,
                "products_discovered": len(products),
            },
        }
        self.meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # -----------------------------------------------------------------------
    # Fetching
    # -----------------------------------------------------------------------

    def _fetch_with_fallback(
        self, url: str, page_action: Any = None,
    ) -> FetchResult:
        if self.config.prefer_stealth:
            ordered = [("stealth", StealthyFetcher), ("dynamic", DynamicFetcher)]
        else:
            ordered = [("dynamic", DynamicFetcher), ("stealth", StealthyFetcher)]

        last_error: str | None = None

        for fetcher_name, fetcher_cls in ordered:
            for attempt in range(1, self.config.retries_per_fetcher + 1):
                try:
                    kwargs = self._build_fetch_kwargs(fetcher_name, page_action)
                    response = fetcher_cls.fetch(url, **kwargs)
                    blocked = is_probably_block_page(response.html_content)
                    if blocked and fetcher_name == "dynamic":
                        last_error = "dynamic fetch hit anti-bot page; switching fetcher"
                        LOGGER.warning("%s | %s", url, last_error)
                        break
                    return FetchResult(response=response, fetcher_name=fetcher_name,
                                       error=None, blocked=blocked)
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    LOGGER.warning("Fetch failed (%s %s/%s): %s",
                                   fetcher_name, attempt,
                                   self.config.retries_per_fetcher, last_error)
                    time.sleep(0.5 + random.random() * 0.5)

        return FetchResult(response=None, fetcher_name="none",
                           error=last_error, blocked=False)

    def _build_fetch_kwargs(self, fetcher_name: str, page_action: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "headless": self.config.headless,
            "timeout": self.config.timeout_ms,
            "wait": self.config.wait_ms,
            "network_idle": True,
            "load_dom": True,
            "disable_resources": False,
            "google_search": False,
            "retries": 1,
            "retry_delay": 1,
        }
        if self.config.useragent:
            kwargs["useragent"] = self.config.useragent
        if self.config.proxy:
            kwargs["proxy"] = self.config.proxy
        if fetcher_name == "stealth" and self.config.solve_cloudflare:
            kwargs["solve_cloudflare"] = True
        # Force US marketplace by setting headers
        if self.config.marketplace == "US":
            kwargs["headers"] = {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Currency": "USD",
            }
        if page_action is not None:
            kwargs["page_action"] = page_action
        return kwargs

    def _make_scroll_action(self):
        """page_action that sets marketplace cookies then scrolls Bestsellers to trigger lazy-loaded items."""
        pause_ms = self.config.scroll_pause_ms
        marketplace = self.config.marketplace

        def _scroll(page) -> None:
            # Force US marketplace by setting cookies before any navigation
            if marketplace == "US":
                try:
                    page.context.add_cookies([{
                        "name": "i18n-prefs",
                        "value": "USD",
                        "domain": ".amazon.com",
                        "path": "/",
                    }])
                    page.context.add_cookies([{
                        "name": "lc-main",
                        "value": "en_US",
                        "domain": ".amazon.com",
                        "path": "/",
                    }])
                    LOGGER.debug("Set US marketplace cookies (i18n-prefs=USD, lc-main=en_US)")
                except Exception as exc:
                    LOGGER.debug("Failed to set marketplace cookies: %s", exc)
            max_scrolls = 30
            prev_count = 0
            stale_rounds = 0
            for i in range(1, max_scrolls + 1):
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                page.wait_for_timeout(pause_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                count = page.evaluate("""
                    () => {
                        const links = document.querySelectorAll('a[href*="/dp/"]');
                        const asins = new Set();
                        for (const a of links) {
                            const m = a.href.match(/\\/dp\\/([A-Z0-9]{10})/i);
                            if (m) asins.add(m[1].toUpperCase());
                        }
                        return asins.size;
                    }
                """)
                LOGGER.debug("Scroll %s/%s – %s unique ASINs found", i, max_scrolls, count)
                if count >= 50:
                    LOGGER.info("Scroll complete: %s ASINs found (>= 50)", count)
                    break
                if count <= prev_count:
                    stale_rounds += 1
                    if stale_rounds >= 4:
                        LOGGER.info("No new ASINs after %s scrolls, stopping at %s",
                                    stale_rounds, count)
                        break
                else:
                    stale_rounds = 0
                prev_count = count
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(300)

        return _scroll


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl Amazon Bestsellers category pages and log rank snapshots.",
    )
    parser.add_argument("--category-url", required=True)
    parser.add_argument("--output-dir", default="workspace", type=Path,
                        help="Workspace root (default: workspace)")
    parser.add_argument("--max-category-pages", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--wait-ms", type=int, default=2500)
    parser.add_argument("--delay-ms", type=int, default=3500)
    parser.add_argument("--retries-per-fetcher", type=int, default=2)

    hl = parser.add_mutually_exclusive_group()
    hl.add_argument("--headless", dest="headless", action="store_true")
    hl.add_argument("--headful", dest="headless", action="store_false")
    parser.set_defaults(headless=True)

    stl = parser.add_mutually_exclusive_group()
    stl.add_argument("--prefer-stealth", dest="prefer_stealth", action="store_true", default=True)
    stl.add_argument("--no-prefer-stealth", dest="prefer_stealth", action="store_false")

    parser.add_argument("--solve-cloudflare", action="store_true")
    parser.add_argument("--useragent", default=None)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--no-scroll", action="store_true")
    parser.add_argument("--scroll-pause-ms", type=int, default=1500)
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> CategoryCrawlConfig:
    return CategoryCrawlConfig(
        category_url=args.category_url,
        output_dir=args.output_dir.resolve(),
        max_category_pages=args.max_category_pages,
        timeout_ms=args.timeout_ms,
        wait_ms=args.wait_ms,
        delay_ms=args.delay_ms,
        retries_per_fetcher=args.retries_per_fetcher,
        headless=args.headless,
        prefer_stealth=args.prefer_stealth,
        solve_cloudflare=args.solve_cloudflare,
        useragent=args.useragent,
        proxy=args.proxy,
        scroll_category=not args.no_scroll,
        scroll_pause_ms=args.scroll_pause_ms,
    )


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    config = build_config(args)
    spider = CategorySpider(config)
    result = spider.crawl_category_pages()
    LOGGER.info("Browse Node ID: %s", result["browse_node_id"])
    LOGGER.info("Category dir: %s", spider.category_dir)
    LOGGER.info("Stats: %s", json.dumps(result["stats"], ensure_ascii=False))
    return 0 if result["stats"]["category_success"] > 0 else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
