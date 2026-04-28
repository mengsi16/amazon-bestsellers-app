#!/usr/bin/env python3
"""
Listing Image Extractor — 从 Amazon 商品详情页 HTML 中提取 listing 海报图 URL 并下载。

Amazon 商品详情页的 listing 图片存储在以下位置：
1. #imageBlock / #imgTaggingId — 主图区域
2. #altImages — 缩略图列表
3. data-old-hires 属性 — 高清主图
4. JavaScript 变量 colorImages / imageBlockData — 完整图片数据

Usage:
    # 从已爬取的 HTML 目录提取（{root}/{ASIN}/product.html）
    python extract_listing_images.py --root-dir ./products

    # 对单个 ASIN 目录处理
    python extract_listing_images.py --asin-dir ./products/B0XXXXX

    # 只提取 URL 不下载
    python extract_listing_images.py --root-dir ./products --no-download
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from downloader import download_image, url_to_filename


# ---------------------------------------------------------------------------
# Image URL extraction from HTML
# ---------------------------------------------------------------------------

# Amazon image URL patterns
AMAZON_IMG_RE = re.compile(
    r"https?://m\.media-amazon\.com/images/[^\s\"')\]]+",
    re.IGNORECASE,
)
HIRES_DATA_ATTR = "data-old-hires"
COLOR_IMAGES_RE = re.compile(
    r"'colorImages':\s*\{[^}]*?'initial':\s*(\[.*?\])\s*[,}]",
    re.DOTALL,
)
# Matches each variant entry's hiRes / large URL inside colorImages.initial array
COLOR_HIRES_RE = re.compile(r'"hiRes"\s*:\s*"(https?://[^"]+)"')
COLOR_LARGE_RE = re.compile(r'"large"\s*:\s*"(https?://[^"]+)"')

# Filter out obvious non-listing images (tiny thumbnails, placeholders, video overlays)
# Amazon size modifier legend: SRxx,yy = thumbnail, SLxxxx = large, SYxxx = medium
NON_LISTING_MARKERS = re.compile(
    r"[._]SR\d+,\d+_"                   # tiny thumbnails like _SR38,50_ or .SR38,50_
    r"|[._]US\d+_"                      # small thumbnails like _US40_
    r"|[._]SS\d+_"                      # small square thumbnails
    r"|[._]SY(?:[1-9]\d?|1\d{2})[_.]"   # tiny SY size < 200 (SY46, SY150, ...)
    r"|[._]SX(?:[1-9]\d?|1\d{2})[_.]"   # tiny SX size < 200
    r"|_CR,0,0,\d{1,3},\d{1,3}_"        # cropped small thumbnails
    r"|PKmb-play-button"                # video play-button overlay thumbnails
    r"|PImini-player"                   # video mini-player thumbnail
    r"|mini-player-shuttle"             # video shuttle thumbnail
    r"|grey-pixel\.gif"                 # placeholder
    r"|transparent-pixel"               # placeholder
    r"|/sprites/",                       # sprite sheets
    re.IGNORECASE,
)


def _is_listing_image(url: str) -> bool:
    """过滤掉缩略图、占位符、视频按钮等非海报图。"""
    if not url or "amazon" not in url.lower():
        return False
    if NON_LISTING_MARKERS.search(url):
        return False
    return True


# Amazon image IDs are the 10-11 char alnum code in the URL path, e.g. /I/61dGsnLrk4L.jpg
AMAZON_IMG_ID_RE = re.compile(r"/I/([A-Za-z0-9+\-]{10,12})[._]")


def _image_id(url: str) -> str | None:
    """提取 Amazon 图片的唯一 ID（用于跨分辨率去重）。"""
    m = AMAZON_IMG_ID_RE.search(url)
    return m.group(1) if m else None


def _extract_from_alt_images(soup: BeautifulSoup) -> list[str]:
    """从 #altImages 提取缩略图/主图 URL。"""
    urls: list[str] = []
    seen: set[str] = set()

    alt_images = soup.select_one("#altImages")
    if not alt_images:
        return urls

    for img in alt_images.find_all("img"):
        src = img.get("src", "")
        if not src or "amazon.com/images" not in src:
            continue

        # Convert thumbnail URL to full-size URL
        full_url = _to_full_size_url(src)
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)

    return urls


def _extract_from_main_image_container(soup: BeautifulSoup) -> list[str]:
    """从 #main-image-container 提取主图 URL（Amazon 现代模板的主图画廊）。

    通用选择器：``#main-image-container li.image.item img``
    覆盖 desktop-media-mainView 下所有主图 item（itemNo1..itemNoN，每个 li 对应
    一张主图/variant）。不依赖特定 itemNo / variant-PT0X 编号，因此具备跨商品通用性。

    优先取 data-old-hires（高清），其次取 src。
    """
    urls: list[str] = []
    seen: set[str] = set()

    # Modern desktop main image gallery (covers itemNo0, itemNo1, ...).
    # We only harvest ``data-old-hires`` here because ``src`` on these <li>
    # items is typically the small navigation thumbnail (SR38,50), not the
    # full listing image. The hi-res URL for variant switches lives in the
    # ``colorImages`` JS variable handled by ``_extract_from_color_images_js``.
    items = soup.select("#main-image-container li.image.item img")
    for img in items:
        hires = img.get(HIRES_DATA_ATTR, "")
        if hires and "amazon.com/images" in hires and hires not in seen:
            seen.add(hires)
            urls.append(hires)

    return urls


def _extract_from_image_block(soup: BeautifulSoup) -> list[str]:
    """从 #imageBlock / #imgTaggingId 提取主图 URL（传统模板 fallback）。"""
    urls: list[str] = []
    seen: set[str] = set()

    image_block = soup.select_one("#imageBlock")
    if not image_block:
        image_block = soup.select_one("#imgTaggingId")
    if not image_block:
        return urls

    for img in image_block.find_all("img", attrs={HIRES_DATA_ATTR: True}):
        hires = img.get(HIRES_DATA_ATTR, "")
        if hires and "amazon.com/images" in hires and hires not in seen:
            seen.add(hires)
            urls.append(hires)

    for img in image_block.find_all("img"):
        src = img.get("src", "")
        if not src or "amazon.com/images" not in src:
            continue
        full_url = _to_full_size_url(src)
        if full_url not in seen:
            seen.add(full_url)
            urls.append(full_url)

    # Also pick up #landingImage (main product image)
    landing = soup.select_one("#landingImage")
    if landing:
        hires = landing.get(HIRES_DATA_ATTR, "")
        if hires and "amazon.com/images" in hires and hires not in seen:
            seen.add(hires)
            urls.append(hires)
        src = landing.get("src", "")
        if src and "amazon.com/images" in src:
            full_url = _to_full_size_url(src)
            if full_url not in seen:
                seen.add(full_url)
                urls.append(full_url)

    return urls


def _extract_from_color_images_js(html: str) -> list[str]:
    """从 JS 变量 ``colorImages.initial`` 提取所有 variant 的 hiRes URL。

    这是 Amazon 商品页所有 variant / 多角度图的权威来源。
    每个 entry 结构：``{"hiRes": "...1500_.jpg", "thumb": "...38,50_.jpg", "large": "..."}``

    策略：在包含 ``colorImages`` 的 script 文本范围内扫描所有
    ``"hiRes":"..."`` 出现——这样即使 entry 内嵌套有 ``[...]`` 也不会被截断。
    若 hiRes 为空再 fallback 到同一 entry 的 large URL。
    """
    urls: list[str] = []
    seen: set[str] = set()

    # Locate the script that defines colorImages. The value block is large so we
    # don't try to carve its exact array boundaries; we grep downstream.
    cs_pos = html.find("'colorImages'")
    if cs_pos < 0:
        cs_pos = html.find('"colorImages"')
    if cs_pos < 0:
        return urls

    # Search within a window starting from colorImages declaration.
    # Next top-level JS variable after colorImages is usually 'colorToAsin' /
    # 'imageGalleryData' — cap window size at 250KB to stay safe.
    window = html[cs_pos : cs_pos + 250_000]

    # Heuristic: end the window at the first 'colorToAsin' or 'heroImage' or
    # 'imageGalleryData' marker that comes after colorImages — whichever is first.
    for end_marker in ("'colorToAsin'", '"colorToAsin"', "'heroImage'",
                       '"heroImage"', "'imageGalleryData'", '"imageGalleryData"'):
        idx = window.find(end_marker, 20)  # skip the opening 'colorImages' itself
        if 0 < idx < len(window):
            window = window[:idx]
            break

    for hires_m in COLOR_HIRES_RE.finditer(window):
        url = hires_m.group(1)
        if url.lower() in ("null", "none", ""):
            continue
        if _is_listing_image(url) and url not in seen:
            seen.add(url)
            urls.append(url)

    # If no hiRes found at all, fallback to large URLs from same window
    if not urls:
        for large_m in COLOR_LARGE_RE.finditer(window):
            url = large_m.group(1)
            if _is_listing_image(url) and url not in seen:
                seen.add(url)
                urls.append(url)

    return urls


def _to_full_size_url(url: str) -> str:
    """将 Amazon 缩略图 URL 转换为全尺寸 URL。

    Amazon 图片 URL 格式：
    https://m.media-amazon.com/images/I/XXXXXX._AC_US40_.jpg
    全尺寸：https://m.media-amazon.com/images/I/XXXXXX.jpg
    """
    # Remove size modifiers like _AC_US40_, _SY300_, etc.
    clean = re.sub(r"\._[A-Z]+[\d,]*_", ".", url)
    # Remove duplicate extensions
    clean = re.sub(r"\.(\.\w+)$", r"\1", clean)
    return clean


def extract_listing_images(html: str) -> list[str]:
    """从商品 HTML 中提取 listing 海报图 URL（去重、有序）。

    提取优先级：
    1. ``colorImages.initial[*].hiRes`` (JS) — 所有 variant 的权威高清来源
    2. ``#main-image-container li.image.item`` 的 ``data-old-hires`` — 现代模板主图
    3. ``#imageBlock`` / ``#landingImage`` — 传统模板 fallback
    4. ``#altImages`` 缩略图 → 转全尺寸（兜底）

    所有 URL 统一过缩略图/占位符过滤器 (:func:`_is_listing_image`)。
    """
    soup = BeautifulSoup(html, "lxml")
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    ordered_urls: list[str] = []

    def _merge(urls: list[str]) -> None:
        for url in urls:
            if url in seen_urls or not _is_listing_image(url):
                continue
            img_id = _image_id(url)
            if img_id and img_id in seen_ids:
                # Same image in different resolution — skip (keep the first/best).
                continue
            seen_urls.add(url)
            if img_id:
                seen_ids.add(img_id)
            ordered_urls.append(url)

    # Priority 1: colorImages JS variable (best — structured hiRes per variant)
    _merge(_extract_from_color_images_js(html))

    # Priority 2: main-image-container main image (data-old-hires of itemNo0)
    _merge(_extract_from_main_image_container(soup))

    # Priority 3: imageBlock / landingImage (legacy template)
    _merge(_extract_from_image_block(soup))

    # Priority 4: altImages thumbnails (last resort fallback)
    if not ordered_urls:
        _merge(_extract_from_alt_images(soup))

    return ordered_urls


# ---------------------------------------------------------------------------
# ASIN-scoped processing
# ---------------------------------------------------------------------------

LISTING_SUBDIR = "listing-images"
LISTING_URLS_FILE = "urls.json"
LISTING_IMG_SUBDIR = "images"
LISTING_IMG_PREFIX = "listing_img"


def is_listing_done(asin_dir: Path, require_download: bool = True) -> bool:
    """Check if this ASIN's listing extraction + download is complete."""
    urls_path = asin_dir / LISTING_SUBDIR / LISTING_URLS_FILE
    if not urls_path.exists():
        return False
    try:
        urls_data = json.loads(urls_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    urls = urls_data.get("urls", [])
    if not urls:
        # No listing images at all — treat as done (nothing to download).
        return True
    if not require_download:
        return True
    images_dir = asin_dir / LISTING_SUBDIR / LISTING_IMG_SUBDIR
    if not images_dir.is_dir():
        return False
    # Each entry in urls_data["images"] should have a local file present.
    for entry in urls_data.get("images", []):
        local = entry.get("local_path")
        if not local or not Path(local).exists():
            return False
    return True


def process_asin(
    asin_dir: Path,
    download: bool = True,
    force: bool = False,
) -> dict[str, Any]:
    """Extract listing images for a single ASIN directory.

    Expects ``{asin_dir}/product.html`` to exist. Writes:
      - ``{asin_dir}/listing-images/urls.json``
      - ``{asin_dir}/listing-images/images/listing_img_NNN.jpg`` (if download)
    """
    asin = asin_dir.name
    html_path = asin_dir / "product.html"
    listing_dir = asin_dir / LISTING_SUBDIR
    urls_path = listing_dir / LISTING_URLS_FILE
    images_dir = listing_dir / LISTING_IMG_SUBDIR

    if not html_path.exists():
        return {"asin": asin, "status": "SKIPPED",
                "reason": "product.html missing"}

    if not force and is_listing_done(asin_dir, require_download=download):
        return {"asin": asin, "status": "ALREADY_DONE",
                "urls_file": str(urls_path)}

    listing_dir.mkdir(parents=True, exist_ok=True)

    html = html_path.read_text(encoding="utf-8", errors="replace")
    image_urls = extract_listing_images(html)

    # Build urls.json skeleton
    urls_data: dict[str, Any] = {
        "asin": asin,
        "source_html": str(html_path),
        "image_count": len(image_urls),
        "urls": image_urls,
    }

    if download and image_urls:
        images_dir.mkdir(parents=True, exist_ok=True)
        images_info: list[dict] = []
        success_count = 0
        for idx, url in enumerate(image_urls, start=1):
            filename = url_to_filename(url, idx, prefix=LISTING_IMG_PREFIX)
            save_path = images_dir / filename
            if save_path.exists() and save_path.stat().st_size > 0:
                # Already downloaded in a prior run — keep.
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
            "SUCCESS" if success_count == len(image_urls)
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
        "image_count": len(image_urls),
        "download_status": urls_data.get("download_status"),
        "urls_file": str(urls_path),
    }


def process_root(
    root_dir: Path,
    download: bool = True,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Iterate every ``{root}/{ASIN}/product.html`` and extract listing images."""
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
            print(f"[Skip] {asin_dir.name} — listing already extracted")
        elif status == "SKIPPED":
            print(f"[Skip] {asin_dir.name} — {res.get('reason')}")
        else:
            print(f"[OK]   {asin_dir.name} — {res.get('image_count', 0)} image(s)")
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract listing poster images from Amazon product HTML (ASIN-scoped)",
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--root-dir", type=Path,
        help="Output root containing per-ASIN subdirectories "
             "(each with product.html). Writes listing-images/ inside each.",
    )
    input_group.add_argument(
        "--asin-dir", type=Path,
        help="Single ASIN directory containing product.html. "
             "Writes listing-images/ inside it.",
    )
    parser.add_argument(
        "--no-download", action="store_true",
        help="Only extract URLs, don't download images",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-extract even if listing-images/urls.json already exists",
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
