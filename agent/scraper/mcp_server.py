"""MCP Server for Amazon Bestsellers Scraper.

Exposes four tools for Claude-Code agents:
  1. crawl_bestseller_list     — 爬取类目列表页，写入 {workspace}/categories/{browse_node_id}/
  2. crawl_product_details     — 爬取商品详情页（ASIN 去重），写入 {workspace}/products/{ASIN}/
                                  默认自动串联 listing + A+ 提取
  3. extract_listing_images    — 从 {workspace}/products/{ASIN}/product.html 提取 listing 图
  4. extract_aplus_images      — 从 {workspace}/products/{ASIN}/product.html 提取 A+ 图

Workspace 目录约定：
    {workspace}/
    ├── categories/{browse_node_id}/     ← 类目日志（排名快照 append-only）
    │   ├── category_001.html
    │   ├── rankings.jsonl
    │   └── meta.json
    ├── products/{ASIN}/                  ← 全局 ASIN 去重
    │   ├── product.html
    │   ├── meta.json
    │   ├── listing-images/
    │   └── aplus-images/
    ├── chunks/                           (chunker 产出)
    ├── reports/                          (analyst 产出)
    └── summary.md

Usage (stdio mode):
    python scraper/mcp_server.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from category_spider import (
    CategoryCrawlConfig,
    CategorySpider,
    extract_browse_node_id,
)
from product_spider import (
    ProductCrawlConfig,
    ProductSpider,
    canonical_product_url,
    extract_asin,
)
from extract_listing_images import process_asin as process_listing_asin
from extract_aplus import process_asin as process_aplus_asin

LOGGER = logging.getLogger("mcp_amazon_scraper")

mcp = FastMCP(
    "Amazon Bestsellers Scraper",
    instructions=(
        "Crawl Amazon Bestsellers category pages & product detail pages, "
        "and extract listing / A+ poster images. "
        "IMPORTANT: output_dir MUST be an absolute workspace root path provided by the "
        "orchestrator (e.g. {workspace}). Products are de-duplicated globally by ASIN."
    ),
)

# ---------------------------------------------------------------------------
# Windows path normalization (same as before)
# ---------------------------------------------------------------------------

WINDOWS_ROOTED_DRIVE_SEGMENT_RE = re.compile(r"^[\\/](?P<drive>[A-Za-z])[\\/](?P<rest>.*)$")
WINDOWS_MISSING_COLON_RE = re.compile(r"^(?P<drive>[A-Za-z])[\\/](?P<rest>.*)$")
WINDOWS_DRIVE_RELATIVE_RE = re.compile(r"^(?P<drive>[A-Za-z]):(?P<rest>[^\\/].*)$")


def _coerce_windows_absolute_path(raw_path: str) -> str:
    """Fix common malformed Windows absolute paths produced by agents."""
    candidate = raw_path.strip().strip('"').strip("'")
    if not candidate or os.name != "nt":
        return candidate

    current_drive = Path.cwd().drive.rstrip(":").upper()

    for pattern in (
        WINDOWS_ROOTED_DRIVE_SEGMENT_RE,
        WINDOWS_MISSING_COLON_RE,
        WINDOWS_DRIVE_RELATIVE_RE,
    ):
        match = pattern.match(candidate)
        if not match:
            continue
        drive = match.group("drive").upper()
        if drive != current_drive:
            return candidate
        rest = match.group("rest").lstrip("\\/").replace("\\", "/")
        return f"{drive}:/{rest}" if rest else f"{drive}:/"

    return candidate


def _resolve_workspace_path(output_dir: str) -> Path:
    """Normalize + validate workspace root absolute path."""
    coerced = _coerce_windows_absolute_path(output_dir)
    if coerced != output_dir:
        LOGGER.warning("Normalized output_dir '%s' -> '%s'", output_dir, coerced)
    path = Path(coerced)
    if not path.is_absolute():
        raise ValueError(
            f"output_dir must be an absolute path. Received: '{output_dir}'. "
            "Orchestrator must pass the full workspace root path."
        )
    return path.resolve()


# ---------------------------------------------------------------------------
# Tool 1: Crawl Bestseller Category List
# ---------------------------------------------------------------------------

@mcp.tool()
async def crawl_bestseller_list(
    category_url: str,
    output_dir: str,
    max_category_pages: int = 1,
    delay_ms: int = 3500,
    headless: bool = True,
    scroll_category: bool = True,
    proxy: str | None = None,
    marketplace: str = "US",
) -> dict[str, Any]:
    """Crawl Amazon Bestsellers category pages and discover Top50/Top100 product links.

    Writes to:
      {workspace}/categories/{browse_node_id}/
        ├── category_001.html   (listing page HTML)
        ├── meta.json           (category metadata)
        └── rankings.jsonl      (rank snapshot, append-only per run)

    Args:
        category_url: Amazon Bestsellers URL
            (e.g. "https://www.amazon.com/gp/bestsellers/fashion/1040658/")
        output_dir: **Absolute path** to the workspace root directory.
            Products + categories + chunks + reports all live under this.
        max_category_pages: Max pagination pages to crawl (default: 1)
        delay_ms: Delay between requests in ms (default: 3500)
        headless: Run browser headlessly (default: True)
        scroll_category: Auto-scroll to trigger lazy-loaded items (default: True)
        proxy: Optional proxy string
        marketplace: Amazon marketplace to target (default: "US").
            Supported: US, JP, UK, DE, FR, CA, AU.
            When "US", sets Accept-Language/Accept-Currency headers and
            i18n-prefs/lc-main cookies to force US content.

    Returns:
        Dict with browse_node_id, discovered products (ASIN + rank),
        stats, and output paths.
    """
    workspace = _resolve_workspace_path(output_dir)
    browse_node_id = extract_browse_node_id(category_url)
    if not browse_node_id:
        return {
            "error": (
                f"Cannot extract Browse Node ID (codied) from URL: {category_url}. "
                "Expected something like /gp/bestsellers/fashion/1040658/"
            ),
        }

    config = CategoryCrawlConfig(
        category_url=category_url,
        output_dir=workspace,
        max_category_pages=max_category_pages,
        delay_ms=delay_ms,
        headless=headless,
        scroll_category=scroll_category,
        proxy=proxy,
        marketplace=marketplace,
    )

    spider = CategorySpider(config)
    # Run sync Playwright in a thread pool to avoid asyncio loop conflict.
    result = await asyncio.to_thread(spider.crawl_category_pages)

    return {
        "browse_node_id": result["browse_node_id"],
        "category_slug_hint": result["category_slug_hint"],
        "product_count": len(result["discovered_products"]),
        "products": [
            {
                "canonical_url": p["canonical_url"],
                "asin": p.get("asin"),
                "rank": p.get("rank"),
            }
            for p in result["discovered_products"]
        ],
        "stats": result["stats"],
        "paths": result["paths"],
        "current_ranking_path": str(spider.current_ranking_path),
    }


# ---------------------------------------------------------------------------
# Tool 2: Crawl Product Details (ASIN de-duplicated)
# ---------------------------------------------------------------------------

@mcp.tool()
async def crawl_product_details(
    product_urls: list[str],
    output_dir: str,
    max_concurrency: int = 3,
    delay_ms: int = 3500,
    headless: bool = True,
    solve_cloudflare: bool = False,
    proxy: str | None = None,
    auto_extract_images: bool = True,
    force: bool = False,
    max_rounds: int = 3,
    inter_round_delay_s: float = 30.0,
    marketplace: str = "US",
) -> dict[str, Any]:
    """Crawl Amazon product detail pages with async concurrency + ASIN-based de-duplication.

    Already-crawled ASINs (valid product.html exists, > 500KB, contains product markers)
    are **skipped by default** unless ``force=True``.

    Uses queue-based retry: each round makes one attempt per ASIN without
    inline sleep. Failed ASINs are queued for the next round, keeping
    semaphore slots free for other ASINs in the same round.

    Writes to:
      {workspace}/products/{ASIN}/
        ├── product.html
        ├── meta.json
        ├── listing-images/urls.json + images/  (if auto_extract_images)
        └── aplus-images/urls.json + images/    (if auto_extract_images)

    Args:
        product_urls: List of Amazon product URLs to crawl
            (e.g. ["https://www.amazon.com/dp/B0DRNRC5H5"])
        output_dir: **Absolute path** to the workspace root directory.
        max_concurrency: Max concurrent browser tabs (default: 3)
        delay_ms: Delay between requests in ms (default: 3500)
        headless: Run browser headlessly (default: True)
        solve_cloudflare: Enable Cloudflare bypass (default: False; Amazon does not use Cloudflare)
        proxy: Optional proxy string
        auto_extract_images: Auto-run listing + A+ extraction after each
            successful crawl (default: True)
        force: Re-crawl ASINs even if valid product.html already exists
        max_rounds: Max retry rounds for failed ASINs (default: 3)
        inter_round_delay_s: Cooldown seconds between rounds (default: 30)
        marketplace: Amazon marketplace to target (default: "US").
            Supported: US, JP, UK, DE, FR, CA, AU.
            When "US", sets Accept-Language/Accept-Currency headers and
            i18n-prefs/lc-main cookies to force US content.

    Returns:
        Dict with stats + per-product results (including extraction outcomes).
    """
    workspace = _resolve_workspace_path(output_dir)
    products_root = workspace / "products"
    products_root.mkdir(parents=True, exist_ok=True)

    config = ProductCrawlConfig(
        output_dir=products_root,
        delay_ms=delay_ms,
        headless=headless,
        solve_cloudflare=solve_cloudflare,
        proxy=proxy,
        marketplace=marketplace,
    )
    spider = ProductSpider(config)

    seen: set[str] = set()
    product_list = []
    for url in product_urls:
        asin = extract_asin(url)
        dedup_key = asin if asin else canonical_product_url(url)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        product_list.append({
            "canonical_url": url,
            "asin": asin,
        })

    crawl_result = await spider.crawl_product_details(
        product_list,
        max_concurrency=max_concurrency,
        force=force,
        max_rounds=max_rounds,
        inter_round_delay_s=inter_round_delay_s,
    )

    # Auto-extract listing + A+ images for every successfully-crawled ASIN.
    extraction_results: list[dict[str, Any]] = []
    if auto_extract_images:
        for item in product_list:
            asin = item.get("asin")
            if not asin:
                continue
            asin_dir = products_root / asin
            if not (asin_dir / "product.html").exists():
                extraction_results.append({
                    "asin": asin,
                    "listing": {"status": "SKIPPED", "reason": "no product.html"},
                    "aplus": {"status": "SKIPPED", "reason": "no product.html"},
                })
                continue
            # Run extractions in threads (CPU/IO-bound, not asyncio-native).
            listing_res = await asyncio.to_thread(
                process_listing_asin, asin_dir, True, force,
            )
            aplus_res = await asyncio.to_thread(
                process_aplus_asin, asin_dir, True, force,
            )
            extraction_results.append({
                "asin": asin,
                "listing": listing_res,
                "aplus": aplus_res,
            })

    return {
        "stats": crawl_result["stats"],
        "crawl_results": [
            {
                "url": r.get("requested_url"),
                "asin": r.get("asin"),
                "status": r.get("status"),
                "valid": r.get("valid_product_page"),
                "html_size": r.get("html_size"),
                "html_file": r.get("html_file"),
                "error": r.get("error"),
            }
            for r in crawl_result["results"]
        ],
        "extraction_results": extraction_results,
    }


# ---------------------------------------------------------------------------
# Tool 3: Extract listing images (single ASIN)
# ---------------------------------------------------------------------------

@mcp.tool()
async def extract_listing_images(
    asin: str,
    output_dir: str,
    download: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Extract listing (poster) images for a single ASIN from its cached product.html.

    Requires {workspace}/products/{ASIN}/product.html to exist (run
    ``crawl_product_details`` first). Writes:
      {workspace}/products/{ASIN}/listing-images/
        ├── urls.json
        └── images/listing_img_NNN.jpg  (if download=True)

    Args:
        asin: Amazon ASIN (e.g. "B0DRNRC5H5")
        output_dir: **Absolute path** to the workspace root directory.
        download: Download images to disk (default: True). If False, only
            URLs are extracted (urls.json still written).
        force: Re-extract even if listing-images/urls.json already exists.

    Returns:
        Dict with asin, status, image_count, download_status, urls_file path.
    """
    workspace = _resolve_workspace_path(output_dir)
    asin_dir = workspace / "products" / asin.upper()
    if not asin_dir.exists():
        return {
            "asin": asin,
            "status": "ERROR",
            "reason": f"ASIN directory not found: {asin_dir}. "
                      f"Run crawl_product_details first.",
        }
    return await asyncio.to_thread(process_listing_asin, asin_dir, download, force)


# ---------------------------------------------------------------------------
# Tool 4: Extract A+ images (single ASIN)
# ---------------------------------------------------------------------------

@mcp.tool()
async def extract_aplus_images(
    asin: str,
    output_dir: str,
    download: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Extract A+ (Enhanced Brand Content) modules & images for a single ASIN.

    Requires {workspace}/products/{ASIN}/product.html to exist. Writes:
      {workspace}/products/{ASIN}/aplus-images/
        ├── urls.json
        ├── aplus_extracted.md   (structured markdown with modules, brand story,
        │                          comparison tables)
        ├── aplus.html           (raw A+ HTML fragment)
        └── images/aplus_img_NNN.png  (if download=True)

    Args:
        asin: Amazon ASIN (e.g. "B0DRNRC5H5")
        output_dir: **Absolute path** to the workspace root directory.
        download: Download images to disk (default: True).
        force: Re-extract even if aplus-images/urls.json already exists.

    Returns:
        Dict with asin, status, has_aplus, module_count, image_count,
        download_status, urls_file path.
    """
    workspace = _resolve_workspace_path(output_dir)
    asin_dir = workspace / "products" / asin.upper()
    if not asin_dir.exists():
        return {
            "asin": asin,
            "status": "ERROR",
            "reason": f"ASIN directory not found: {asin_dir}. "
                      f"Run crawl_product_details first.",
        }
    return await asyncio.to_thread(process_aplus_asin, asin_dir, download, force)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    mcp.run(transport="stdio")
