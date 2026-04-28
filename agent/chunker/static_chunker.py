#!/usr/bin/env python3
"""
Static HTML chunker for Amazon product detail pages.

Reads raw product.html from MCP scraper output and splits it into
4 semantic blocks using stable DOM selectors:

    ppd              → #ppd / #dp-container
    customer_reviews → #customerReviews / #reviewsMedley
    product_details  → #productDetails_feature_div / #detailBullets_feature_div
    aplus            → #aplus / #aplusBrandStory_feature_div

Each block is written as a standalone HTML file with all <script> tags removed.

Input:
    {products_dir}/{ASIN}/product.html

Output (per product):
    {out_dir}/{rank}_{ASIN}/{block}/raw/{block}.html
    {out_dir}/{rank}_{ASIN}/manifest.json
"""

from __future__ import annotations

import json
from pathlib import Path

from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Block selector definitions
# ---------------------------------------------------------------------------

BLOCKS = ("ppd", "customer_reviews", "product_details", "aplus")

BLOCK_SELECTORS: dict[str, list[str]] = {
    "ppd": ["#ppd", "#dp-container"],
    "customer_reviews": ["#customerReviews", "#reviewsMedley"],
    "product_details": ["#productDetails_feature_div", "#detailBullets_feature_div"],
    "aplus": ["#aplus", "#aplusBrandStory_feature_div"],
}


# ---------------------------------------------------------------------------
# Core chunking logic
# ---------------------------------------------------------------------------

def _find_block(soup: BeautifulSoup, block: str) -> Tag | None:
    """Try each selector in priority order; return first match or None."""
    for selector in BLOCK_SELECTORS[block]:
        el = soup.select_one(selector)
        if el is not None:
            return el
    return None


def _clean_block_html(tag: Tag) -> str:
    """Remove all <script> tags, return cleaned HTML string."""
    for script in tag.find_all("script"):
        script.decompose()
    return str(tag)


def chunk_product_html(html_path: Path, product_out_dir: Path) -> dict:
    """Chunk a single product.html into block HTML files.

    Args:
        html_path: Path to the raw product.html file.
        product_out_dir: Output directory for this product
                         (e.g. {out_dir}/001_B0XXXXX/).

    Returns:
        Dict with chunk status per block, suitable for manifest.
    """
    if not html_path.exists():
        return {"status": "SKIPPED", "reason": "product_html_missing"}

    html_content = html_path.read_text(encoding="utf-8", errors="ignore")

    # Reject pages that are not valid product detail pages (e.g. Amazon
    # homepage redirects). Without this check, non-product HTML produces
    # all-NOT_FOUND blocks → audit marks incomplete → orchestrator re-runs
    # chunker → same invalid HTML → infinite loop.
    try:
        from scraper.product_spider import is_valid_product_page
        if not is_valid_product_page(html_content):
            return {"status": "SKIPPED", "reason": "invalid_product_page"}
    except ImportError:
        pass  # scraper not on sys.path during standalone batch_run

    soup = BeautifulSoup(html_content, "lxml")

    blocks_status: dict[str, dict] = {}

    for block in BLOCKS:
        tag = _find_block(soup, block)
        if tag is None:
            blocks_status[block] = {
                "chunk": "NOT_FOUND",
                "selector_used": None,
                "path": "N/A",
            }
            continue

        cleaned_html = _clean_block_html(tag)

        raw_dir = product_out_dir / block / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        out_path = raw_dir / f"{block}.html"
        out_path.write_text(cleaned_html, encoding="utf-8")

        # Record which selector actually matched (useful for debugging)
        matched_selector = None
        for selector in BLOCK_SELECTORS[block]:
            if soup.select_one(selector) is not None:
                matched_selector = selector
                break

        blocks_status[block] = {
            "chunk": "SUCCESS",
            "selector_used": matched_selector,
            "path": str(out_path),
        }

    return {"status": "SUCCESS", "blocks": blocks_status}


def write_product_manifest(product_out_dir: Path, blocks_status: dict) -> Path:
    """Write manifest.json inside the product output directory."""
    manifest: dict = {"product_dir": product_out_dir.name}

    if product_out_dir.joinpath("manifest.json").exists():
        try:
            manifest = json.loads(
                product_out_dir.joinpath("manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            pass

    manifest.setdefault("blocks", {}).update(blocks_status)

    manifest_path = product_out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest_path
