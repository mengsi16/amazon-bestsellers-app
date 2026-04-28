#!/usr/bin/env python3
"""
A+ Poster Content Scraper — 从 Amazon 商品详情页 HTML 中提取 A+ 海报页面内容并下载图片。

Amazon A+ Content（Enhanced Brand Content）是商品详情页下方的品牌内容区域，
包含对比表、横幅图、图文并排、品牌故事等模块。

A+ 内容在 HTML 中的位置：
- #aplusBrandFeatureDiv / #aplus — A+ 内容容器
- .aplus-module — 各个 A+ 模块
- module-X class — 模块类型标识

Usage:
    # 从已爬取的 HTML 目录提取（{root}/{ASIN}/product.html）
    python extract_aplus.py --root-dir ./products

    # 对单个 ASIN 目录处理
    python extract_aplus.py --asin-dir ./products/B0XXXXX

    # 只提取内容不下载图片
    python extract_aplus.py --root-dir ./products --no-download
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from downloader import download_image, url_to_filename


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AplusModule:
    module_type: str
    module_index: int
    text_content: str
    images: list[dict[str, str]] = field(default_factory=list)
    comparison_table: dict[str, Any] | None = None


@dataclass
class AplusContent:
    has_aplus: bool
    modules: list[AplusModule] = field(default_factory=list)
    all_image_urls: list[str] = field(default_factory=list)
    brand_story: str = ""


# ---------------------------------------------------------------------------
# A+ Content extraction
# ---------------------------------------------------------------------------

# Structural classes that are NOT module type identifiers.
_STRUCTURAL_CLASSES = frozenset({
    "celwidget", "aplus-module", "aplus-standard", "aplus-brand-story-hero",
    "aplus-brand-story-card", "aplus-3p-fixed-width",
})

# Match any class that looks like a module type identifier:
#   module-5-comparison-table-scroller
#   premium-module-8-hero-video
#   brand-story-hero-1-image-logo
#   3p-module-b / 3p-module-c
#   np-module-x  (any future prefix)
MODULE_CLASS_RE = re.compile(
    r"^(?:premium-)?module-(\d+)(?:-([a-z0-9-]+))?$"
    r"|^(brand-story)-([a-z0-9-]+)$"
    r"|^([a-z0-9]+)-module-([a-z0-9-]+)$",
    re.IGNORECASE,
)
AMAZON_IMG_URL_RE = re.compile(r"https?://m\.media-amazon\.com/images/[^\s\"')\]]+", re.IGNORECASE)
APLUS_SELECTORS = [
    "#aplusBrandFeatureDiv",
    "#aplus",
    "#aplusBrandContentDiv",
    ".aplus-v2",
    "#dpx-aplus-brand-feature_div",
    "#aplus_feature_div",
]


def _find_aplus_containers(soup: BeautifulSoup) -> list[Tag]:
    """定位所有 A+ 内容容器（Brand Story + Premium A+ 可能分属不同 div）。"""
    containers: list[Tag] = []
    seen_ids: set[str] = set()
    for selector in APLUS_SELECTORS:
        for el in soup.select(selector):
            # Deduplicate by id or object identity
            el_id = el.get("id", "") or str(id(el))
            if el_id in seen_ids:
                continue
            seen_ids.add(el_id)
            containers.append(el)
    return containers


def _extract_module_type(element: Tag) -> str:
    """从元素 class 中提取模块类型（兼容 legacy / premium / brand-story / Xp-module）。

    返回示例：
    - ``module-5-comparison-table-scroller`` (premium)
    - ``module-8-hero-video`` (premium)
    - ``brand-story-hero-1-image-logo``
    - ``3p-module-b`` (third-party)
    - ``np-module-x`` (any future prefix)
    - ``module-2`` (legacy)
    - ``module-unknown`` (未匹配)
    """
    classes = element.get("class", [])
    if isinstance(classes, str):
        classes = classes.split()
    for cls in classes:
        match = MODULE_CLASS_RE.match(cls)
        if not match:
            continue
        number, descriptor, brand_prefix, brand_descriptor, xp_prefix, xp_descriptor = match.groups()
        if number:
            return f"module-{number}" + (f"-{descriptor}" if descriptor else "")
        if brand_prefix:
            return f"{brand_prefix}-{brand_descriptor}"
        if xp_prefix:
            return f"{xp_prefix}-module-{xp_descriptor}"
    return "module-unknown"


def _normalize_text(value: str) -> str:
    """清洗文本：去除多余空白。"""
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _extract_text(node: Tag | None) -> str:
    """从 Tag 中提取清洗后的文本。"""
    if node is None:
        return ""
    for br in node.find_all("br"):
        br.replace_with("\n")
    return _normalize_text(node.get_text(" ", strip=True))


def _extract_images_from_module(module_el: Tag) -> list[dict[str, str]]:
    """从模块中提取图片 URL 和 alt 文本。"""
    images: list[dict[str, str]] = []
    seen: set[str] = set()

    for img in module_el.find_all("img"):
        # Prioritize data-src (lazy-loaded), then src
        src = img.get("data-src") or img.get("src", "")
        if not src or "amazon.com/images" not in src:
            continue
        # Clean URL - remove size modifiers (only legacy ._AC_ format)
        src = re.sub(r"\._[A-Z]+[\d,]*_", ".", src)          # ._AC_SX300_
        src = re.sub(r"\.([.]\w+)$", r"\1", src)

        if src in seen:
            continue
        seen.add(src)

        alt = img.get("alt", "").strip()
        images.append({"alt": alt, "src": src})

    return images


def _extract_comparison_table(module_el: Tag) -> dict[str, Any] | None:
    """从模块中提取对比表数据。"""
    table = module_el.find("table")
    if not table:
        # Some comparison tables use div-based grids
        grid = module_el.select_one(".apm-tablemodule, .apm-fourthcol")
        if not grid:
            return None
        return _extract_div_comparison(grid)

    headers: list[str] = []
    rows: list[list[str]] = []

    # Extract headers
    thead = table.find("thead")
    if thead:
        for th in thead.find_all(["th", "td"]):
            headers.append(_extract_text(th))

    # Extract rows
    tbody = table.find("tbody") or table
    for tr in tbody.find_all("tr"):
        cells = [_extract_text(td) for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)

    if not rows:
        return None

    return {
        "headers": headers,
        "rows": rows,
    }


def _extract_div_comparison(element: Tag) -> dict[str, Any]:
    """从 div-based 对比网格中提取数据。"""
    headers: list[str] = []
    rows: list[list[str]] = []

    # Try to find column headers
    header_cells = element.select(".apm-tablemodule-keyvalue, .apm-tablemodule-left")
    for cell in header_cells:
        text = _extract_text(cell)
        if text:
            headers.append(text)

    return {
        "headers": headers,
        "rows": rows,
        "note": "div-based comparison grid, structure may vary",
    }


def _extract_brand_story(container: Tag) -> str:
    """提取 Brand Story 文案。"""
    # Look for brand story specific elements
    for selector in [
        ".aplus-brand-story",
        "[data-a-expander-name='aplus-brand-story']",
        ".apm-brand-story",
    ]:
        el = container.select_one(selector)
        if el:
            return _extract_text(el)

    # Fallback: look for module-2 (pure text module often used for brand story)
    for module_el in container.select(".aplus-module"):
        module_type = _extract_module_type(module_el)
        if module_type == "module-2":
            text = _extract_text(module_el)
            if len(text) > 100:
                return text

    return ""


def extract_aplus_content(html: str) -> AplusContent:
    """从商品 HTML 中提取 A+ 海报页面内容。

    Returns:
        AplusContent 包含模块列表、图片 URL、品牌故事等。
    """
    soup = BeautifulSoup(html, "lxml")
    containers = _find_aplus_containers(soup)

    if not containers:
        return AplusContent(has_aplus=False)

    modules: list[AplusModule] = []
    all_image_urls: list[str] = []
    seen_urls: set[str] = set()

    # Collect A+ modules from ALL containers (Brand Story + Premium A+)
    module_elements: list[Tag] = []
    for container in containers:
        found = container.select(".aplus-module")
        if not found:
            found = container.select("[class*='module-']")
        module_elements.extend(found)

    for idx, module_el in enumerate(module_elements):
        module_type = _extract_module_type(module_el)
        text_content = _extract_text(module_el)
        images = _extract_images_from_module(module_el)

        # Skip empty modules
        if not text_content and not images:
            continue

        # Extract comparison table if present.
        # Covers legacy "module-5" / "module-7" and modern
        # "module-5-comparison-table-scroller" etc.
        comparison_table = None
        if module_type.startswith(("module-5", "module-7")):
            comparison_table = _extract_comparison_table(module_el)

        module = AplusModule(
            module_type=module_type,
            module_index=idx + 1,
            text_content=text_content,
            images=images,
            comparison_table=comparison_table,
        )
        modules.append(module)

        # Collect all image URLs
        for img in images:
            if img["src"] not in seen_urls:
                seen_urls.add(img["src"])
                all_image_urls.append(img["src"])

    # Extract brand story (from first container — brand story section)
    brand_story = _extract_brand_story(containers[0]) if containers else ""

    return AplusContent(
        has_aplus=True,
        modules=modules,
        all_image_urls=all_image_urls,
        brand_story=brand_story,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_aplus_markdown(content: AplusContent) -> str:
    """将 A+ 内容渲染为 Markdown。"""
    if not content.has_aplus:
        return "# A+ Content\n\nNo A+ content found on this page.\n"

    lines = ["# A+ Content Extracted", ""]

    # Module summary
    lines.append("## Module Summary")
    lines.append("")
    lines.append("| # | Type | Text Length | Images | Has Comparison |")
    lines.append("| --- | --- | --- | --- | --- |")
    for m in content.modules:
        has_comp = "Yes" if m.comparison_table else "No"
        lines.append(
            f"| {m.module_index} | {m.module_type} | {len(m.text_content)} | {len(m.images)} | {has_comp} |"
        )
    lines.append("")

    # Detailed modules
    for m in content.modules:
        lines.append(f"## Module {m.module_index}: {m.module_type}")
        lines.append("")

        if m.text_content:
            # Truncate very long text for readability
            text = m.text_content
            if len(text) > 500:
                text = text[:500] + "..."
            lines.append(f"**Text**: {text}")
            lines.append("")

        if m.images:
            lines.append("**Images**:")
            lines.append("")
            lines.append("| Alt | Src |")
            lines.append("| --- | --- |")
            for img in m.images:
                alt = img["alt"].replace("|", "\\|") if img["alt"] else "N/A"
                src = img["src"].replace("|", "\\|")
                lines.append(f"| {alt} | {src} |")
            lines.append("")

        if m.comparison_table:
            lines.append("**Comparison Table**:")
            lines.append("")
            table = m.comparison_table
            if table.get("headers"):
                header = " | ".join(table["headers"])
                sep = " | ".join(["---"] * len(table["headers"]))
                lines.append(f"| {header} |")
                lines.append(f"| {sep} |")
                for row in table.get("rows", []):
                    cells = " | ".join(row)
                    lines.append(f"| {cells} |")
            lines.append("")

    # Brand Story
    if content.brand_story:
        lines.append("## Brand Story")
        lines.append("")
        lines.append(content.brand_story)
        lines.append("")

    # All image URLs
    if content.all_image_urls:
        lines.append("## All Image URLs")
        lines.append("")
        for url in content.all_image_urls:
            lines.append(f"- {url}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ASIN-scoped processing
# ---------------------------------------------------------------------------

APLUS_SUBDIR = "aplus-images"
APLUS_URLS_FILE = "urls.json"
APLUS_MD_FILE = "aplus_extracted.md"
APLUS_HTML_FILE = "aplus.html"
APLUS_IMG_SUBDIR = "images"
APLUS_IMG_PREFIX = "aplus_img"


def is_aplus_done(asin_dir: Path, require_download: bool = True) -> bool:
    """Check if this ASIN's A+ extraction + download is complete."""
    urls_path = asin_dir / APLUS_SUBDIR / APLUS_URLS_FILE
    if not urls_path.exists():
        return False
    try:
        data = json.loads(urls_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not data.get("has_aplus", False):
        # No A+ content exists on the page — nothing to re-do.
        return True
    urls = data.get("image_urls", [])
    if not urls:
        return True
    if not require_download:
        return True
    for entry in data.get("images", []):
        local = entry.get("local_path")
        if not local or not Path(local).exists():
            return False
    return True


def process_asin(
    asin_dir: Path,
    download: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Extract A+ content for a single ASIN directory.

    Expects ``{asin_dir}/product.html`` to exist. Writes:
      - ``{asin_dir}/aplus-images/urls.json`` (structured metadata)
      - ``{asin_dir}/aplus-images/aplus_extracted.md`` (rendered markdown)
      - ``{asin_dir}/aplus-images/aplus.html`` (raw A+ section, if present)
      - ``{asin_dir}/aplus-images/images/aplus_img_NNN.png`` (if download)
    """
    asin = asin_dir.name
    html_path = asin_dir / "product.html"
    aplus_dir = asin_dir / APLUS_SUBDIR
    urls_path = aplus_dir / APLUS_URLS_FILE
    md_path = aplus_dir / APLUS_MD_FILE
    aplus_html_path = aplus_dir / APLUS_HTML_FILE
    images_dir = aplus_dir / APLUS_IMG_SUBDIR

    if not html_path.exists():
        return {"asin": asin, "status": "SKIPPED",
                "reason": "product.html missing"}

    if not force and is_aplus_done(asin_dir, require_download=download):
        return {"asin": asin, "status": "ALREADY_DONE",
                "urls_file": str(urls_path)}

    aplus_dir.mkdir(parents=True, exist_ok=True)

    html = html_path.read_text(encoding="utf-8", errors="replace")
    content = extract_aplus_content(html)

    urls_data: dict[str, Any] = {
        "asin": asin,
        "source_html": str(html_path),
        "has_aplus": content.has_aplus,
        "module_count": len(content.modules),
        "image_count": len(content.all_image_urls),
        "modules": [
            {
                "type": m.module_type,
                "index": m.module_index,
                "text_length": len(m.text_content),
                "image_count": len(m.images),
                "has_comparison": m.comparison_table is not None,
            }
            for m in content.modules
        ],
        "image_urls": content.all_image_urls,
    }

    # Always write the markdown rendering (cheap, useful for inspection).
    md_path.write_text(render_aplus_markdown(content), encoding="utf-8")
    urls_data["markdown_file"] = str(md_path)

    # Save the raw A+ section HTML if present.
    soup = BeautifulSoup(html, "lxml")
    aplus_containers = _find_aplus_containers(soup)
    if aplus_containers:
        combined_html = "\n".join(str(c) for c in aplus_containers)
        aplus_html_path.write_text(combined_html, encoding="utf-8")
        urls_data["aplus_html_file"] = str(aplus_html_path)

    if download and content.all_image_urls:
        images_dir.mkdir(parents=True, exist_ok=True)
        images_info: list[dict] = []
        success_count = 0
        for idx, url in enumerate(content.all_image_urls, start=1):
            filename = url_to_filename(url, idx, prefix=APLUS_IMG_PREFIX)
            save_path = images_dir / filename
            if save_path.exists() and save_path.stat().st_size > 0:
                status = "OK"
                success_count += 1
            else:
                ok = download_image(url, save_path)
                status = "OK" if ok else "FAILED"
                if ok:
                    success_count += 1
            images_info.append({
                "index": idx,
                "url": url,
                "filename": filename,
                "local_path": str(save_path),
                "status": status,
            })
        urls_data["images"] = images_info
        urls_data["download_success_count"] = success_count
        urls_data["download_status"] = (
            "SUCCESS" if success_count == len(content.all_image_urls)
            else "PARTIAL" if success_count > 0
            else "ALL_FAILED"
        )

    urls_path.write_text(
        json.dumps(urls_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "asin": asin,
        "status": "OK",
        "has_aplus": content.has_aplus,
        "module_count": len(content.modules),
        "image_count": len(content.all_image_urls),
        "download_status": urls_data.get("download_status"),
        "urls_file": str(urls_path),
    }


def process_root(
    root_dir: Path,
    download: bool = True,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Iterate ``{root}/{ASIN}/product.html`` and extract A+ content."""
    asin_dirs = sorted([
        p for p in root_dir.iterdir()
        if p.is_dir() and (p / "product.html").exists()
    ])
    if not asin_dirs:
        print(f"No ASIN dirs with product.html found under {root_dir}",
              file=sys.stderr)
        return []

    results: list[dict[str, Any]] = []
    for asin_dir in asin_dirs:
        res = process_asin(asin_dir, download=download, force=force)
        status = res.get("status")
        if status == "ALREADY_DONE":
            print(f"[Skip] {asin_dir.name} — A+ already extracted")
        elif status == "SKIPPED":
            print(f"[Skip] {asin_dir.name} — {res.get('reason')}")
        else:
            aplus = "Yes" if res.get("has_aplus") else "No"
            print(
                f"[OK]   {asin_dir.name} — A+:{aplus}, "
                f"modules:{res.get('module_count', 0)}, "
                f"images:{res.get('image_count', 0)}"
            )
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract A+ poster content from Amazon product HTML (ASIN-scoped)",
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--root-dir", type=Path,
        help="Output root containing per-ASIN subdirectories "
             "(each with product.html). Writes aplus-images/ inside each.",
    )
    input_group.add_argument(
        "--asin-dir", type=Path,
        help="Single ASIN directory containing product.html. "
             "Writes aplus-images/ inside it.",
    )
    parser.add_argument(
        "--no-download", action="store_true",
        help="Only extract content, don't download images",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract even if aplus-images/urls.json already exists",
    )
    args = parser.parse_args()

    download = not args.no_download

    if args.asin_dir:
        if not args.asin_dir.exists():
            print(f"Error: {args.asin_dir} not found", file=sys.stderr)
            sys.exit(1)
        res = process_asin(args.asin_dir, download=download, force=args.force)
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        if not args.root_dir.exists():
            print(f"Error: {args.root_dir} not found", file=sys.stderr)
            sys.exit(1)
        process_root(args.root_dir, download=download, force=args.force)


if __name__ == "__main__":
    main()
