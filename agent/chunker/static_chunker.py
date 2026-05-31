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
# Product page validation constants (duplicated from scraper.product_spider
# to avoid import failure causing silent skip — ref CLAUDE.md rule 17)
# ---------------------------------------------------------------------------

_PRODUCT_PAGE_MARKERS = (
    "producttitle",
    "product-title",
    "data-asin",
    "buybox",
    "dp-container",
    "centerCol",
    "ppd",
    "#aplus_feature_div",
    "productDescription",
)

_NON_PRODUCT_PAGE_MARKERS = (
    "spend less. smile more.",
    "amazon.com. spend less",
)

_SERVICE_ERROR_MARKERS = (
    "503 - service unavailable error",
    "service unavailable error",
    "sorry! something went wrong",
)


def _is_service_error_page(html_content: str) -> bool:
    content = html_content.lower()
    return any(marker in content for marker in _SERVICE_ERROR_MARKERS)


def _is_probably_block_page(html_content: str) -> bool:
    content = html_content.lower()
    block_indicators = ("captcha", "robot check", "you are shopping from", "please show us")
    return any(indicator in content for indicator in block_indicators)


def _is_valid_product_page(html_content: str) -> bool:
    """Returns True if html_content looks like an Amazon product detail page.

    Amazon 首页重定向会被精确拦截，避免 chunker 产出全 NOT_FOUND 后 audit 死循环。
    """
    if _is_probably_block_page(html_content) or _is_service_error_page(html_content):
        return False
    content = html_content.lower()
    if any(marker in content for marker in _NON_PRODUCT_PAGE_MARKERS):
        return False
    return any(marker in content for marker in _PRODUCT_PAGE_MARKERS)

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
    """Try each selector in priority order; return first match or None.

    对 aplus 块不使用此函数——aplus 可能存在多个容器（Brand Story + Premium A+），
    需要使用 _find_aplus_containers 通过 select 收集所有匹配容器。
    """
    for selector in BLOCK_SELECTORS[block]:
        el = soup.select_one(selector)
        if el is not None:
            return el
    return None


def _find_aplus_containers(soup: BeautifulSoup) -> list[Tag]:
    """定位所有 A+ 内容容器（Brand Story + Premium A+ 可能分属不同 div）。

    使用 select（而非 select_one）收集所有匹配容器，确保：
    - 所有 #aplus、#aplusBrandStory_feature_div 等容器都被收集
    - 按 Tag 对象身份去重，保留 Amazon 页面中重复 id 的不同真实容器
    """
    containers: list[Tag] = []
    seen_tags: set[int] = set()
    for selector in BLOCK_SELECTORS["aplus"]:
        for el in soup.select(selector):
            tag_identity = id(el)
            if tag_identity in seen_tags:
                continue
            seen_tags.add(tag_identity)
            containers.append(el)
    return containers


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
    # chunker → same invalid HTML → infinite loop (ref CLAUDE.md rule 17).
    if not _is_valid_product_page(html_content):
        return {"status": "SKIPPED", "reason": "invalid_product_page"}

    soup = BeautifulSoup(html_content, "lxml")

    blocks_status: dict[str, dict] = {}

    for block in BLOCKS:
        # A+ 特殊处理：收集所有容器后合并 HTML（Brand Story + Premium A+ 可能分属不同 div）
        if block == "aplus":
            containers = _find_aplus_containers(soup)
            if not containers:
                blocks_status[block] = {
                    "chunk": "NOT_FOUND",
                    "selector_used": None,
                    "path": "N/A",
                }
                continue
            # 合并所有容器的 HTML（去重后）
            seen_ids: set[str] = set()
            combined_parts: list[str] = []
            for el in containers:
                el_id = el.get("id", "") or str(id(el))
                if el_id not in seen_ids:
                    seen_ids.add(el_id)
                    cleaned_parts = _clean_block_html(el)
                    combined_parts.append(cleaned_parts)
            cleaned_html = "\n".join(combined_parts)
            matched_selector = "|".join(BLOCK_SELECTORS["aplus"])
        else:
            tag = _find_block(soup, block)
            if tag is None:
                blocks_status[block] = {
                    "chunk": "NOT_FOUND",
                    "selector_used": None,
                    "path": "N/A",
                }
                continue
            cleaned_html = _clean_block_html(tag)
            matched_selector = None
            for selector in BLOCK_SELECTORS[block]:
                if soup.select_one(selector) is not None:
                    matched_selector = selector
                    break

        raw_dir = product_out_dir / block / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        out_path = raw_dir / f"{block}.html"
        out_path.write_text(cleaned_html, encoding="utf-8")

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
