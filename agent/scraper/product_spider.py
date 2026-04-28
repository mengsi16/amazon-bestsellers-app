#!/usr/bin/env python3
"""
Product Detail Page Crawler — 爬取 Amazon 商品详情页 HTML（ASIN 为中心）。

输出布局：
    {output_dir}/{ASIN}/product.html
    {output_dir}/{ASIN}/meta.json
    {output_dir}/requests.jsonl   (全局爬取日志 append-only)

幂等跳过：如果 ``{ASIN}/product.html`` 存在、size > 500 KB 且含商品页标记，
          则默认跳过该 ASIN。``--force`` 可强制重爬。

Usage:
    # 单个商品
    python product_spider.py --urls "https://www.amazon.com/dp/B0XXXXX" --output-dir ./products

    # 多个商品（并发）
    python product_spider.py --urls "https://..." "https://..." --output-dir ./products

    # 从文件读取 URL
    python product_spider.py --url-file urls.txt --output-dir ./products --max-concurrency 3
"""

from __future__ import annotations

import argparse
import asyncio
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
from urllib.parse import urlparse, urlunparse

from scrapling.engines.toolbelt.custom import Response
from scrapling.fetchers import AsyncStealthySession, DynamicFetcher, StealthyFetcher

LOGGER = logging.getLogger("product_spider")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", re.IGNORECASE)

ANTI_BOT_MARKERS = (
    "captcha",
    "robot check",
    "type the characters you see in this image",
    "enter the characters you see below",
    "sorry, we just need to make sure you're not a robot",
    "validatecaptcha",
)

SERVICE_ERROR_MARKERS = (
    "503 - service unavailable error",
    "service unavailable error",
    "sorry! something went wrong",
)

PRODUCT_PAGE_MARKERS = (
    "producttitle",
    "product-title",
    "data-asin",
    "buybox",
    "dp-container",
    "centerCol",
    "centercol",
    "ppd",
    "#aplus_feature_div",
    "productDescription",
)

# Markers that indicate the page is NOT a product detail page even if some
# PRODUCT_PAGE_MARKERS coincidentally matched (e.g. Amazon homepage CSS
# contains "asin-add-to-cart-button" in carousel cards).
NON_PRODUCT_PAGE_MARKERS = (
    "spend less. smile more.",  # Amazon.com homepage <title>
    "amazon.com. spend less",   # Amazon.com homepage <title> variant
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductCrawlConfig:
    output_dir: Path
    max_products: int | None = None
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
    retry_backoff_ms: int = 60000


@dataclass(frozen=True)
class FetchResult:
    response: Response | None
    fetcher_name: str
    error: str | None
    blocked: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_hash(value: str) -> str:
    return sha1(value.encode("utf-8")).hexdigest()[:16]


def safe_slug(value: str, max_length: int = 96) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not normalized:
        normalized = "item"
    return normalized[:max_length]


def extract_asin(url: str) -> str | None:
    match = ASIN_RE.search(url)
    if not match:
        return None
    return match.group(1).upper()


def canonical_product_url(url: str) -> str:
    parsed = urlparse(url)
    asin = extract_asin(url)
    if asin:
        return urlunparse((parsed.scheme or "https", parsed.netloc, f"/dp/{asin}", "", "", ""))
    path = parsed.path.split("/ref=", 1)[0]
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))


def is_probably_block_page(html_content: str) -> bool:
    content = html_content.lower()
    return any(marker in content for marker in ANTI_BOT_MARKERS)


def is_service_error_page(html_content: str) -> bool:
    content = html_content.lower()
    return any(marker in content for marker in SERVICE_ERROR_MARKERS)


def is_valid_product_page(html_content: str) -> bool:
    if is_probably_block_page(html_content) or is_service_error_page(html_content):
        return False
    content = html_content.lower()
    # Exclude pages that look like Amazon homepage / gateway even if some
    # product markers coincidentally match (e.g. CSS class "asin-add-to-cart-button"
    # appears in homepage carousel cards).
    if any(marker in content for marker in NON_PRODUCT_PAGE_MARKERS):
        return False
    return any(marker in content for marker in PRODUCT_PAGE_MARKERS)


# ---------------------------------------------------------------------------
# Product Spider
# ---------------------------------------------------------------------------

# Minimum byte size to accept an already-downloaded product.html as "complete".
# Captcha/503 pages are a few KB; a real Amazon product page is always > 500 KB.
MIN_VALID_HTML_BYTES = 500_000


def asin_dir_for(output_root: Path, asin_or_id: str) -> Path:
    """Return the canonical per-ASIN directory (``{output_root}/{ASIN}``)."""
    return output_root / safe_slug(asin_or_id, max_length=32)


def product_html_path(output_root: Path, asin_or_id: str) -> Path:
    return asin_dir_for(output_root, asin_or_id) / "product.html"


def product_meta_path(output_root: Path, asin_or_id: str) -> Path:
    return asin_dir_for(output_root, asin_or_id) / "meta.json"


def is_crawl_done(output_root: Path, asin_or_id: str) -> bool:
    """Check if this ASIN already has a valid, complete product.html."""
    html_path = product_html_path(output_root, asin_or_id)
    if not html_path.exists():
        return False
    if html_path.stat().st_size < MIN_VALID_HTML_BYTES:
        return False
    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return is_valid_product_page(html)


class ProductSpider:
    """Crawl Amazon product detail pages with async concurrency (ASIN-centric)."""

    def __init__(self, config: ProductCrawlConfig) -> None:
        self.config = config
        self.output_dir = self.config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.requests_log_path = self.output_dir / "requests.jsonl"

    async def crawl_product_details(
        self,
        product_list: list[dict[str, Any]],
        max_concurrency: int = 3,
        force: bool = False,
        max_rounds: int = 3,
        inter_round_delay_s: float = 30.0,
    ) -> dict[str, Any]:
        """Crawl product detail pages with async concurrency and queue-based retry.

        Each round makes a single attempt per ASIN without inline sleep.
        Failed ASINs are pushed to the next round's queue; the browser is only
        idle during the brief inter-round cooldown, keeping the semaphore free
        for other ASINs in the same round.

        Args:
            product_list: List of product dicts with ``canonical_url``
                          (and optionally ``asin``).
            max_concurrency: Max concurrent browser tabs (default 3).
            force: If True, re-crawl ASINs that already have a valid
                   ``product.html`` on disk.
            max_rounds: Maximum retry rounds for failed ASINs (default 3).
            inter_round_delay_s: Cooldown in seconds between rounds (default 30).

        Returns:
            Dict with ``results`` list and ``stats`` summary.
        """
        if not product_list:
            return {
                "results": [],
                "stats": {"product_targets": 0, "product_success": 0,
                          "product_failed": 0, "product_skipped": 0},
            }

        # Partition into skippable vs. to-crawl
        to_crawl: list[dict[str, Any]] = []
        skipped_results: list[dict[str, Any]] = []
        for item in product_list:
            asin = item.get("asin") or short_hash(str(item["canonical_url"]))
            if not force and is_crawl_done(self.output_dir, asin):
                LOGGER.info("[Skip] %s — product.html already present & valid", asin)
                skipped_results.append({
                    "request_type": "product",
                    "requested_url": item["canonical_url"],
                    "asin": item.get("asin"),
                    "status": "SKIPPED",
                    "reason": "already_downloaded",
                    "html_file": str(product_html_path(self.output_dir, asin)),
                })
            else:
                to_crawl.append(item)

        total_targets = len(to_crawl)
        product_success = 0
        product_failed = 0

        LOGGER.info(
            "Crawling %s product detail pages (concurrency=%s, max_rounds=%s, skipped=%s)",
            total_targets, max_concurrency, max_rounds, len(skipped_results),
        )

        if total_targets == 0:
            return {
                "results": skipped_results,
                "stats": {
                    "product_targets": len(product_list),
                    "product_success": 0,
                    "product_failed": 0,
                    "product_skipped": len(skipped_results),
                },
            }

        final_crawl_results: list[dict[str, Any]] = []

        async with AsyncStealthySession(
            max_pages=max_concurrency,
            headless=self.config.headless,
        ) as session:
            fetch_kwargs: dict[str, Any] = {
                "google_search": True,
                "timeout": self.config.timeout_ms,
                "wait": self.config.wait_ms,
                "network_idle": True,
                "load_dom": True,
            }
            if self.config.proxy:
                fetch_kwargs["proxy"] = self.config.proxy
            if self.config.solve_cloudflare:
                fetch_kwargs["solve_cloudflare"] = True

            # Force US marketplace by setting headers
            if self.config.marketplace == "US":
                fetch_kwargs["headers"] = {
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Currency": "USD",
                }

            scroll_pause_ms = max(400, self.config.wait_ms // 3)

            async def _bypass_captcha(page) -> bool:
                """Amazon 'Continue shopping' captcha 自动通过。返回是否触发过 bypass。"""
                form = await page.query_selector('form[action*="validateCaptcha"]')
                if not form:
                    return False
                LOGGER.warning("Detected Amazon captcha form, attempting auto-bypass...")
                button = await form.query_selector('button[type="submit"]')
                if not button:
                    return False
                await button.click()
                try:
                    await page.wait_for_url(
                        lambda url: "validateCaptcha" not in url and "opfcaptcha" not in url,
                        timeout=20000,
                    )
                except Exception:
                    pass
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                LOGGER.info("Captcha bypass submitted, current url: %s", page.url)
                return True

            async def _trigger_lazy_load(page) -> None:
                """分段下拉触发 Amazon 懒加载模块（A+、评论、推荐位）。"""
                try:
                    viewport_height = await page.evaluate("window.innerHeight")
                except Exception:
                    viewport_height = 900

                max_steps = 20
                aplus_selector = (
                    "#aplus_feature_div, #aplusBrandFeatureDiv, "
                    "#aplusBrandStory_feature_div, #dpx-aplus-brand-feature_div, "
                    ".aplus-module, .aplus-v2"
                )

                prev_scroll = -1
                for step in range(1, max_steps + 1):
                    try:
                        await page.evaluate(
                            f"window.scrollBy(0, {viewport_height})"
                        )
                        await page.wait_for_timeout(scroll_pause_ms)
                        try:
                            await page.wait_for_load_state(
                                "networkidle", timeout=3000
                            )
                        except Exception:
                            pass

                        # Check whether A+ content has materialized yet.
                        has_aplus = await page.evaluate(f"""
                            (() => {{
                                const el = document.querySelector({aplus_selector!r});
                                if (!el) return false;
                                return el.innerText.trim().length > 50
                                    || el.querySelectorAll('img').length > 0;
                            }})()
                        """)
                        if has_aplus:
                            await page.wait_for_timeout(1200)
                            LOGGER.debug("A+ content detected after step %s", step)
                            break

                        # Stop if we've reached bottom (scrollY plateaued).
                        scroll_y = await page.evaluate(
                            "window.scrollY + window.innerHeight"
                        )
                        doc_height = await page.evaluate(
                            "document.documentElement.scrollHeight"
                        )
                        if scroll_y >= doc_height - 50:
                            break
                        if scroll_y == prev_scroll:
                            break
                        prev_scroll = scroll_y
                    except Exception as exc:
                        LOGGER.debug("Lazy-load scroll step %s failed: %s", step, exc)
                        break

                # Scroll back to top for deterministic output (optional, cosmetic).
                try:
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(200)
                except Exception:
                    pass

            async def _prepare_product_page(page) -> None:
                """page_action 主流程：设置地区 Cookie → 过 captcha → reload → 滚动触发懒加载。"""
                try:
                    # Force US marketplace by setting cookies before any navigation
                    if self.config.marketplace == "US":
                        try:
                            await page.context.add_cookies([{
                                "name": "i18n-prefs",
                                "value": "USD",
                                "domain": ".amazon.com",
                                "path": "/",
                            }])
                            await page.context.add_cookies([{
                                "name": "lc-main",
                                "value": "en_US",
                                "domain": ".amazon.com",
                                "path": "/",
                            }])
                            LOGGER.debug("Set US marketplace cookies (i18n-prefs=USD, lc-main=en_US)")
                        except Exception as exc:
                            LOGGER.debug("Failed to set marketplace cookies: %s", exc)

                    bypassed = await _bypass_captcha(page)
                    if bypassed:
                        try:
                            await page.reload(wait_until="domcontentloaded",
                                              timeout=30000)
                            await page.wait_for_load_state(
                                "networkidle", timeout=15000,
                            )
                            LOGGER.info("Reloaded after captcha bypass.")
                        except Exception as exc:
                            LOGGER.debug("Reload after bypass failed: %s", exc)

                    await _trigger_lazy_load(page)
                except Exception as exc:
                    LOGGER.debug("page_action error (ignored): %s", exc)

            fetch_kwargs["page_action"] = _prepare_product_page

            async def _fetch_one(
                index: int, item: dict[str, Any], round_num: int
            ) -> dict[str, Any]:
                """Single attempt — no inline retry, no sleep.
                On failure returns a record with valid_product_page=False;
                the caller decides whether to retry in the next round.
                """
                target_url = str(item["canonical_url"])
                asin = item.get("asin")
                product_id = asin if asin else short_hash(target_url)
                asin_output_dir = asin_dir_for(self.output_dir, product_id)
                asin_output_dir.mkdir(parents=True, exist_ok=True)

                LOGGER.info("[R%s][%s/%s] %s", round_num, index, total_targets, target_url)

                try:
                    response = await session.fetch(target_url, **fetch_kwargs)
                except Exception as exc:  # noqa: BLE001
                    error_str = f"{type(exc).__name__}: {exc}"
                    LOGGER.warning("[R%s][%s] fetch failed: %s", round_num, product_id, error_str)
                    record: dict[str, Any] = {
                        "request_type": "product",
                        "requested_url": target_url,
                        "asin": asin,
                        "fetcher": "async_stealth",
                        "attempted_at_utc": utc_now_iso(),
                        "round": round_num,
                        "error": error_str,
                        "blocked": True,
                        "valid_product_page": False,
                    }
                    self._append_jsonl(self.requests_log_path, record)
                    return record

                html = response.html_content
                valid = is_valid_product_page(html)

                if not valid:
                    reason = (
                        "503/service-error" if is_service_error_page(html)
                        else "captcha/block" if is_probably_block_page(html)
                        else "no-product-markers"
                    )
                    LOGGER.warning(
                        "[R%s][%s] got %s page (%s bytes)",
                        round_num, product_id, reason, len(html),
                    )

                html_path = asin_output_dir / "product.html"
                html_path.write_text(html, encoding="utf-8", errors="replace")

                record = {
                    "request_type": "product",
                    "requested_url": target_url,
                    "asin": asin,
                    "fetcher": "async_stealth",
                    "attempted_at_utc": utc_now_iso(),
                    "round": round_num,
                    "error": None if valid else (reason if not valid else None),
                    "blocked": not valid,
                    "valid_product_page": valid,
                    "status_code": getattr(response, "status", None),
                    "final_url": response.url,
                    "html_file": str(html_path),
                    "html_size": len(html),
                }
                self._append_jsonl(self.requests_log_path, record)

                if valid:
                    meta_path = asin_output_dir / "meta.json"
                    meta = {
                        "asin": asin,
                        "requested_url": target_url,
                        "final_url": response.url,
                        "status_code": getattr(response, "status", None),
                        "html_size": len(html),
                        "valid_product_page": True,
                        "fetched_at_utc": record["attempted_at_utc"],
                    }
                    meta_path.write_text(
                        json.dumps(meta, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                else:
                    LOGGER.warning("[R%s][%s] saved but marked INVALID", round_num, product_id)

                return record

            # ---------------------------------------------------------------
            # Queue-based multi-round loop
            # ---------------------------------------------------------------
            retry_queue = list(to_crawl)

            for round_num in range(1, max_rounds + 1):
                if not retry_queue:
                    LOGGER.info("Round %s/%s: queue empty, done.", round_num, max_rounds)
                    break

                round_size = len(retry_queue)
                LOGGER.info(
                    "Round %s/%s: %s ASINs to crawl",
                    round_num, max_rounds, round_size,
                )

                semaphore = asyncio.Semaphore(max_concurrency)

                async def _throttled(
                    index: int,
                    item: dict[str, Any],
                    rn: int = round_num,
                ) -> dict[str, Any]:
                    async with semaphore:
                        if index > 1 and self.config.delay_ms > 0:
                            jitter = random.uniform(0.5, 1.5)
                            await asyncio.sleep(self.config.delay_ms / 1000.0 * jitter)
                        return await _fetch_one(index, item, rn)

                tasks = [
                    _throttled(i, item)
                    for i, item in enumerate(retry_queue, start=1)
                ]
                round_raw = await asyncio.gather(*tasks, return_exceptions=True)

                next_retry: list[dict[str, Any]] = []
                round_success = 0

                for item, result in zip(retry_queue, round_raw):
                    if isinstance(result, Exception):
                        LOGGER.error("Unexpected gather exception for %s: %s",
                                     item.get("asin", "?"), result)
                        if round_num < max_rounds:
                            next_retry.append(item)
                        else:
                            product_failed += 1
                            final_crawl_results.append({
                                "request_type": "product",
                                "requested_url": str(item["canonical_url"]),
                                "asin": item.get("asin"),
                                "error": str(result),
                                "blocked": True,
                                "valid_product_page": False,
                                "round": round_num,
                            })
                    elif result.get("valid_product_page"):
                        product_success += 1
                        round_success += 1
                        final_crawl_results.append(result)
                    else:
                        if round_num < max_rounds:
                            next_retry.append(item)
                        else:
                            product_failed += 1
                            final_crawl_results.append(result)

                LOGGER.info(
                    "Round %s/%s complete: %s success, %s failed/queued",
                    round_num, max_rounds, round_success,
                    len(next_retry) + (round_size - round_success - len(next_retry)),
                )

                retry_queue = next_retry

                if retry_queue and round_num < max_rounds:
                    LOGGER.info(
                        "Cooling down %.0fs before round %s (%s ASINs remaining) …",
                        inter_round_delay_s, round_num + 1, len(retry_queue),
                    )
                    await asyncio.sleep(inter_round_delay_s)

        final_results: list[dict[str, Any]] = list(skipped_results) + final_crawl_results

        stats = {
            "product_targets": len(product_list),
            "product_success": product_success,
            "product_failed": product_failed,
            "product_skipped": len(skipped_results),
        }
        LOGGER.info("Crawl complete: %s", json.dumps(stats, ensure_ascii=False))
        return {"results": final_results, "stats": stats}

    def crawl_single(self, url: str) -> dict[str, Any]:
        """Crawl a single product page synchronously (convenience wrapper)."""
        return asyncio.run(self.crawl_product_details(
            [{"canonical_url": url, "asin": extract_asin(url)}],
            max_concurrency=1,
        ))

    # -----------------------------------------------------------------------
    # Sync fetch with fallback (for single-page use outside async context)
    # -----------------------------------------------------------------------

    def fetch_page(self, url: str, force_stealth: bool = False) -> FetchResult:
        """Fetch a single page synchronously with fetcher fallback."""
        ordered_fetchers: list[tuple[str, type[Any]]]
        if force_stealth:
            ordered_fetchers = [("stealth", StealthyFetcher)]
        elif self.config.prefer_stealth:
            ordered_fetchers = [("stealth", StealthyFetcher), ("dynamic", DynamicFetcher)]
        else:
            ordered_fetchers = [("dynamic", DynamicFetcher), ("stealth", StealthyFetcher)]

        last_error: str | None = None

        for fetcher_name, fetcher_cls in ordered_fetchers:
            for attempt in range(1, self.config.retries_per_fetcher + 1):
                try:
                    kwargs: dict[str, Any] = {
                        "headless": self.config.headless,
                        "timeout": self.config.timeout_ms,
                        "wait": self.config.wait_ms,
                        "network_idle": True,
                        "load_dom": True,
                        "disable_resources": False,
                        "google_search": True,
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

                    response = fetcher_cls.fetch(url, **kwargs)
                    blocked = is_probably_block_page(response.html_content)

                    should_fallback = (
                        blocked
                        and fetcher_name == "dynamic"
                        and any(name == "stealth" for name, _ in ordered_fetchers)
                    )
                    if should_fallback:
                        last_error = "dynamic fetch likely hit anti-bot page; switching fetcher"
                        LOGGER.warning("%s | %s", url, last_error)
                        break

                    return FetchResult(
                        response=response,
                        fetcher_name=fetcher_name,
                        error=None,
                        blocked=blocked,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{type(exc).__name__}: {exc}"
                    LOGGER.warning(
                        "Fetch failed (%s attempt %s/%s): %s",
                        fetcher_name, attempt,
                        self.config.retries_per_fetcher, last_error,
                    )
                    time.sleep(0.5 + random.random() * 0.5)

        return FetchResult(response=None, fetcher_name="none", error=last_error, blocked=False)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl Amazon product detail pages and save raw HTML (ASIN-centric).",
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        help="One or more Amazon product URLs to crawl",
    )
    parser.add_argument(
        "--url-file",
        type=Path,
        default=None,
        help="File with one product URL per line",
    )
    parser.add_argument(
        "--output-dir",
        default="products",
        help="Directory where per-ASIN artifacts are stored (default: products)",
    )
    parser.add_argument(
        "--max-products",
        type=int,
        default=None,
        help="Maximum product pages to crawl",
    )
    parser.add_argument("--timeout-ms", type=int, default=90000)
    parser.add_argument("--wait-ms", type=int, default=2500)
    parser.add_argument("--delay-ms", type=int, default=3500)
    parser.add_argument("--retries-per-fetcher", type=int, default=2)
    parser.add_argument("--retry-backoff-ms", type=int, default=60000)
    parser.add_argument("--max-concurrency", type=int, default=3)

    headless_group = parser.add_mutually_exclusive_group()
    headless_group.add_argument("--headless", dest="headless", action="store_true")
    headless_group.add_argument("--headful", dest="headless", action="store_false")
    parser.set_defaults(headless=True)

    stealth_group = parser.add_mutually_exclusive_group()
    stealth_group.add_argument("--prefer-stealth", dest="prefer_stealth", action="store_true", default=True)
    stealth_group.add_argument("--no-prefer-stealth", dest="prefer_stealth", action="store_false")

    parser.add_argument("--solve-cloudflare", action="store_true", default=True)
    parser.add_argument("--no-solve-cloudflare", dest="solve_cloudflare", action="store_false")
    parser.add_argument("--useragent", default=None)
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--force", action="store_true",
                        help="Re-crawl ASINs even if a valid product.html already exists")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> ProductCrawlConfig:
    if args.timeout_ms < 1:
        raise ValueError("--timeout-ms must be >= 1")
    if args.wait_ms < 0:
        raise ValueError("--wait-ms must be >= 0")
    if args.delay_ms < 0:
        raise ValueError("--delay-ms must be >= 0")

    return ProductCrawlConfig(
        output_dir=Path(args.output_dir).resolve(),
        max_products=args.max_products,
        timeout_ms=args.timeout_ms,
        wait_ms=args.wait_ms,
        delay_ms=args.delay_ms,
        retries_per_fetcher=args.retries_per_fetcher,
        headless=args.headless,
        prefer_stealth=args.prefer_stealth,
        solve_cloudflare=args.solve_cloudflare,
        useragent=args.useragent,
        proxy=args.proxy,
        retry_backoff_ms=args.retry_backoff_ms,
    )


def load_product_list(args: argparse.Namespace) -> list[dict[str, Any]]:
    urls: list[str] = []
    if args.urls:
        urls.extend(args.urls)
    if args.url_file:
        with args.url_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    if not urls:
        raise ValueError("No product URLs provided. Use --urls or --url-file.")

    seen: set[str] = set()
    product_list: list[dict[str, Any]] = []
    for url in urls:
        asin = extract_asin(url)
        dedup_key = asin if asin else canonical_product_url(url)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        product_list.append({
            "canonical_url": url,
            "asin": asin,
        })

    return product_list


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    try:
        config = build_config(args)
    except ValueError as exc:
        LOGGER.error("Invalid arguments: %s", exc)
        return 2

    product_list = load_product_list(args)
    if config.max_products is not None:
        product_list = product_list[: config.max_products]

    LOGGER.info("Loaded %s product URLs", len(product_list))

    spider = ProductSpider(config)
    result = asyncio.run(spider.crawl_product_details(
        product_list,
        max_concurrency=args.max_concurrency,
        force=args.force,
    ))

    LOGGER.info("Output: %s", spider.output_dir)
    LOGGER.info("Stats: %s", json.dumps(result["stats"], ensure_ascii=False))

    stats = result["stats"]
    if stats["product_success"] == 0 and stats.get("product_skipped", 0) == 0:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
